"""The FluentCRM pack (v0.5 PK4) through wp_pack: the gate's verdicts, what reaches WordPress, the desk item,
the email in the site's design and its proof.

Every test runs the plugin's own pre_tool_call gate and handler (as test_v05_pack_tool.py does) against a
FakeWP that answers like FluentCRM 3.3.5 with the WPBrücke connector: the recorded fluent-crm abilities,
FcrmPermissions from GET wpbruecke/v1/fluentcrm/permissions (what the site's admin ticked), the three
lending routes, and FluentCRM's own campaign read.
"""
import json
import re
import subprocess
from urllib.parse import parse_qsl

import pytest

from wpb_fakes import FakeWP, ROLE_CAPS, ROOT, Settings, create_paired, load_plugin, mod, php_command, secrets

PW = "pw pw pw"
HERMES_ID = 1          # the fake's users/me id
OTHER_ID = 9
LISTING = [a for a in json.loads((ROOT / "tests" / "fixtures" / "abilities" / "listing.json").read_text())["items"]
           if a["name"].startswith("fluent-crm/")]
LISTING.append({"name": "fluent-crm/get-campaign", "label": "Get Campaign", "description": "One campaign",
                "category": "fluent-crm", "input_schema": {"type": "object", "properties": {
                    "campaign_id": {"type": "integer"}, "include": {"type": "array"}, "body_format": {"type": "string"}}},
                "output_schema": {}, "meta": {"annotations": {"readonly": True, "destructive": False, "idempotent": True}}})
LISTING.append({"name": "fluent-crm/send-email-to-contact", "label": "Send Email to Contact", "description": "x",
                "category": "fluent-crm", "input_schema": {"type": "object", "properties": {"email": {"type": "string"}}},
                "output_schema": {}, "meta": {"annotations": {"readonly": False, "destructive": False,
                                                              "idempotent": False}}})
SAVED = {"fluentcrm": "free", "version": "3.3.5", "pro_version": None, "saved": True, "user_id": HERMES_ID,
         "view": True, "draft": True, "test_email": False, "send": False}
TOKENS = {"colors": [{"slug": "base", "name": "Base", "value": "#F5F1EA"}, {"slug": "contrast", "name": "Contrast",
                                                                            "value": "#1F2A2E"},
                     {"slug": "accent-1", "name": "Accent", "value": "#0B6E4F"}],
          "fonts": {"body": "Manrope", "headings": "Fraunces, serif"}, "logo": None, "source": "theme.json",
          "overridden": []}
HTML = ('<html><body style="background:#f5f1ea"><div style="background:#ffffff;color:#1f2a2e">'
        '<h1>Winter news</h1><p>Hello</p><a href="https://test.example/shop/">Shop</a>'
        '<a href="##crm.unsubscribe_url##">Unsubscribe</a></div></body></html>')


class FcrmWP(FakeWP):
    """FakeWP plus FluentCRM's abilities, the connector's FluentCRM routes and FluentCRM's campaign read."""

    def __init__(self, role="wpbruecke_drafter", permissions=None):
        super().__init__(user="hermes", caps=dict(ROLE_CAPS[role]), roles=[role], password=PW,
                         namespaces=["wp/v2", "wp-abilities/v1", "wpbruecke/v1", "fluent-crm/v2"])
        self.permissions = dict(SAVED if permissions is None else permissions)
        self.campaigns = {12: {"id": 12, "title": "Winter news", "status": "draft", "created_by": HERMES_ID,
                               "email_subject": "Winter at the studio"},
                          13: {"id": 13, "title": "Anna's draft", "status": "draft", "created_by": OTHER_ID,
                               "email_subject": "Hello"}}
        self.calls: list = []       # every ability run and connector FluentCRM route, in order
        self.preview_html = HTML

    def default(self, method, parsed, headers, body):
        p = parsed.path
        data = json.loads(body.decode()) if body else None
        if p == "/wp-json/wpbruecke/v1/info":
            return self.j(200, {"version": "0.5.0", "role": "wpbruecke_drafter"})
        if p == "/wp-json/wp-abilities/v1/abilities" and method == "GET" and self.authed(headers):
            return self.j(200, LISTING, {"X-WP-Total": str(len(LISTING)), "X-WP-TotalPages": "1"})
        if p == "/wp-json/wpbruecke/v1/fluentcrm/permissions" and method == "GET":
            return self.j(200, self.permissions)
        m = re.match(r"^/wp-json/wpbruecke/v1/fluentcrm/(campaign|preview|test)$", p)
        if m and method == "POST":
            self.calls.append({"route": m.group(1), "body": data})
            if m.group(1) == "campaign":
                cid = data.get("campaign_id") or 77
                self.campaigns.setdefault(cid, {"id": cid, "title": data.get("title"), "status": "draft",
                                                "created_by": HERMES_ID, "email_subject": data.get("email_subject")})
                return self.j(200, {"ok": True, "action": "created", "campaign_id": cid, "title": data.get("title"),
                                    "created_by": HERMES_ID, "campaign": {"id": cid, "title": data.get("title")}})
            if m.group(1) == "preview":
                return self.j(200, {"ok": True, "campaign_id": data["campaign_id"], "title": "Winter news",
                                    "subject": "Winter at the studio", "rendered": True, "sent": False,
                                    "html": self.preview_html, "text": "Winter news Hello"})
            return self.j(200, {"ok": True, "sent": True, "campaign_id": data["campaign_id"],
                                "to": "the test address saved in WordPress"})
        m = re.match(r"^/wp-json/fluent-crm/v2/campaigns/(\d+)$", p)
        if m and method == "GET":
            c = self.campaigns.get(int(m.group(1)))
            return self.j(200, {"campaign": c}) if c else self.j(404, {"code": "not_found", "message": "no"})
        if p.startswith("/wp-json/wp-abilities/v1/abilities/fluent-crm/") and p.endswith("/run"):
            name = p[len("/wp-json/wp-abilities/v1/abilities/"):-len("/run")]
            query = dict(parse_qsl(parsed.query))
            self.calls.append({"ability": name, "method": method, "query": query, "body": data})
            if name == "fluent-crm/get-campaign":
                c = self.campaigns.get(int(query.get("input[campaign_id]", 0)))
                if c is None:
                    return self.j(404, {"code": "not_found", "message": "Campaign not found"})
                return self.j(200, {"recipients": {"lists": [{"id": 1, "title": "Newsletter"}], "tags": []}, **c,
                                    "recipients_estimate": {"count": 214}})
            if name == "fluent-crm/change-campaign-status":
                self.campaigns[data["input"]["campaign_id"]]["status"] = "scheduled"
                return self.j(200, {"ok": True, "status": "scheduled"})
            return self.j(200, {"ok": True, "ran": name})
        if p == "/shop/":
            return 200, {"Content-Type": "text/html"}, b"<html>shop</html>"
        return super().default(method, parsed, headers, body)

    @property
    def writes(self):
        return [c for c in self.calls if c.get("route") or (c.get("ability") or "").endswith("change-campaign-status")]


def site_with(tmp_path, packs=None, **kw):
    fake = FcrmWP(**kw)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1=PW), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                       "packs": {"fluentcrm": True} if packs is None else packs}, "person")
    return fake, rt


def run_ctx(**kw):
    return mod("lanes").RunContext(**kw)


class Turn:
    def __init__(self, rt, run=None):
        self.rt, self.run = rt, run or run_ctx()
        self.tickets = mod("hooks").Tickets()
        plugin = load_plugin()
        self.gate = mod("hooks").make_gate({"wp_pack": plugin.pack_spec(lambda: rt)}, lambda: self.run, lambda: True,
                                           self.tickets)
        self.handler = plugin.make_pack_handler(rt.get_setting, rt.store.root, lambda: self.run,
                                                secrets(WPBRUECKE_KEY1=PW), tickets=self.tickets,
                                                transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})

    def args(self, op, **input_):
        return {"site": "s", "pack": "fluentcrm", "action": "run", "op": op, "input": input_}

    def call(self, op, **input_):
        """The gate as Hermes asks it, then the handler; a card is answered with Enter (approved once)."""
        args = self.args(op, **input_)
        verdict = self.gate(tool_name="wp_pack", args=args, session_id="sess")
        if verdict and verdict["action"] == "block":
            return verdict, None
        return verdict, json.loads(self.handler(args, session_id="sess"))


def log_lines(rt):
    return [l for l in mod("activity").ActivityLog(rt.store).read("s") if l["tool"] == "wp_pack"]


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setattr(mod("packs"), "design_tokens", lambda rt, site, **kw: dict(TOKENS))
    return TOKENS


SECTIONS = [{"heading": "New courses", "text": "Hello {{contact.first_name}},\n\nthree new courses start in January.",
             "button": {"text": "See the courses", "url": "https://test.example/courses/"}}]


# --- the screen was never saved -------------------------------------------------------------------------------

@pytest.mark.parametrize("op,input_", [("crm-context", {}), ("list-campaigns", {}), ("get-campaign", {"campaign_id": 12}),
                                       ("draft-campaign", {"title": "x", "sections": SECTIONS}),
                                       ("preview-campaign", {"campaign_id": 12}), ("send-test", {"campaign_id": 12}),
                                       ("schedule-campaign", {"campaign_id": 12})])
def test_never_saved_screen_runs_nothing_and_names_it(tmp_path, op, input_):
    # FluentCRM Pro granted the user something, but the connector's screen was never saved for it
    fake, rt = site_with(tmp_path, permissions={**SAVED, "saved": False, "draft": False, "send": True})
    verdict, out = Turn(rt).call(op, **input_)
    assert verdict["action"] == "block" and "refused (needs_permission)" in verdict["message"]
    assert "Settings > WPBrücke FluentCRM" in verdict["message"] and "runs nothing" in verdict["message"]
    assert fake.calls == [] and out is None


def test_unreadable_permissions_fail_closed(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.routes[("GET", "/wp-json/wpbruecke/v1/fluentcrm/permissions")] = fake.j(500, {"code": "x", "message": "boom"})
    verdict, _ = Turn(rt).call("list-campaigns")
    assert "refused (needs_unknown)" in verdict["message"] and "WPBrücke FluentCRM" in verdict["message"]
    assert fake.calls == []


# --- default boxes: view and draft ----------------------------------------------------------------------------

def test_reads_run_with_their_default_input(tmp_path):
    fake, rt = site_with(tmp_path)
    turn = Turn(rt)
    verdict, out = turn.call("crm-context")
    assert verdict is None and out["ok"] and fake.calls[-1]["method"] == "GET"
    assert fake.calls[-1]["query"] == {"input[include_segment_counts]": "false"}   # 400 without an input (P30)
    verdict, out = turn.call("list-campaigns")
    assert out["ok"] and fake.calls[-1]["query"] == {"input[per_page]": "20"}
    assert log_lines(rt) == []


def test_new_draft_goes_through_the_route_in_the_sites_design_without_a_card(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    verdict, out = Turn(rt).call("draft-campaign", title="Winter news", email_subject="Winter at the studio",
                                 email_pre_header="Three new courses", sections=SECTIONS,
                                 recipients={"lists": ["newsletter"]})
    assert verdict is None and out["ok"], out
    sent = fake.calls[-1]
    assert sent["route"] == "campaign" and set(sent["body"]) == {"title", "email_subject", "email_pre_header", "recipients",
                                                                "email_body", "design_template", "settings"}
    body = sent["body"]["email_body"]
    assert sent["body"]["design_template"] == "simple"
    assert mod("packs.fluentcrm.compose").check_markup(body) == []
    assert "Hello {{contact.first_name}}," in body and "See the courses" in body
    assert 'style="color:#ffffff;background-color:#0b6e4f"' in body          # the accent, white text on it
    cfg = sent["body"]["settings"]["template_config"]
    assert cfg["text_color"] == "#1f2a2e" and cfg["headings_color"] == "#0b6e4f" and cfg["body_bg_color"] == "#f5f1ea"
    assert cfg["content_font_family"].startswith("Manrope, ") and cfg["headings_font_family"] == "Fraunces, serif"
    assert out["design"]["source"] == "theme.json"
    item = mod("desk").Desk(rt.store).get(out["desk_item"])
    assert item["kind"] == "campaign" and item["wp_id"] == 77 and item["title"] == "Winter news"
    assert item["links"]["edit"] == "https://test.example/wp-admin/admin.php?page=fluentcrm-admin#/email/campaigns/77"
    assert out["editor_url"] == item["links"]["edit"]
    lines = log_lines(rt)
    assert len(lines) == 1 and lines[0]["action"] == "draft-campaign" and lines[0]["label"] == "Winter news"
    assert lines[0]["wp_id"] == 77 and lines[0]["result"] == "ok"


def test_text_is_escaped_and_bad_links_never_reach_the_site(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    turn = Turn(rt)
    _, out = turn.call("draft-campaign", title="x", sections=[{"heading": "<script>alert(1)</script>", "text": "a & b"}])
    body = fake.calls[-1]["body"]["email_body"]
    assert out["ok"] and "<script>" not in body and "&lt;script&gt;" in body and "a &amp; b" in body
    before = len(fake.calls)
    for bad in ({"text": "Go", "url": "javascript:alert(1)"}, {"text": "Go"}):
        _, out = turn.call("draft-campaign", title="x", sections=[{"heading": "h", "button": bad}])
        assert out["ok"] is False and out["error"] == "route_invalid_input" and out["sent"] is False
    _, out = turn.call("draft-campaign", title="x", sections=[{"heading": "h", "image": {"url": "https://test.example/a.jpg"}}])
    assert out["error"] == "route_invalid_input" and "alt text" in out["message"]
    assert len(fake.calls) == before


@pytest.mark.parametrize("input_,why", [
    ({"title": "x", "email_body": "<p>plain HTML</p>"}, "not block markup"),
    ({"title": "x", "email_body": "<!-- wp:html -->\n<script>x</script>\n<!-- /wp:html -->"}, "core/html"),
    ({"title": "x", "sections": SECTIONS, "design_template": "raw_html"}, "simple, plain, classic"),
    ({"sections": SECTIONS}, "needs a title"),
    ({"title": "x"}, "needs sections"),
])
def test_draft_input_that_fluentcrms_editor_would_not_keep_is_refused_before_any_request(tmp_path, tokens, input_, why):
    fake, rt = site_with(tmp_path)
    _, out = Turn(rt).call("draft-campaign", **input_)
    assert out["ok"] is False and why in out["message"] and fake.writes == []


def test_an_edited_block_body_is_kept_as_sent(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    markup = "<!-- wp:paragraph -->\n<p>Edited in place</p>\n<!-- /wp:paragraph -->"
    _, out = Turn(rt).call("draft-campaign", campaign_id=12, email_body=markup)
    assert out["ok"] and fake.calls[-1]["body"]["email_body"] == markup and fake.calls[-1]["body"]["campaign_id"] == 12


# --- another person's draft (A31) -----------------------------------------------------------------------------

def test_own_draft_updates_without_a_card(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    verdict, out = Turn(rt).call("draft-campaign", campaign_id=12, sections=SECTIONS)
    assert verdict is None and out["ok"]


def test_another_persons_draft_asks_with_a_card_and_runs_only_after_it(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    turn = Turn(rt)
    args = turn.args("draft-campaign", campaign_id=13, sections=SECTIONS, email_subject="New subject")
    verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
    assert verdict["action"] == "approve"
    assert verdict["message"].startswith("Overwrites another person's draft (WordPress user id 9, not the Hermes user): "
                                         "campaign \"Anna's draft\" (id 13, draft).")
    assert "Saving replaces that person's draft" in verdict["message"] and "New subject" in verdict["message"]
    assert fake.writes == []
    out = json.loads(turn.handler(args, session_id="sess"))
    assert out["ok"] and fake.writes[-1]["body"]["campaign_id"] == 13
    again = json.loads(turn.handler(args, session_id="sess"))      # no card answered for this call
    assert again["error"] == "not_approved" and len(fake.writes) == 1


def test_a_draft_whose_owner_cannot_be_read_asks(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    verdict, _ = Turn(rt).call("draft-campaign", campaign_id=404, title="x", sections=SECTIONS)
    assert verdict["action"] == "approve"
    assert verdict["message"].startswith("Overwrites a draft whose owner WPBrücke could not read (it may be another "
                                         "person's, not the Hermes user's)")


# --- a long title never hides the decision (review row 2) ------------------------------------------------------

LONG = ("Spring news " + "x" * 1990)[:2000]
CLI_LINES, CLI_COLS = 6, 66           # what a chat client shows of a card before it cuts it (F5)


def first_sentence(msg: str) -> str:
    return msg.split("\n", 1)[0]


def assert_decision_first(msg: str, *words: str) -> None:
    for w in words:
        assert 0 <= msg.find(w) < 300, (w, msg[:400])
    assert len(first_sentence(msg)) <= CLI_LINES * CLI_COLS, len(first_sentence(msg))
    assert "x" * 200 not in msg                                    # the title is cut everywhere on the card


def test_another_persons_draft_with_a_2000_character_title_states_the_overwrite_first(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    fake.campaigns[14] = {"id": 14, "title": LONG, "status": "draft", "created_by": OTHER_ID, "email_subject": LONG}
    turn = Turn(rt)
    verdict = turn.gate(tool_name="wp_pack", args=turn.args("draft-campaign", campaign_id=14, sections=SECTIONS,
                                                           email_subject=LONG), session_id="sess")
    assert verdict["action"] == "approve"
    assert_decision_first(verdict["message"], "Overwrites another person's draft", "not the Hermes user",
                          "Spring news x")
    assert fake.writes == []


def test_schedule_with_a_2000_character_title_and_list_name_states_the_send_first(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    fake.campaigns[15] = {"id": 15, "title": LONG, "status": "draft", "created_by": HERMES_ID, "email_subject": LONG,
                          "recipients": {"lists": [{"id": 2, "title": LONG}, {"id": 3, "title": "Second"}], "tags": []}}
    turn = Turn(rt)
    verdict = turn.gate(tool_name="wp_pack", args=turn.args("schedule-campaign", campaign_id=15,
                                                           scheduled_at="2026-12-01 09:00:00"), session_id="sess")
    assert verdict["action"] == "approve"
    assert_decision_first(verdict["message"], "Sends real email to about 214 subscribed contacts",
                          "at 2026-12-01 09:00:00", "Spring news x")


# --- preview: the proof ---------------------------------------------------------------------------------------

def fake_capture(calls):
    def capture(url, *, host, widths, extra_hosts, out_dir, chromium_path="", **kw):
        calls.append({"url": url, "host": host, "widths": list(widths), "extra": list(extra_hosts)})
        rows = {}
        for w in widths:
            (out_dir / f"{w}.png").write_bytes(b"\x89PNG fake")
            rows[w] = {"file": f"{w}.png", "status": 200, "height": 900, "page_height": 900, "overflow_px": 0,
                       "console_errors": [], "failed_requests": [], "images": [], "links": [], "links_checked": True,
                       "contrast": [], "contrast_checked": 6, "contrast_unknown": 0, "lcp_ms": 300, "cls": 0}
        return {"url": url, "chromium": "HeadlessChrome/fake", "widths": rows, "blocked_hosts": [], "seconds": 0.1}
    return capture


def test_preview_renders_fluentcrms_html_at_600_and_390_and_files_the_proof(tmp_path, tokens, monkeypatch):
    fake, rt = site_with(tmp_path)
    shots = []
    monkeypatch.setattr(mod("proof.runner"), "capture", fake_capture(shots))
    turn = Turn(rt)
    _, drafted = turn.call("draft-campaign", title="Winter news", sections=SECTIONS)
    verdict, out = turn.call("preview-campaign", campaign_id=drafted["result"]["campaign_id"])
    assert verdict is None and out["ok"], out
    assert shots[0]["widths"] == [600, 390] and shots[0]["host"] == "127.0.0.1" and "test.example" in shots[0]["extra"]
    proof = out["result"]["proof"]
    assert proof["verdict"] == "match", proof
    assert [s["width"] for s in proof["shots"]] == [600, 390]
    rows = {c["check"]: c for c in proof["checks"]}
    assert rows["broken_links"]["detail"].startswith("1 link to the site answered")
    assert rows["contrast"]["verdict"] == "match" and rows["images_load"]["verdict"] == "match"
    item = mod("desk").Desk(rt.store).get(out["desk_item"])
    assert item["proof_id"] == proof["proof_id"] and item["id"] == drafted["desk_item"]
    assert "<html" not in json.dumps(out)                      # the HTML stays out of the model's context


def test_preview_with_a_broken_link_differs(tmp_path, monkeypatch):
    fake, rt = site_with(tmp_path)
    fake.preview_html = HTML.replace("https://test.example/shop/", "https://test.example/gone/")
    monkeypatch.setattr(mod("proof.runner"), "capture", fake_capture([]))
    _, out = Turn(rt).call("preview-campaign", campaign_id=12)
    rows = {c["check"]: c for c in out["result"]["proof"]["checks"]}
    assert rows["broken_links"]["verdict"] == "differs" and "HTTP 404" in rows["broken_links"]["detail"]
    assert out["result"]["proof"]["verdict"] == "differs"


def test_email_link_rules():
    render = mod("packs.fluentcrm.render")
    row = render.link_row(["", "#", "##crm.unsubscribe_url##", "mailto:a@b.test", "{{crm.x}}",
                           "https://other.example/", "https://test.example/?fluentcrm=1&route=x"],
                          "test.example", lambda url: 200)
    assert row["verdict"] == "differs" and "(empty) (no target)" in row["detail"] and "unresolved SmartCode" in row["detail"]
    assert "1 link to other hosts not opened" in row["detail"] and "1 FluentCRM action link not opened" in row["detail"]


# --- test emails ----------------------------------------------------------------------------------------------

def test_send_test_needs_the_test_box(tmp_path):
    fake, rt = site_with(tmp_path)
    verdict, _ = Turn(rt).call("send-test", campaign_id=12)
    assert verdict["action"] == "block" and '"test_email"' in verdict["message"]
    assert 'It is the box "Send test emails"' in verdict["message"] and fake.calls == []


def test_send_test_goes_to_the_route_and_takes_no_address(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "test_email": True})
    turn = Turn(rt)
    verdict, out = turn.call("send-test", campaign_id=12)
    assert verdict is None and out["ok"] and fake.calls[-1] == {"route": "test", "body": {"campaign_id": 12}}
    verdict, out = turn.call("send-test", campaign_id=12, to_email="someone@else.test")
    assert verdict["action"] == "block" and "does not take to_email" in verdict["message"]
    assert len(fake.calls) == 1


# --- schedule: the card every time, never unattended ----------------------------------------------------------

def test_schedule_is_refused_while_the_send_box_is_off(tmp_path):
    fake, rt = site_with(tmp_path)
    verdict, _ = Turn(rt).call("schedule-campaign", campaign_id=12, scheduled_at="2026-12-01 09:00:00")
    assert verdict["action"] == "block" and "refused (needs_permission)" in verdict["message"]
    assert 'It is the box "Schedule and send campaigns"' in verdict["message"]
    assert "Settings > WPBrücke FluentCRM" in verdict["message"] and fake.calls == []
    assert log_lines(rt)[-1]["code"] == "needs_permission"


def test_schedule_shows_the_card_every_time_and_runs_only_after_enter(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    turn = Turn(rt)
    args = turn.args("schedule-campaign", campaign_id=12, scheduled_at="2026-12-01 09:00:00")
    keys = []
    for _ in range(2):
        verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
        assert verdict["action"] == "approve"
        keys.append(verdict["rule_key"])
        msg = verdict["message"]
        # The decision fits the first sentence: the CLI shows only the card's first wrapped lines.
        first = msg.split("\n", 1)[0]
        assert first.startswith("Sends real email to about 214 subscribed contacts (Newsletter) at 2026-12-01 09:00:00")
        assert 'campaign "Winter news" (id 12, draft)' in first
        assert msg.split("\n")[1] == "To stop it, unschedule it in FluentCRM before the send time."
        assert ("This runs FluentCRM op schedule-campaign on s (https://test.example) through ability "
                "fluent-crm/change-campaign-status. WPBrücke asks for every run of this op.") in msg
    assert keys[0] != keys[1]
    assert not [c for c in fake.calls if c.get("ability", "").endswith("change-campaign-status")]
    out = json.loads(turn.handler(args, session_id="sess"))
    assert out["ok"], out
    run = [c for c in fake.calls if c.get("ability", "").endswith("change-campaign-status")]
    assert len(run) == 1 and run[0]["body"] == {"input": {"campaign_id": 12, "scheduled_at": "2026-12-01 09:00:00",
                                                          "action": "schedule"}}
    assert json.loads(turn.handler(args, session_id="sess"))["error"] == "not_approved"


def test_schedule_without_a_time_says_it_sends_at_once(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    verdict, _ = Turn(rt).call("schedule-campaign", campaign_id=12)
    assert verdict["message"].startswith("Sends real email to about 214 subscribed contacts (Newsletter) immediately, "
                                         "as soon as you approve")


@pytest.mark.parametrize("run", [{"cron": True}, {"single_query": True}, {"platform": "api_server"}])
def test_schedule_is_refused_unattended_before_any_request(tmp_path, run):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    turn = Turn(rt, run_ctx(**run))
    verdict, _ = turn.call("schedule-campaign", campaign_id=12)
    assert verdict["action"] == "block" and "refused (unattended)" in verdict["message"] and fake.calls == []
    out = json.loads(turn.handler(turn.args("schedule-campaign", campaign_id=12), session_id="sess"))
    assert out["error"] == "unattended" and fake.calls == []


@pytest.mark.parametrize("input_", [{"campaign_id": 12, "action": "delete"}, {"campaign_id": 12, "sending_type": "range_schedule"}])
def test_schedule_cannot_become_another_status_change(tmp_path, input_):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    verdict, _ = Turn(rt).call("schedule-campaign", **input_)
    assert verdict["action"] == "block" and "refused (bad_args)" in verdict["message"] and fake.calls == []


def test_the_scheduled_campaign_reads_back_on_its_desk_item(tmp_path, tokens):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "send": True})
    turn = Turn(rt)
    _, drafted = turn.call("draft-campaign", title="Winter news", sections=SECTIONS)
    cid = drafted["result"]["campaign_id"]
    args = turn.args("schedule-campaign", campaign_id=cid, scheduled_at="2026-12-01 09:00:00")
    turn.gate(tool_name="wp_pack", args=args, session_id="sess")
    assert json.loads(turn.handler(args, session_id="sess"))["ok"]
    item = mod("desk").Desk(rt.store).get(drafted["desk_item"])
    assert item["state"] == "scheduled" and item["live_status"] == "scheduled"


def test_only_the_packs_abilities_run(tmp_path):
    fake, rt = site_with(tmp_path)
    got = mod("abilities").run_declared(rt, rt.site("s"), "fluent-crm/send-email-to-contact", {"email": "a@b.test"})
    assert got.error == "ability_not_declared" and fake.calls == []
    spec = mod("packs").get("fluentcrm")
    assert sorted(spec.ops) == ["crm-context", "draft-campaign", "get-campaign", "list-campaigns", "preview-campaign",
                                "schedule-campaign", "send-test"]
    assert {op.ability for op in spec.ops.values() if op.ability} == {
        "fluent-crm/get-crm-context", "fluent-crm/list-campaigns", "fluent-crm/get-campaign",
        "fluent-crm/change-campaign-status"}


def test_level_drafts_is_enough_and_the_reader_level_is_not(tmp_path, tokens):
    fake, rt = site_with(tmp_path, role="wpbruecke_reader")
    verdict, _ = Turn(rt).call("draft-campaign", title="x", sections=SECTIONS)
    assert verdict["action"] == "block" and "refused (level_read)" in verdict["message"] and fake.writes == []


# --- doctor and desk ------------------------------------------------------------------------------------------

def test_doctor_shows_the_fluentcrm_permissions(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "test_email": True})
    report = mod("tools.doctor").run(rt, "s")["sites"][0]
    assert report["fluentcrm"]["saved"] is True and report["fluentcrm"]["test_email"] is True
    line = next(l for l in report["lines"] if l.startswith("FluentCRM permissions"))
    assert line == ("FluentCRM permissions (FluentCRM 3.3.5 (free)): view campaigns on, write draft campaigns on, "
                    "send test emails on, schedule and send campaigns off")
    assert report["packs"]["fluentcrm"]["detected"] is True and report["packs"]["fluentcrm"]["needs"] is None


def test_doctor_names_the_screen_when_it_was_never_saved(tmp_path):
    fake, rt = site_with(tmp_path, permissions={**SAVED, "saved": False})
    report = mod("tools.doctor").run(rt, "s")["sites"][0]
    line = next(l for l in report["lines"] if l.startswith("FluentCRM permissions"))
    assert "never saved for this user" in line and "Settings > WPBrücke FluentCRM" in line


def test_desk_reads_a_sent_campaign_back_as_published(tmp_path):
    fake, rt = site_with(tmp_path)
    desk = mod("desk").Desk(rt.store)
    item = desk.create(site="s", site_url="https://test.example", kind="campaign", wp_type="fluentcrm_campaign",
                       wp_id=12, title="Winter news")
    fake.campaigns[12]["status"] = "archived"
    assert desk.read_back(rt.client(rt.site("s")), item)["state"] == "published"


# --- the design -----------------------------------------------------------------------------------------------

def test_roles_from_a_generatepress_palette_keep_aa_contrast():
    compose = mod("packs.fluentcrm.compose")
    gp = {"colors": [{"slug": "contrast", "value": "#222222"}, {"slug": "base", "value": "#f0f0f0"},
                     {"slug": "base-3", "value": "#ffffff"}, {"slug": "accent", "value": "#1e73be"}],
          "fonts": {"body": "", "headings": ""}}
    r = compose.roles(gp)
    assert (r["text"], r["content_bg"], r["page_bg"], r["accent"], r["on_accent"]) == \
        ("#222222", "#ffffff", "#f0f0f0", "#1e73be", "#ffffff")
    assert r["body_font"] == compose.FALLBACK_FONTS


def test_a_pale_accent_is_not_used_for_text():
    compose = mod("packs.fluentcrm.compose")
    r = compose.roles({"colors": [{"slug": "accent", "value": "#ffe600"}, {"slug": "contrast", "value": "#eeeeee"}]})
    assert r["text"] == "#1a1a1a"                      # #eeeeee on white fails AA, so the dark fallback
    assert r["heading"] == "#1a1a1a" and r["link"] == "#1a1a1a" and r["on_accent"] == "#1a1a1a"
    assert compose.contrast(compose.parse_color(r["on_accent"]), compose.parse_color(r["accent"])) >= 4.5


def test_check_markup_finds_what_the_editor_would_not_keep():
    check = mod("packs.fluentcrm.compose").check_markup
    ok = "<!-- wp:paragraph -->\n<p>a</p>\n<!-- /wp:paragraph -->\n\n<!-- wp:separator -->\n<hr/>\n<!-- /wp:separator -->"
    assert check(ok) == []
    assert "text outside a block" in check(ok + "\n<p>loose</p>")[0]
    assert check("<!-- wp:paragraph -->\n<p>a</p>") == ["core/paragraph is never closed"]


# --- the connector's includes/fluentcrm.php (PHP smoke over stub WordPress and FluentCRM) ------------------------

SMOKE = ROOT.parents[1] / "experiments" / "wpbruecke" / "connector" / "php_smoke_fluentcrm.php"
CONNECTOR = ROOT / "connector" / "wpbruecke-connector"


def smoke_lines() -> list:
    out = subprocess.run([*php_command(), "-n", "-d", "error_reporting=-1", str(SMOKE), str(CONNECTOR)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "FAIL" not in out.stdout and "Warning" not in out.stdout, out.stdout[-3000:]
    return out.stdout.splitlines()


@pytest.mark.skipif(php_command() is None or not SMOKE.exists(),
                    reason="needs PHP 8 (WPB_PHP or php on PATH) and the development repository's smoke script")
def test_previews_and_test_mails_never_fill_smartcodes_from_a_customer_in_the_php_smoke():
    lines = smoke_lines()
    for must in ("own contact deleted: the preview recreates it (user_id linked, transactional)",
                 "own contact deleted: the preview greets the own contact, never the customer",
                 "own contact deleted: the test mail goes to the saved address and greets the own contact",
                 "test address of a customer: the test mail still greets the own contact",
                 "no own contact: preview and test refuse 409 wpbruecke_fcrm_no_own_contact",
                 "no own contact: nothing rendered, nothing sent",
                 "the render lends fcrm_read_contacts for that call only"):
        assert f"ok   {must}" in lines, must


@pytest.mark.skipif(php_command() is None or not SMOKE.exists(),
                    reason="needs PHP 8 (WPB_PHP or php on PATH) and the development repository's smoke script")
def test_the_campaign_route_takes_only_what_the_pack_sends_in_the_php_smoke():
    assert "ok   campaign route drops the sender, reply-to, footer and if_exists the pack never sends" in smoke_lines()


def test_the_pack_never_sends_a_sender_footer_or_if_exists(tmp_path, tokens):
    fake, rt = site_with(tmp_path)
    _, out = Turn(rt).call("draft-campaign", title="Winter news", sections=SECTIONS)
    body = fake.calls[-1]["body"]
    assert out["ok"] and "if_exists" not in body and set(body["settings"]) == {"template_config"}


def test_the_opt_in_harness_run_writes_the_tracked_preview_only_when_asked(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("harness_fcrm_under_test", ROOT / "tests" / "test_harness_fluentcrm.py")
    harness_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness_mod)
    tracked = ROOT.parents[1] / "experiments" / "wpbruecke" / "runs" / "v05-pk4-preview-600.webp"
    monkeypatch.delenv("WPB_PK4_EVIDENCE", raising=False)
    assert harness_mod.preview_webp(tmp_path).parent == tmp_path
    monkeypatch.setenv("WPB_PK4_EVIDENCE", "1")
    assert harness_mod.preview_webp(tmp_path) == tracked
