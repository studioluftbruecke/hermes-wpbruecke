"""v0.4 phase-end review fixes: each test reproduces a break the review found, through the tools' own
handlers against the fake connector (elementor_fakes.py), and asserts what a user of the tools sees.

Row 1: Elementor's save drops dynamic tags the running Elementor cannot resolve (4.4.2 free registers
none), on elements the edit never named. The connector refuses such a page (``wpbruecke_unknown_dynamic``),
and the pack's read-back hashes every element's ``__dynamic__``, so a save that drops one anyway is caught.

Rows 2 and 3: the scrubber missed a bare ``base64(user:password)`` (a plugin echoing the Basic credential
without the word Basic), ``base64(password)``, the password with other separators than one space, values
under secret-named keys that are lists or dicts, and ``pwd=`` fields. Secret fixtures are joined at run
time so the release gate's secret scanner sees no literal.
"""
import base64
import json
import re

import pytest

from elementor_fakes import ElementorSite
from wpb_fakes import FakeWP, Settings, create_paired, load_plugin, mod, secrets

TAG = "[elementor-tag id=\"1a2b3c4\" name=\"{}\" settings=\"%7B%7D\"]"
BUTTON, HEADING, TEXT = "b1c2d3e", "e4f5a6b", "c7d8e9f"


def setup(tmp_path, role="editor"):
    fake = ElementorSite(role=role)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                       "packs": {"elementor": True}}, "person")
    plugin, run = load_plugin(), mod("lanes").RunContext()
    kw = dict(transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})
    d = plugin.make_draft_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret,
                                  tickets=mod("hooks").Tickets(), **kw)
    c = plugin.make_content_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    return fake, (lambda a: json.loads(d(a, session_id="x"))), (lambda a: json.loads(c(a)))


def writes(fake, since=0):
    return [f"{r['method']} {r['path']}" for r in fake.requests[since:] if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]


def page_with_button_tag(fake, tag_name):
    page = fake.elementor_page()
    tree = fake.tree(page["id"])
    button = next(el for el in tree[0]["elements"] if el["id"] == BUTTON)
    button["settings"]["__dynamic__"] = {"text": TAG.format(tag_name)}
    fake.posts[page["id"]]["meta"]["_elementor_data"] = json.dumps(tree)
    return page


def stage_heading(draft, page):
    return draft({"site": "s", "action": "stage", "type": "page", "id": page["id"],
                  "elementor_edits": [{"element_id": HEADING, "set": {"title": "New heading"}}]})


# --- row 1: dynamic tags ---------------------------------------------------------------------------------

def test_a_save_that_drops_an_untouched_dynamic_tag_is_refused_on_read_back(tmp_path):
    fake, draft, _ = setup(tmp_path)
    fake.dynamic_tags = ("post-title",)
    page = page_with_button_tag(fake, "post-title")

    def drop_dynamic(tree):   # what Elementor's Document::save does to a tag it cannot resolve
        for el in tree[0]["elements"]:
            if "__dynamic__" in el.get("settings", {}):
                el["settings"]["__dynamic__"] = []
        return tree
    fake.tamper = drop_dynamic
    before = set(fake.posts)
    out = stage_heading(draft, page)
    assert out["ok"] is False and out["error"] == "elementor_readback", out
    assert BUTTON in out["message"] and "dynamic tags" in out["message"]
    assert set(fake.posts) == before, "the broken copy is removed"
    live_button = next(el for el in fake.tree(page["id"])[0]["elements"] if el["id"] == BUTTON)
    assert live_button["settings"]["__dynamic__"] == {"text": TAG.format("post-title")}


def test_a_resolvable_dynamic_tag_on_another_element_is_kept_and_confirmed(tmp_path):
    fake, draft, _ = setup(tmp_path)
    fake.dynamic_tags = ("post-title",)
    page = page_with_button_tag(fake, "post-title")
    out = stage_heading(draft, page)
    assert out["ok"], out
    copy_button = next(el for el in fake.tree(out["wp_id"])[0]["elements"] if el["id"] == BUTTON)
    assert copy_button["settings"]["__dynamic__"] == {"text": TAG.format("post-title")}
    applied = draft({"site": "s", "action": "apply", "item": out["desk_item"]})
    assert applied["confirmed"], applied


def test_a_page_with_a_tag_elementor_cannot_resolve_is_read_only_and_nothing_is_sent(tmp_path):
    fake, draft, content = setup(tmp_path)   # dynamic_tags (): Elementor 4.4.2 free resolves none
    page = page_with_button_tag(fake, "post-title")
    got = content({"site": "s", "action": "get", "type": "page", "id": page["id"]})
    assert got["elementor"]["editable"] is False
    assert f"{BUTTON} text" in got["elementor"]["read_only"]
    n = len(fake.requests)
    out = stage_heading(draft, page)
    assert out["error"] == "elementor_unknown_dynamic", out
    assert f"{BUTTON} text" in out["message"] and "plugin that is not active cannot be edited here" in out["message"]
    assert writes(fake, n) == []


def test_the_connectors_unknown_dynamic_refusal_reaches_the_agent_with_its_sentence(tmp_path):
    fake, draft, _ = setup(tmp_path)
    page = page_with_button_tag(fake, "post-title")
    fake.read = (lambda orig: (lambda pid: {**orig(pid), "unknown_dynamic": []}))(fake.read)   # an older read
    out = stage_heading(draft, page)
    assert out["error"] == "elementor_unknown_dynamic" and f"{BUTTON} text" in out["message"], out
    assert [w for w in writes(fake) if "/elementor/" in w] == [f"POST /wp-json/wpbruecke/v1/elementor/{page['id']}/stage"]


# --- rows 2 and 3: the scrubber ---------------------------------------------------------------------------

PW = " ".join(["Ab3d", "eF6h", "iJ9l", "mN2p", "qR5t", "uV8x"])   # the site's own key (WPBRUECKE_KEY1)
CORE = PW.replace(" ", "")
LOGIN = "hermes_editor"
OTHER_PW = " ".join(["Zq7w", "Xe4r", "Ty1u", "Ip0o", "As2d", "Fg5h"])   # not configured here: shape only


def b64(text: str, urlsafe: bool = False) -> str:
    raw = text.encode()
    return (base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)).decode()


def survives(out: str, token: str = "") -> bool:
    """The password, in any spelling, or the token itself is still in the scrubbed text."""
    flat = re.sub(r"[^A-Za-z0-9]", "", out.replace("%20", "").replace("&nbsp;", "").replace("&#160;", ""))
    return CORE in flat or bool(token and token in out)


SEPARATED = {name: PW.replace(" ", sep) for name, sep in [
    ("dashes", "-"), ("underscores", "_"), ("dots", "."), ("%20", "%20"), ("plus", "+"), ("&nbsp;", "&nbsp;"),
    ("&#160;", "&#160;"), ("two spaces", "  "), ("newlines", "\n"), ("tabs", "\t"), ("no-break space", "\u00a0")]}


@pytest.mark.parametrize("name", sorted(SEPARATED))
def test_the_configured_password_with_any_separator_is_masked(name):
    out = mod("redact").scrub_text(f"error near {SEPARATED[name]} end", [PW])
    assert not survives(out), out
    assert out.startswith("error near ") and out.endswith(" end")


BASE64_FORMS = {
    "base64(user:password)": lambda: b64(f"{LOGIN}:{PW}"),
    "base64(user:unspaced password)": lambda: b64(f"{LOGIN}:{CORE}"),
    "base64(password)": lambda: b64(PW),
    "base64(unspaced password)": lambda: b64(CORE),
    "url-safe base64(user:password)": lambda: b64(f"{LOGIN}:{PW}", urlsafe=True),
    "base64 without padding": lambda: b64(f"{LOGIN}:{PW}").rstrip("="),
    "base64 of a password with a newline": lambda: b64(PW + "\n"),
}


@pytest.mark.parametrize("name", sorted(BASE64_FORMS))
@pytest.mark.parametrize("frame", ["excerpt={}&x=1", "{}", "auth was {} here", "\"{}\"", "creds{}"])
def test_a_base64_token_carrying_the_configured_password_is_masked(name, frame):
    token = BASE64_FORMS[name]()
    out = mod("redact").scrub_text(frame.format(token), [PW])
    assert token not in out and mod("redact").MASK in out, out


def test_base64_of_any_application_password_with_a_login_is_masked_by_shape():
    token = b64(f"someone:{OTHER_PW}")
    out = mod("redact").scrub_text(f"credential {token} echoed", [])
    assert token not in out, out


@pytest.mark.parametrize("text", [
    b64("hello world, this is fine"),                  # base64 of plain words
    "wp-block-group-is-layout-flex and elementor-widget-container",
    "item-20261125-101500-a1b2c3 and the hash 3f2a9c1b7e3d4f5a6b7c8d9e0f1a2b3c",
])
def test_ordinary_tokens_pass_unchanged(text):
    assert mod("redact").scrub_text(text, [PW]) == text


@pytest.mark.parametrize("value", [["plain words"], {"inner": "plain words"}, [{"x": 1}]])
@pytest.mark.parametrize("key", ["password", "pwd", "token", "client_secret"])
def test_lists_and_dicts_under_a_secret_key_are_masked(key, value):
    out = mod("redact").scrub({"outer": {key: value, "title": "About us"}})
    assert out["outer"][key] == mod("redact").MASK and out["outer"]["title"] == "About us"


@pytest.mark.parametrize("sample", ['{{"password": ["{}"]}}', '{{"api_token": {{"value": "{}"}}}}'])
def test_lists_and_objects_under_a_secret_key_in_json_text_are_masked(sample):
    value = "-".join(["hunter2", "plain"])
    out = mod("redact").scrub_text(sample.format(value), [])
    assert value not in out and mod("redact").MASK in out, out
    assert mod("redact").scrub_text(out, []) == out


@pytest.mark.parametrize("sample", ["log=admin&pwd={}&wp-submit=Log+In", "user_pwd={} sent", "{{\"pwd\": \"{}\"}}"])
def test_pwd_fields_are_masked(sample):
    value = "-".join(["hunter2", "plain"])
    out = mod("redact").scrub_text(sample.format(value), [])
    assert value not in out and mod("redact").MASK in out, out


def test_a_wordpress_error_echoing_the_bare_base64_credential_reaches_no_answer_log_or_activity(tmp_path):
    """The review's live reproduction (row 2): a plugin answers a write with an error that holds
    ``base64(login:password)`` without the word Basic."""
    token = b64(f"hermes:{PW}")
    fake = FakeWP(user="hermes", password=PW, caps={"edit_posts": True}, namespaces=["wp/v2"])
    get_secret = secrets(WPBRUECKE_KEY1=PW)
    kw = {"transport": fake, "wporg_fetch": lambda u: {"version": "1"}}
    rt = mod("runtime").Runtime(Settings(), get_secret, tmp_path, **kw)
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1}, "person")
    target = fake.add_post(status="draft", title="MARK ECHOAUTH probe")
    fake.routes[("POST", f"/wp-json/wp/v2/posts/{target['id']}")] = \
        lambda m, p, h, b: FakeWP.j(500, {"code": "db_error", "message": f"bad request from {token}",
                                          "data": {"excerpt": token}})
    plugin = load_plugin()
    draft = plugin.make_draft_handler(rt.get_setting, tmp_path, lambda: mod("lanes").RunContext(), get_secret,
                                      tickets=mod("hooks").Tickets(), **kw)
    doctor = plugin.make_doctor_handler(rt.get_setting, tmp_path, get_secret, **kw)
    answer = draft({"site": "s", "action": "update", "id": target["id"], "title": "x"}, session_id="s1")
    assert json.loads(answer)["ok"] is False
    log = (tmp_path / "logs" / "s.jsonl").read_text(encoding="utf-8")
    activity = json.dumps(mod("api").site_activity(rt, "s")[1])
    doctor_log = doctor({"action": "log", "site": "s"})
    assert "MARK ECHOAUTH" in log, "the write left a log line"
    for where, text in {"tool result": answer, "log file": log, "activity route": activity,
                        "wp_doctor log": doctor_log}.items():
        assert token not in text and not survives(text), where


# --- row 5: an error inside Elementor ----------------------------------------------------------------------

def test_an_error_inside_elementors_save_reaches_the_agent_as_elementor_error_and_the_copy_goes(tmp_path):
    fake, draft, _ = setup(tmp_path)
    page = fake.elementor_page()
    fake.save = lambda pid, edits: fake.j(409, {"code": "wpbruecke_elementor_error", "message": "Elementor failed "
                                                "while saving this page (TypeError); the draft was left as it was."})
    before = set(fake.posts)
    out = stage_heading(draft, page)
    assert out["error"] == "elementor_error" and "TypeError" in out["message"], out
    assert set(fake.posts) == before


# --- size cap -------------------------------------------------------------------------------------------

def test_an_edit_value_over_64_kb_is_refused_before_any_request_and_64_kb_passes(tmp_path):
    fake, draft, _ = setup(tmp_path)
    page = fake.elementor_page()
    n = len(fake.requests)
    big = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"],
                 "elementor_edits": [{"element_id": TEXT, "set": {"editor": "<p>" + "ä" * 32767 + "</p>"}}]})
    assert big["error"] == "bad_value" and "64 KB" in big["message"], big
    assert writes(fake, n) == []
    exact = "x" * (65536 - 7)
    ok = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"],
                "elementor_edits": [{"element_id": TEXT, "set": {"editor": f"<p>{exact}</p>"}}]})
    assert ok["ok"], ok


# --- draft.py _apply: the copy's _wpbruecke_source against the desk item --------------------------------

def test_apply_refuses_a_copy_repointed_at_another_live_post_after_staging(tmp_path):
    fake, draft, _ = setup(tmp_path)
    page, other = fake.elementor_page(), fake.elementor_page(title="Other page")
    staged = stage_heading(draft, page)
    assert staged["ok"], staged
    fake.posts[staged["wp_id"]]["meta"]["_wpbruecke_source"] = other["id"]
    n = len(fake.requests)
    out = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert out["error"] == "not_staged" and str(other["id"]) in out["message"], out
    assert writes(fake, n) == []
    assert fake.posts[staged["wp_id"]]["status"] == "draft"
