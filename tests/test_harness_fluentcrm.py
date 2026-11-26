"""Opt-in: the FluentCRM pack's six live boxes on the full harness (WordPress 7.1.5, FluentCRM 3.3.5, this
connector), driven through the plugin's own wp_pack gate and handler as the seeded Hermes user, and the
connector's wp-admin screen clicked by an administrator who logs in through wp-login.php.

The six boxes of the FluentCRM permissions milestone:
  1 default boxes: the Hermes user holds fcrm_read_emails only; a draft in the site's design is saved
    through the route, opens as blocks in FluentCRM's editor, and its proof renders at 600 and 390 px
  2 every direct path (P33's list, and FluentCRM Pro's own routes on Pro) refuses the user, again after
    each route call, and sends no mail
  3 the test email reaches the saved address only (harness mail log); another to_email changes nothing
  4 send box off: schedule-campaign is refused before any request, naming the screen
  5 send box on: the card shows the campaign, the send time and the recipient estimate, and the campaign
    is scheduled only after the card is answered; an unattended run is refused (the real chat turn runs
    in a scratch profile, outside this test)
  6 a second user's other FluentCRM permissions survive a save; deactivating the connector leaves the
    Hermes user without FluentCRM permissions

``WPB_FCRM_PRO=1`` boots FluentCRM Pro too (unpacked beforehand into the harness's gitignored plugins
folder, no licence key) and adds the Pro checks: Pro's Managers screen and this screen
show the same grants, and Pro's sequences, recurring campaigns and campaigns-pro routes refuse the user.
``WPB_PK4_OUT=<file>`` writes what each box measured (no secrets, no addresses but the harness inbox), and
``WPB_PK4_EVIDENCE=1`` rewrites the tracked ``runs/v05-pk4-preview-600.webp`` (otherwise the preview's WebP
goes to a temporary folder, so a run leaves the tree clean).
Mail never leaves the harness: the PK0 mail-log stub short-circuits wp_mail. Skipped unless WPB_HARNESS=1.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

import harness_support as hs
from wpb_fakes import load_plugin, mod

EXP = hs.HARNESS.parent
STUB = EXP / "live" / "stubs" / "wpb-pk4-stub"
MAILLOG = EXP / "probes" / "pk0" / "stub" / "wpb-pk0-maillog"
PRO_DIR = hs.HARNESS / "plugins" / "fluentcampaign-pro"
PRO = os.environ.get("WPB_FCRM_PRO") == "1"
GUEST = "/wordpress/wp-content/plugins/"
HERMES, SECOND = "hermes_drafter", "hermes_editor"
INBOX = "pk4-inbox@example.test"
OTHER_TO = "someone-else@example.test"
SECOND_PERMS = ["fcrm_read_contacts", "fcrm_manage_forms", "fcrm_read_funnels"]
STAMP = time.strftime("%H%M%S")
FC = "/fluent-crm/v2"
CONNECTOR = "wpbruecke-connector/wpbruecke-connector"
RECORD: dict = {"pro": PRO, "boxes": {}}

needs = [STUB, MAILLOG] + ([PRO_DIR] if PRO else [])
pytestmark = pytest.mark.skipif(hs.skip_reason(*needs) is not None, reason=hs.skip_reason(*needs) or "")


def preview_webp(tmp_dir: Path) -> Path:
    """Where the preview's 600 px WebP goes: the tracked evidence file only when WPB_PK4_EVIDENCE=1."""
    if os.environ.get("WPB_PK4_EVIDENCE") == "1":
        return EXP / "runs" / "v05-pk4-preview-600.webp"
    return tmp_dir / "v05-pk4-preview-600.webp"


def box(n: int, **facts) -> None:
    RECORD["boxes"].setdefault(str(n), {}).update(facts)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    mounts = [(STUB, GUEST + "wpb-pk4-stub"), (MAILLOG, GUEST + "wpb-pk0-maillog")]
    if PRO:
        mounts.append((PRO_DIR, GUEST + "fluentcampaign-pro"))
    for log in ("pk0-mail.jsonl", "pk4-mail.jsonl"):
        (hs.HARNESS / "run" / log).unlink(missing_ok=True)
    if os.environ.get("WPB_PK4_ATTACH") == "1":  # a box already started with the same mounts (debugging)
        run = hs.HARNESS / "run"
        yield hs.Harness("full", json.loads((run / "ready.json").read_text()),
                         json.loads((run / "creds.json").read_text()), json.loads((run / "seed.json").read_text()))
    else:
        yield from hs.boot("full", tmp_path_factory.mktemp("harness-fcrm"), extra_mounts=tuple(mounts))
    out = os.environ.get("WPB_PK4_OUT")
    if out:
        Path(out).write_text(json.dumps(RECORD, indent=1, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module")
def admin(harness):
    return harness.user("hermes_admin")


@pytest.fixture(scope="module")
def ui(harness, admin):
    """Cookies of an administrator logged in through wp-login.php, and its login for the editor check."""
    status, got = hs.rest(*admin, "POST", "/wpb-pk4/v1/admin")
    assert status == 200, got
    cookies = {"wordpress_test_cookie": "WP%20Cookie%20check"}
    _, headers, _ = hs.http("POST", "/wp-login.php", form={"log": got["login"], "pwd": got["login_password"],
                                                           "wp-submit": "Log In", "testcookie": "1",
                                                           "redirect_to": "/wp-admin/"}, cookies=cookies)
    for key, value in headers:
        if key.lower() == "set-cookie":
            name, _, rest_ = value.partition("=")
            value = rest_.split(";", 1)[0]
            if name.startswith("wordpress_") and value and value != "deleted":
                cookies[name] = value
    assert any(k.startswith("wordpress_logged_in_") for k in cookies)
    for _ in range(4):  # WooCommerce's and Elementor's one-time onboarding redirects for a new administrator
        if hs.http("GET", "/wp-admin/options-general.php?page=wpbruecke-fluentcrm", cookies=cookies)[0] == 200:
            break
    return {"cookies": cookies, "login": got["login"], "login_password": got["login_password"]}


@pytest.fixture(scope="module")
def audience(harness, admin):
    status, got = hs.rest(*admin, "POST", "/wpb-pk4/v1/fcrm/audience")
    assert status == 200, got
    return got


def raw(admin, login) -> dict:
    return hs.rest(*admin, "GET", f"/wpb-pk4/v1/fcrm/raw?login={login}")[1]


def mails(log: str = "pk0-mail.jsonl") -> list:
    path = hs.HARNESS / "run" / log
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


class Recorder:
    """The stdlib transport, with every request's method and path kept (no headers, no bodies)."""

    def __init__(self):
        self.send = mod("client")._stdlib_transport("")
        self.seen: list = []

    def __call__(self, method, url, headers, body, timeout):
        self.seen.append((method, url.split("?")[0]))
        return self.send(method, url, headers, body, timeout)


class FcrmAgent(hs.Agent):
    """hs.Agent with the FluentCRM pack on and wp_pack wired as Hermes wires it (the gate, then the handler)."""

    def __init__(self, login, pw):
        self.recorder = Recorder()
        super().__init__(login, pw, packs=False)
        self.kw["transport"] = self.recorder
        self.rt().sites.update(self.site, {"packs": {"fluentcrm": True}}, "person")
        plugin, hooks = load_plugin(), mod("hooks")
        self.tickets = hooks.Tickets()
        self.run = mod("lanes").RunContext()
        self.pack_h = plugin.make_pack_handler(self.settings, self.tmp, lambda: self.run, self.secret,
                                               tickets=self.tickets, **self.kw)
        self.gate = hooks.make_gate({"wp_pack": plugin.pack_spec(self.rt)}, lambda: self.run, lambda: True,
                                    self.tickets)

    def forget(self):
        """The screen changed: read FcrmPermissions again on the next call."""
        self.rt().store.path("facts", f"_fcrm-{self.site}.json").unlink(missing_ok=True)

    def args(self, op, **input_):
        return {"site": self.site, "pack": "fluentcrm", "action": "run", "op": op, "input": input_}

    def ask(self, op, **input_):
        """The gate's verdict alone (what Hermes shows before anything runs)."""
        return self.gate(tool_name="wp_pack", args=self.args(op, **input_), session_id=f"harness-{self.login}")

    def pack(self, op, **input_):
        """The gate, then the handler; a card counts as answered with Enter."""
        verdict = self.ask(op, **input_)
        if verdict and verdict["action"] == "block":
            return verdict, None
        return verdict, json.loads(self.pack_h(self.args(op, **input_), session_id=f"harness-{self.login}"))

    def unattended(self, op, **input_):
        saved, self.run = self.run, mod("lanes").RunContext(cron=True)
        try:
            return self.pack(op, **input_)
        finally:
            self.run = saved


@pytest.fixture(scope="module")
def hermes(harness):
    return FcrmAgent(HERMES, harness.pw(HERMES))


def screen_html(ui, user_id) -> str:
    status, _, body = hs.http("GET", f"/wp-admin/options-general.php?page=wpbruecke-fluentcrm&user={user_id}",
                              cookies=ui["cookies"])
    assert status == 200
    return body.decode("utf-8", "replace")


def boxes_shown(page: str) -> dict:
    out = {}
    for key in ("view", "draft", "test", "send"):
        tag = re.search(r'<input type="checkbox" name="wpb_%s"[^>]*>' % key, page)
        out[key] = bool(tag and "checked" in tag.group(0))
    return out


def save_screen(ui, user_id, *, view=False, draft=False, test=False, send=False, to="") -> str:
    """The administrator's click on the screen: the real form, its nonce, admin-post.php."""
    page = screen_html(ui, user_id)
    form = re.search(r'<form method="post" action="[^"]*admin-post\.php">(.*?)</form>', page, re.S).group(1)
    fields = {k: html.unescape(v) for k, v in re.findall(r'<input type="hidden" (?:id="[^"]*" )?name="([^"]+)" value="([^"]*)"', form)}
    for key, on in (("view", view), ("draft", draft), ("test", test), ("send", send)):
        if on:
            fields[f"wpb_{key}"] = "1"
    fields["wpb_test_to"] = to
    status, headers, _ = hs.http("POST", "/wp-admin/admin-post.php", cookies=ui["cookies"], form=fields)
    location = next((v for k, v in headers if k.lower() == "location"), "")
    assert status == 302 and "wpb_notice=saved" in location, (status, location)
    return screen_html(ui, user_id)


def uid(harness, login) -> int:
    return int(harness.creds[login]["id"])


SECTIONS = [{"heading": "Three new courses", "text": "Hello {{contact.first_name}},\n\nthree courses start in January.",
             "button": {"text": "See the courses", "url": f"{hs.BASE}/"}}]


def direct_paths(harness, campaign_id) -> dict:
    """P33's list as the Hermes user itself: FluentCRM's own REST and abilities (Pro's own routes on Pro)."""
    h = harness.user(HERMES)
    ab = "/wp-abilities/v1/abilities/fluent-crm"
    paths = [("GET campaigns (read)", "GET", f"{FC}/campaigns?per_page=5", None),
             ("POST campaigns", "POST", f"{FC}/campaigns", {"title": f"PK4 direct {STAMP}"}),
             ("PUT campaigns/<id>", "PUT", f"{FC}/campaigns/{campaign_id}", {"title": "direct put"}),
             ("POST campaigns/<id>/schedule", "POST", f"{FC}/campaigns/{campaign_id}/schedule", {}),
             ("ability upsert-campaign", "POST", f"{ab}/upsert-campaign/run", {"input": {"campaign_id": campaign_id,
                                                                                         "email_subject": "direct"}}),
             ("ability change-campaign-status", "POST", f"{ab}/change-campaign-status/run",
              {"input": {"campaign_id": campaign_id, "action": "schedule"}}),
             ("ability send-test-email", "POST", f"{ab}/send-test-email/run",
              {"input": {"campaign_id": campaign_id, "to_email": OTHER_TO}}),
             ("ability send-email-to-contact", "POST", f"{ab}/send-email-to-contact/run",
              {"input": {"email": "pk4-reader@example.test", "subject": "x", "body": "x"}})]
    if PRO:
        paths += [("POST sequences", "POST", f"{FC}/sequences", {"title": f"PK4 seq {STAMP}"}),
                  ("POST recurring-campaigns", "POST", f"{FC}/recurring-campaigns", {"title": f"PK4 rec {STAMP}"}),
                  ("POST campaigns-pro/<id>/resend-emails", "POST", f"{FC}/campaigns-pro/{campaign_id}/resend-emails", {}),
                  ("POST campaigns-pro/<id>/tag-actions", "POST", f"{FC}/campaigns-pro/{campaign_id}/tag-actions", {})]
    out = {}
    for label, method, path, body in paths:
        status, _ = hs.rest(*h, method, path, body)
        out[label] = status
    return out


def assert_refused(got: dict) -> None:
    for label, status in got.items():
        if "(read)" in label:
            assert status == 200, (label, status)
        else:
            assert status in (401, 403), (label, status)


# --- before the screen was saved ------------------------------------------------------------------------------

def test_never_saved_the_pack_runs_nothing_and_names_the_screen(harness, admin, ui, hermes, audience):
    for login in (HERMES, SECOND):
        assert hs.rest(*admin, "POST", "/wpb-pk4/v1/fcrm/reset", {"login": login})[0] == 200
    hermes.forget()
    booted = harness.plugins
    RECORD["versions"] = {k: booted.get(k) for k in ("fluent-crm", "fluentcampaign-pro", "wpbruecke-connector")}
    RECORD["versions"]["wordpress"] = (harness.ready.get("booted") or {}).get("wp")
    seen = hs.rest(*harness.user(HERMES), "GET", "/wpbruecke/v1/fluentcrm/permissions")[1]
    RECORD["versions"]["fluent-crm"], RECORD["versions"]["fluentcampaign-pro"] = seen["version"], seen["pro_version"]
    assert bool(seen["pro_version"]) is PRO
    if PRO:  # Q23: a grant in Pro's Managers screen shows on this screen, and the reverse further down
        status, _ = hs.rest(*admin, "POST", f"{FC}/campaign-pro-settings/managers",
                            {"manager": {"email": f"{HERMES}@example.test", "permissions": ["fcrm_read_emails"]}})
        assert status == 200
        shown = boxes_shown(screen_html(ui, uid(harness, HERMES)))
        assert shown["view"] is True and "FluentCRM now: granted" in screen_html(ui, uid(harness, HERMES))
        box(1, pro_grant_shows_on_screen=True)
    page = screen_html(ui, uid(harness, HERMES))
    assert "Not saved for this user yet" in page
    offered = re.findall(r'<option value="(\d+)"', page)
    assert str(uid(harness, HERMES)) in offered and str(uid(harness, "hermes_admin")) not in offered  # never an admin
    verdict, out = hermes.pack("crm-context")
    assert verdict["action"] == "block" and "refused (needs_permission)" in verdict["message"]
    assert "Settings > WPBrücke FluentCRM" in verdict["message"] and "runs nothing" in verdict["message"]
    line = next(l for l in hermes.doctor()["sites"][0]["lines"] if l.startswith("FluentCRM permissions"))
    assert "never saved for this user" in line
    nonce_less = hs.http("POST", "/wp-admin/admin-post.php", cookies=ui["cookies"],
                         form={"action": "wpbruecke_fcrm_save", "user": str(uid(harness, HERMES)), "wpb_send": "1"})
    assert nonce_less[0] in (403, 400) and raw(admin, HERMES)["boxes"] is None
    if PRO:
        hs.rest(*admin, "DELETE", f"{FC}/campaign-pro-settings/managers/{uid(harness, HERMES)}")
    box(0, refused=verdict["message"].split(": ", 1)[-1][:400], doctor=line, save_without_nonce=nonce_less[0])


# --- box 1 --------------------------------------------------------------------------------------------------

def test_box1_default_boxes_draft_in_the_sites_design_and_its_proof(harness, admin, ui, hermes, audience, tmp_path):
    page = save_screen(ui, uid(harness, HERMES), view=True, draft=True)
    hermes.forget()
    held = raw(admin, HERMES)
    assert held["meta_row"] == ["fcrm_read_emails"] and held["has_role_meta"] is True
    assert held["own_contact"]["user_id"] == uid(harness, HERMES) and held["own_contact"]["status"] == "transactional"
    assert boxes_shown(page) == {"view": True, "draft": True, "test": False, "send": False}
    assert 'whole "Write and send emails" permission (fcrm_manage_emails)' in html.unescape(page)
    if PRO:
        status, got = hs.rest(*admin, "GET", f"{FC}/campaign-pro-settings/managers?per_page=50")
        listed = {m["id"]: sorted(m.get("permissions") or []) for m in ((got.get("managers") or {}).get("data") or [])}
        assert listed.get(uid(harness, HERMES)) == ["fcrm_read_emails"]
        box(1, pro_lists_the_connector_save=True)

    verdict, ctx = hermes.pack("crm-context", include=["sender", "segments"])
    assert verdict is None and ctx["ok"], ctx
    verdict, out = hermes.pack("draft-campaign", title=f"PK4 newsletter {STAMP}", email_subject="Winter at the studio",
                               email_pre_header="Three new courses", sections=SECTIONS,
                               recipients={"lists": [audience["list_slug"]]})
    assert verdict is None and out["ok"], out
    cid = out["result"]["campaign_id"]
    row = hs.rest(*admin, "GET", f"/wpb-pk4/v1/campaign?id={cid}")[1]
    assert row["status"] == "draft" and row["created_by"] == uid(harness, HERMES) and row["block_comments"]
    assert row["design_template"] == "simple" and row["template_config"]["text_color"] == out["design"]["text"]
    assert raw(admin, HERMES)["meta_row"] == ["fcrm_read_emails"]       # the lend left nothing behind
    item = mod("desk").Desk(hermes.rt().store).get(out["desk_item"])
    assert item["kind"] == "campaign" and item["links"]["edit"].endswith(f"#/email/campaigns/{cid}")

    verdict, prev = hermes.pack("preview-campaign", campaign_id=cid)
    assert verdict is None and prev["ok"], prev
    proof = prev["result"]["proof"]
    assert [s["width"] for s in proof["shots"]] == [600, 390], proof
    rows = {c["check"]: c["verdict"] for c in proof["checks"]}
    assert rows.get("render") == "match" and rows.get("contrast") == "match" and rows.get("broken_links") == "match", proof
    assert audience["customer_name"] not in prev["result"]["text"]          # SmartCodes from the own contact
    shot = hermes.rt().store.path("proofs", proof["proof_id"]) / "600.png"
    if not PRO:
        from PIL import Image
        im = Image.open(shot).convert("RGB")
        im.save(preview_webp(tmp_path), "WEBP", quality=70, method=6)

    editor = open_in_editor(ui, cid, f"PK4 newsletter {STAMP}")
    box(1, holds=held["meta_row"], has_role_meta=held["has_role_meta"], own_contact_status=held["own_contact"]["status"],
        campaign_id=cid, created_by_is_hermes=True, design=out["design"], design_template=row["design_template"],
        proof={"verdict": proof["verdict"], "checks": proof["checks"], "widths": [s["width"] for s in proof["shots"]]},
        smartcodes_from_own_contact=True, editor=editor)
    RECORD["campaign_id"] = cid

    # another person's draft asks the person first (A31)
    status, other = hs.rest(*admin, "POST", f"{FC}/campaigns", {"title": f"PK4 admin draft {STAMP}"})
    other_id = (other.get("campaign") or other).get("id") if isinstance(other, dict) else None
    assert status == 200 and other_id
    verdict = hermes.ask("draft-campaign", campaign_id=other_id, sections=SECTIONS)
    assert verdict["action"] == "approve" and verdict["message"].startswith("Overwrites another person's draft")
    hermes.tickets.revoke(hermes.tickets.key("wp_pack", f"harness-{HERMES}",
                                             hermes.args("draft-campaign", campaign_id=other_id, sections=SECTIONS)))
    box(1, others_draft_card=verdict["message"][:600])


def open_in_editor(ui, cid, marker) -> dict:
    """FluentCRM's own campaign editor as the administrator (probes/pk0/q20_editor.mjs): block editor, which
    blocks, and no invalid-block warning."""
    script = EXP / "probes" / "pk0" / "q20_editor.mjs"
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        creds = Path(tmp) / "creds.json"
        creds.write_text(json.dumps({"pk0_ui_admin": {"login_password": ui["login_password"]}}))
        creds.chmod(0o600)
        cases = Path(tmp) / "cases.json"
        cases.write_text(json.dumps([{"name": "pk4", "id": cid, "design_template": "simple", "body_kind": "blocks",
                                      "marker": marker}]))
        for attempt in range(2):  # the first FluentCRM admin load on a fresh box can outlast the script's wait
            proc = subprocess.run(["node", str(script), hs.BASE, str(creds), str(cases), tmp, ""], capture_output=True,
                                  text=True, timeout=900)
            assert proc.returncode == 0, proc.stderr[-800:]
            case = json.loads(proc.stdout)["cases"]["pk4"]
            if case.get("editor_appeared"):
                break
    frames = [f for f in case.get("frames") or [] if f.get("block_editor")]
    assert frames, case
    types: dict = {}
    for f in frames:
        for t, n in (f.get("block_types") or {}).items():
            types[t] = types.get(t, 0) + n
    warnings = sum(f.get("invalid_block_warnings") or 0 for f in frames)
    assert warnings == 0 and "core/freeform" not in types and types.get("core/button"), types
    return {"block_types": types, "invalid_block_warnings": warnings}


# --- box 2 --------------------------------------------------------------------------------------------------

def test_box2_every_direct_path_refuses_the_user_after_each_route_call(harness, hermes):
    cid = RECORD["campaign_id"]
    before = len(mails())
    runs = {"after draft": direct_paths(harness, cid)}
    hermes.pack("draft-campaign", campaign_id=cid, sections=SECTIONS)
    runs["after a second draft call"] = direct_paths(harness, cid)
    hermes.pack("preview-campaign", campaign_id=cid)
    runs["after preview"] = direct_paths(harness, cid)
    for got in runs.values():
        assert_refused(got)
    assert len(mails()) == before
    box(2, runs=runs, mail_from_direct_paths=0)


# --- box 3 --------------------------------------------------------------------------------------------------

def test_box3_test_email_reaches_the_saved_address_only(harness, admin, ui, hermes):
    cid = RECORD["campaign_id"]
    save_screen(ui, uid(harness, HERMES), view=True, draft=True, test=True, to=INBOX)
    hermes.forget()
    before = len(mails())
    verdict, out = hermes.pack("send-test", campaign_id=cid)
    assert verdict is None and out["ok"], out
    status, direct = hs.rest(*harness.user(HERMES), "POST", "/wpbruecke/v1/fluentcrm/test",
                             {"campaign_id": cid, "to_email": OTHER_TO})
    assert status == 200, direct
    new = mails()[before:]
    addresses = [[re.sub(r"^.*<([^>]+)>$", r"\1", t) for t in m["to"]] for m in new]   # "Name <addr>"
    assert addresses == [[INBOX], [INBOX]], new
    assert all(m["subject"].startswith("TEST:") for m in new)
    verdict, _ = hermes.pack("send-test", campaign_id=cid, to_email=OTHER_TO)
    assert verdict["action"] == "block" and "does not take to_email" in verdict["message"]
    assert_refused(direct_paths(harness, cid))
    assert len(mails()) == before + 2
    box(3, mails_to=addresses, route_ignored_to_email=True, wp_pack_refused_to_email=True)


# --- SmartCodes after the own contact is gone (review row 1) -------------------------------------------------

def test_smartcodes_never_fill_from_a_customer_after_the_own_contact_is_deleted(harness, admin, ui, hermes, audience):
    cid = RECORD["campaign_id"]
    customer = audience["customer_name"]
    before = len(mails("pk4-mail.jsonl"))
    seen = {}
    for op in ("preview-campaign", "send-test"):
        dropped = hs.rest(*admin, "POST", "/wpb-pk4/v1/fcrm/drop-contact", {"login": HERMES})[1]
        assert dropped["deleted"] and raw(admin, HERMES)["own_contact"] is None
        verdict, out = hermes.pack(op, campaign_id=cid)
        assert verdict is None and out["ok"], out
        own = raw(admin, HERMES)["own_contact"]
        assert own and own["user_id"] == uid(harness, HERMES) and own["status"] == "transactional"
        assert own["id"] != dropped["deleted"] and raw(admin, HERMES)["boxes"]["contact"] == own["id"]
        seen[op] = out
    assert customer not in json.dumps(seen["preview-campaign"]), seen["preview-campaign"]["result"]["text"][:300]
    assert "Hello" in seen["preview-campaign"]["result"]["text"]
    sent = mails("pk4-mail.jsonl")[before:]
    assert len(sent) == 1 and not sent[0]["body_has_customer"] and customer not in json.dumps(sent[0]["to"]), sent
    # the saved test address is the customer's own: FluentCRM would render against that contact first
    save_screen(ui, uid(harness, HERMES), view=True, draft=True, test=True, to="pk4-customer@example.test")
    hermes.forget()
    verdict, out = hermes.pack("send-test", campaign_id=cid)
    assert verdict is None and out["ok"], out
    to_customer = mails("pk4-mail.jsonl")[before + 1:]
    assert len(to_customer) == 1 and not to_customer[0]["body_has_customer"], to_customer
    assert customer not in json.dumps(to_customer[0]["to"]), to_customer
    save_screen(ui, uid(harness, HERMES), view=True, draft=True, test=True, to=INBOX)
    hermes.forget()
    box(3, own_contact_deleted={"preview_names_customer": False, "test_mail_names_customer": False,
                                "test_to_customer_address_names_customer": False})


# --- box 4 --------------------------------------------------------------------------------------------------

def test_box4_send_box_off_schedule_is_refused_before_any_request(harness, admin, hermes):
    cid = RECORD["campaign_id"]
    hermes.recorder.seen.clear()
    verdict, out = hermes.pack("schedule-campaign", campaign_id=cid, scheduled_at="2027-01-15 09:00:00")
    assert verdict["action"] == "block" and "refused (needs_permission)" in verdict["message"]
    assert 'It is the box "Schedule and send campaigns"' in verdict["message"]
    assert "Settings > WPBrücke FluentCRM" in verdict["message"]
    assert not [p for _, p in hermes.recorder.seen if "change-campaign-status" in p or "/schedule" in p]
    assert hs.rest(*admin, "GET", f"/wpb-pk4/v1/campaign?id={cid}")[1]["status"] == "draft"
    box(4, refused=verdict["message"].split(": ", 1)[-1][:500], requests=[p for _, p in hermes.recorder.seen])


# --- box 5 --------------------------------------------------------------------------------------------------

def test_box5_send_box_on_a_card_every_time_and_never_unattended(harness, admin, ui, hermes):
    cid = RECORD["campaign_id"]
    page = save_screen(ui, uid(harness, HERMES), view=True, draft=True, test=True, send=True, to=INBOX)
    hermes.forget()
    assert boxes_shown(page)["send"] is True
    assert sorted(raw(admin, HERMES)["meta_row"]) == ["fcrm_manage_emails", "fcrm_read_emails"]
    when = "2027-01-15 09:00:00"
    verdict, out = hermes.unattended("schedule-campaign", campaign_id=cid, scheduled_at=when)
    assert verdict["action"] == "block" and "refused (unattended)" in verdict["message"]
    assert hs.rest(*admin, "GET", f"/wpb-pk4/v1/campaign?id={cid}")[1]["status"] == "draft"
    cards = []
    for _ in range(2):
        v = hermes.ask("schedule-campaign", campaign_id=cid, scheduled_at=when)
        assert v["action"] == "approve"
        cards.append(v)
    assert cards[0]["rule_key"] != cards[1]["rule_key"]
    card = cards[-1]["message"]
    first = card.split("\n", 1)[0]
    assert f"PK4 newsletter {STAMP}" in first and f"at {when}" in first
    m = re.search(r"^Sends real email to about (\d+) subscribed contacts", first)
    assert m and int(m.group(1)) >= 2, card
    assert hs.rest(*admin, "GET", f"/wpb-pk4/v1/campaign?id={cid}")[1]["status"] == "draft"   # nothing before Enter
    out = json.loads(hermes.pack_h(hermes.args("schedule-campaign", campaign_id=cid, scheduled_at=when),
                                   session_id=f"harness-{HERMES}"))
    assert out["ok"], out
    row = hs.rest(*admin, "GET", f"/wpb-pk4/v1/campaign?id={cid}")[1]
    assert row["status"] in ("scheduled", "pending-scheduled") and str(row["scheduled_at"]).startswith("2027-01-15")
    again = json.loads(hermes.pack_h(hermes.args("schedule-campaign", campaign_id=cid, scheduled_at=when),
                                     session_id=f"harness-{HERMES}"))
    assert again["error"] in ("not_approved",), again
    lines = [l for l in hermes.log_lines() if l.get("action") == "schedule-campaign"]
    assert [l["result"] for l in lines][-2:] == ["ok", "refused"]
    status, _ = hs.rest(*admin, "POST", "/wp-abilities/v1/abilities/fluent-crm/change-campaign-status/run",
                        {"input": {"campaign_id": cid, "action": "unschedule"}})
    box(5, unattended=verdict["message"].split(": ", 1)[-1][:300], card=card, card_rule_keys_differ=True,
        status_before_enter="draft", status_after_enter=row["status"], scheduled_at=row["scheduled_at"],
        second_run_without_card=again["error"], cleanup_unschedule_http=status)
    save_screen(ui, uid(harness, HERMES), view=True, draft=True, test=True, to=INBOX)
    hermes.forget()


# --- box 6 --------------------------------------------------------------------------------------------------

def test_box6_other_permissions_survive_and_deactivation_takes_the_grants_back(harness, admin, ui, hermes):
    if PRO:
        status, _ = hs.rest(*admin, "POST", f"{FC}/campaign-pro-settings/managers",
                            {"manager": {"email": f"{SECOND}@example.test", "permissions": SECOND_PERMS}})
        assert status == 200
    else:
        assert hs.rest(*admin, "POST", "/wpb-pk4/v1/fcrm/attach", {"login": SECOND, "permissions": SECOND_PERMS})[0] == 200
    save_screen(ui, uid(harness, HERMES), view=True, draft=True)
    assert sorted(raw(admin, SECOND)["meta_row"]) == sorted(SECOND_PERMS)
    save_screen(ui, uid(harness, SECOND), view=True)
    assert sorted(raw(admin, SECOND)["meta_row"]) == sorted(SECOND_PERMS + ["fcrm_read_emails"])
    save_screen(ui, uid(harness, SECOND))
    second_after = raw(admin, SECOND)["meta_row"]
    assert sorted(second_after) == sorted(SECOND_PERMS) and raw(admin, SECOND)["has_role_meta"] is True

    status, got = hs.rest(*admin, "POST", f"/wp/v2/plugins/{CONNECTOR}", {"status": "inactive"})
    assert status == 200 and got.get("status") == "inactive", got
    try:
        hermes_after = raw(admin, HERMES)
        second_final = raw(admin, SECOND)
        assert hermes_after["meta_row"] == [] and hermes_after["has_role_meta"] is False
        assert sorted(second_final["meta_row"]) == sorted(SECOND_PERMS)
        status_ctx, _ = hs.rest(*harness.user(HERMES), "GET",
                                "/wp-abilities/v1/abilities/fluent-crm/get-crm-context/run?input[include_segment_counts]=false")
        assert status_ctx == 403
    finally:
        status_on, _ = hs.rest(*admin, "POST", f"/wp/v2/plugins/{CONNECTOR}", {"status": "active"})
    box(6, second_user_kept=second_after, after_deactivation={"hermes": hermes_after["meta_row"],
                                                              "hermes_has_role_meta": hermes_after["has_role_meta"],
                                                              "second": second_final["meta_row"]},
        crm_context_after_deactivation=status_ctx, reactivated_http=status_on)
