"""AE1: no password, application password or bearer token survives in a tool result or a log line.

Fixtures carry each secret form; the end-to-end cases push them through a real handler (the one
place results are serialized, ``_guarded``) and the write log, then search the serialized answer and
the log file for every form. ``test_the_leak_check_catches_an_unscrubbed_result`` switches the text
scrubber off and expects the same search to find the leaks, so the search is proven to see them.
"""
from __future__ import annotations

import base64
import json

import pytest

from wpb_fakes import FakeWP, Settings, create_paired, load_plugin, mod, secrets

APP_PW = "Ab3d eF6h iJ9k lM2n oP5q rS8t"          # the site's own key (WPBRUECKE_KEY1)
OTHER_PW = "Zq7w Xe4r Ty1u Ip0o As2d Fg5h"         # another site's application password: shape only
OTHER_PW_UNSPACED = "Hj3kLm9nPq2rSt5vWx8yZa1b"
BASIC = base64.b64encode(f"hermes:{APP_PW}".encode()).decode()
# Token fixtures are joined at run time so secret scanners (gitleaks in the release gate) see no literal.
JWT = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"])
OPAQUE_BEARER = "q8Fz2LmXw9Rt4Yp1Kc7Vb3Nd"
GOOGLE_TOKEN = "ya" + "29." + "a0AfH6SMBx1234567890abcdefGHIJKL"
API_KEY = "AI" + "za" + "SyD3fG7hJ2kL9mN4pQ8rS1tU6vW0xY5zA2b"
JSON_PASSWORD = "hunter2-secret"
USERINFO_PASSWORD = "s3cr3tPass"
PREVIEW_TOKEN = "0f" * 32
PREVIEW_URL = f"https://test.example/?page_id=12&wpb_preview={PREVIEW_TOKEN}&wpb_exp=1790000000"

LEAKY = (f"site key {APP_PW} / {APP_PW.replace(' ', '')}; other {OTHER_PW} and {OTHER_PW_UNSPACED}; "
         f"Authorization: Basic {BASIC}; Bearer {OPAQUE_BEARER}; bare {JWT}; google {GOOGLE_TOKEN} key={API_KEY}; "
         f'{{"password": "{JSON_PASSWORD}"}}; https://admin:{USERINFO_PASSWORD}@test.example/wp-admin/')
NEVER = [APP_PW, APP_PW.replace(" ", ""), OTHER_PW, OTHER_PW_UNSPACED, BASIC, JWT, OPAQUE_BEARER, GOOGLE_TOKEN, API_KEY,
         JSON_PASSWORD, USERINFO_PASSWORD]


def leaks(text: str, preview: bool = False) -> list:
    found = [s for s in NEVER if s in text]
    return found + ([PREVIEW_TOKEN] if preview and PREVIEW_TOKEN in text else [])


def redact():
    return mod("redact")


@pytest.mark.parametrize("secret,sample", [
    (APP_PW, f"key {APP_PW} here"),
    (APP_PW.replace(" ", ""), f"key {APP_PW.replace(' ', '')}."),
    (OTHER_PW, f"\"{OTHER_PW}\""),
    (OTHER_PW_UNSPACED, f"pw={OTHER_PW_UNSPACED}"),
    (BASIC, f"Authorization: Basic {BASIC}"),
    (BASIC, f"{{'authorization': 'Basic {BASIC}'}}"),
    (OPAQUE_BEARER, f"authorization=Bearer {OPAQUE_BEARER}"),
    (OPAQUE_BEARER, f"sent Bearer {OPAQUE_BEARER} to it"),
    (JWT, f"token {JWT} in a sentence"),
    (GOOGLE_TOKEN, f"access {GOOGLE_TOKEN}"),
    (API_KEY, f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=x&key={API_KEY}"),
    (JSON_PASSWORD, f'{{"password": "{JSON_PASSWORD}"}}'),
    (JSON_PASSWORD, f'{{"app_password":"{JSON_PASSWORD}"}}'),
    (JSON_PASSWORD, f"login with pass={JSON_PASSWORD}&x=1"),
    (JSON_PASSWORD, f"secret: {JSON_PASSWORD}"),
    (USERINFO_PASSWORD, f"clone https://admin:{USERINFO_PASSWORD}@test.example/repo"),
])
def test_each_secret_form_is_scrubbed_from_text(secret, sample):
    out = redact().scrub_text(sample, [APP_PW])
    assert secret not in out and redact().MASK in out, out


def test_configured_secrets_are_scrubbed_whatever_their_shape():
    value = "short-but-secret-value"
    assert value not in redact().scrub_text(f"error mentions {value} twice: {value}", [value])


@pytest.mark.parametrize("key", ["password", "pw", "pass", "secret", "token", "api_key", "Authorization",
                                 "application_password", "client_secret", "access_token", "site1_password"])
def test_values_of_secret_keys_are_masked(key):
    out = redact().scrub({"outer": [{key: "plain words only", "title": "About us"}]})
    assert out["outer"][0][key] == redact().MASK and out["outer"][0]["title"] == "About us"


@pytest.mark.parametrize("text", [
    "this page will have more text",
    "Make sure your page loads fast and that every image has alt text",
    "Basic settings for the shop",
    "the Bearer of good news",
    "content hash 9f2c4a1b7e3d, caps ab12cd34ef56, fingerprint 1a2b3c4d",
    "item-20261125-101500-a1b2c3 and proof-20261125-101500-d4e5f6",
    "uuid 123e4567-e89b-12d3-a456-426614174000",
    "2026-11-25T10:00:00Z: published post 41",
    "https://test.example/about-us/?p=12",
    '{"title": "About us", "key_slot": 1, "key_env": "WPBRUECKE_KEY1"}',
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
])
def test_plain_text_and_ids_pass_unchanged(text):
    assert redact().scrub_text(text) == text


def test_preview_tokens_stay_in_results_and_leave_log_lines():
    r = redact()
    assert r.scrub_text(PREVIEW_URL) == PREVIEW_URL
    masked = r.scrub_text(PREVIEW_URL, previews=False)
    assert PREVIEW_TOKEN not in masked and "wpb_preview=[redacted]" in masked and "page_id=12" in masked


def test_scrubbing_twice_changes_nothing():
    once = redact().scrub_text(LEAKY, [APP_PW])
    assert redact().scrub_text(once, [APP_PW]) == once


# --- end to end: a real handler and the write log -----------------------------------------------------

def setup(tmp_path):
    fake = FakeWP(user="hermes", password=APP_PW, caps={"edit_posts": True, "upload_files": True},
                  namespaces=["wp/v2"])
    get_secret = secrets(WPBRUECKE_KEY1=APP_PW)
    rt = mod("runtime").Runtime(Settings(), get_secret, tmp_path, transport=fake, wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1}, "person")
    return fake, rt, get_secret


def run_leaky_calls(tmp_path) -> "tuple[str, str]":
    """A read whose content carries every secret form, and a write WordPress answers with an error that
    echoes the key; returns (all tool answers, the log file)."""
    fake, rt, get_secret = setup(tmp_path)
    plugin = load_plugin()
    kw = {"transport": fake, "wporg_fetch": lambda u: {"version": "1"}}
    leaky = fake.add_post(status="draft", title=f"Preview {PREVIEW_URL}", content=f"<p>{LEAKY}</p>")
    target = fake.add_post(status="draft", title="Keys page")

    def echo(method, parsed, headers, body):
        if method == "GET":
            return FakeWP.j(200, target)
        return FakeWP.j(500, {"code": "db_error", "message": f"{headers.get('Authorization')} {APP_PW}"})
    fake.routes[("POST", f"/wp-json/wp/v2/posts/{target['id']}")] = echo
    content = plugin.make_content_handler(rt.get_setting, tmp_path, get_secret, **kw)
    draft = plugin.make_draft_handler(rt.get_setting, tmp_path, lambda: mod("lanes").RunContext(), get_secret,
                                      tickets=mod("hooks").Tickets(), **kw)
    doctor = plugin.make_doctor_handler(rt.get_setting, tmp_path, get_secret, **kw)
    answers = [
        content({"site": "s", "action": "get", "id": leaky["id"]}),
        draft({"site": "s", "action": "update", "id": target["id"], "title": f"Keys {OTHER_PW}"}, session_id="s1"),
        draft({"site": "s", "action": "update", "id": leaky["id"], "excerpt": "x"}, session_id="s1"),
    ]
    answers.append(doctor({"action": "log", "site": "s"}))
    log = (tmp_path / "logs" / "s.jsonl").read_text(encoding="utf-8")
    return "\n".join(answers), log


def test_no_secret_survives_a_tool_result_or_a_log_line(tmp_path):
    answers, log = run_leaky_calls(tmp_path)
    assert json.loads(answers.splitlines()[0])["ok"] is True
    assert leaks(answers) == [], leaks(answers)
    assert leaks(log, preview=True) == [], leaks(log, preview=True)
    assert PREVIEW_TOKEN in answers  # the person's own preview link stays in the tool result
    lines = [json.loads(l) for l in log.splitlines()]
    assert [l["result"] for l in lines] == ["error", "ok"]
    assert "wpb_preview=[redacted]" in lines[1]["label"]


def test_the_leak_check_catches_an_unscrubbed_result(tmp_path, monkeypatch):
    r = redact()
    monkeypatch.setattr(r, "scrub_text", lambda text, secrets=(), previews=True: text)
    answers, log = run_leaky_calls(tmp_path)
    assert set(leaks(answers)) >= {APP_PW, BASIC, JWT, GOOGLE_TOKEN, API_KEY, JSON_PASSWORD, USERINFO_PASSWORD}
    assert APP_PW in log and PREVIEW_TOKEN in log


def test_a_failing_scrubber_shows_nothing_unchecked(tmp_path, monkeypatch):
    plugin = load_plugin()

    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(redact(), "scrub", broken)
    out = json.loads(plugin._guarded("wp_content", lambda: {"ok": True, "content": LEAKY}, secrets()))
    assert out["ok"] is False and out["error"] == "internal" and leaks(json.dumps(out)) == []
