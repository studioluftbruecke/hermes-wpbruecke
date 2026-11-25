import json
import struct
import zlib

import pytest

from wpb_fakes import ADMIN_CAPS, FakeWP, Multi, Settings, create_paired, load_plugin, mod, secrets

CONTRIB = {"edit_posts": True}
DRAFTER = {"edit_posts": True, "upload_files": True}


def png_bytes() -> bytes:
    raw = b"\x00\xff\x00\x00"
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Run:
    """lanes.RunContext stand-in factory."""
    @staticmethod
    def attended():
        return mod("lanes").RunContext()

    @staticmethod
    def single():
        return mod("lanes").RunContext(single_query=True)

    @staticmethod
    def cron(job):
        return mod("lanes").RunContext(cron=True, cron_job_name=job)


def setup(tmp_path, caps, connector=False, **site):
    fake = FakeWP(user="hermes", caps=caps, namespaces=["wp/v2"] + (["wpbruecke/v1"] if connector else []))
    if connector:
        fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.1.0"})
    other = FakeWP(host="other.example", user="hermes", caps=caps, namespaces=["wp/v2"])
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw", WPBRUECKE_KEY2="pw pw pw"), tmp_path,
                                transport=Multi(fake, other), wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1, **site}, "person")
    return fake, rt


def wire(rt, fake, run):
    """The gate and handlers over rt's data folder and settings (each call builds its own Runtime)."""
    plugin = load_plugin()
    tickets = mod("hooks").Tickets()
    tmp_path, get, pw, fake = rt.store.root, rt.get_setting, rt.get_secret, rt.transport
    rt_factory = lambda: mod("runtime").Runtime(get, pw, tmp_path, transport=fake, wporg_fetch=lambda u: {"version": "1"})
    specs = {"wp_draft": mod("hooks").Spec(lambda a: True, lambda a, r: plugin.decide_draft(rt_factory(), a, r)),
             "wp_sites": mod("hooks").Spec(lambda a: bool(mod("tools.sites").touches_guarded(a)),
                                           lambda a, r: plugin.decide_sites(rt_factory(), a, r))}
    gate = mod("hooks").make_gate(specs, lambda: run, lambda: True, tickets)
    draft = plugin.make_draft_handler(get, tmp_path, lambda: run, pw, tickets=tickets,
                                      transport=fake, wporg_fetch=lambda u: {"version": "1"})
    sites = plugin.make_gated_sites_handler(get, tmp_path, lambda: run, pw,
                                            tickets=tickets, transport=fake, wporg_fetch=lambda u: {"version": "1"})
    return gate, draft, sites, tickets


def writes(fake):
    return [r for r in fake.requests if r["method"] in ("POST", "PUT", "DELETE")]


def test_drafts_only_key_writes_without_a_card_and_lands_on_the_desk(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    gate, draft, _, _ = wire(rt, fake, Run.attended())
    args = {"site": "s", "action": "create", "title": "Five things", "content": "# Intro\n\nFirst paragraph.\n\nSecond."}
    assert gate("wp_draft", args, "sess") is None
    out = json.loads(draft(args, session_id="sess"))
    assert out["ok"] and out["status"] == "draft" and out["desk_item"].startswith("item-")
    post = fake.posts[out["wp_id"]]
    assert '<h2 class="wp-block-heading">Intro</h2>' in post["content"]["raw"] and post["status"] == "draft"
    item = mod("desk").Desk(rt.store).get(out["desk_item"])
    assert item["kind"] == "new_post" and item["state"] == "waiting" and item["created_by"] == "hermes"
    assert item["links"]["edit"] == f"https://test.example/wp-admin/post.php?post={out['wp_id']}&action=edit"


def test_publish_status_is_refused_before_any_request(tmp_path):
    fake, rt = setup(tmp_path, ADMIN_CAPS)
    gate, draft, _, _ = wire(rt, fake, Run.attended())
    n = len(fake.requests)
    args = {"site": "s", "action": "create", "title": "x", "status": "publish"}
    verdict = gate("wp_draft", args, "sess")
    assert verdict["action"] == "block" and "no_publish" in verdict["message"]
    out = json.loads(draft(args, session_id="sess"))
    assert out["error"] == "no_publish" and out["sent"] is False
    assert len(fake.requests) == n  # not even a read


def test_a_key_that_can_publish_writes_without_a_card(tmp_path):
    # Plan D6: within the site's level no card is asked; the level checks themselves come with access.py.
    fake, rt = setup(tmp_path, {"edit_posts": True, "upload_files": True, "publish_posts": True})
    gate, draft, _, _ = wire(rt, fake, Run.attended())
    args = {"site": "s", "action": "create", "title": "Weekly notes", "content": "Text"}
    assert gate("wp_draft", args, "sess") is None
    out = json.loads(draft(args, session_id="sess"))
    assert out["ok"] and fake.posts[out["wp_id"]]["status"] == "draft"


def test_a_site_whose_group_is_not_paired_sends_no_password(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    fake.requests.clear()
    rt.get_setting["site1_url"] = "https://other.example"  # the group now points elsewhere, same password
    gate, draft, _, _ = wire(rt, fake, Run.attended())
    out = json.loads(draft({"site": "s", "action": "create", "title": "x"}, session_id="s"))
    assert out["ok"] is False and out["error"] == "no_key" and "host_changed" in out["message"]
    assert not [r for f in rt.transport.sites.values() for r in f.requests if r["auth"]]


def test_single_query_write_is_refused_before_any_write(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    gate, draft, _, _ = wire(rt, fake, Run.single())
    args = {"site": "s", "action": "create", "title": "x"}
    assert gate("wp_draft", args, "s")["action"] == "block"
    out = json.loads(draft(args, session_id="s"))
    assert out["error"] == "unattended" and "single-query" in out["message"] and not writes(fake)


def test_recurring_draft_automation_writes_only_on_its_site_and_only_with_a_safe_key(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB, automations=[{"id": "weekly", "prompt": "notes"}])
    create_paired(rt, {"id": "other", "url": "https://other.example", "login": "hermes", "key_slot": 2}, "person")
    gate, draft, _, _ = wire(rt, fake, Run.cron("wpbruecke:s:weekly"))
    ok = json.loads(draft({"site": "s", "action": "create", "title": "Week 45"}, session_id="c"))
    assert ok["ok"] and ok["lane"].startswith("unattended, allowed by automation weekly on s")
    other = json.loads(draft({"site": "other", "action": "create", "title": "x"}, session_id="c"))
    assert other["error"] == "unattended" and "belongs to site s" in other["message"]
    upd = json.loads(draft({"site": "s", "action": "stage", "id": 1}, session_id="c"))
    assert upd["error"] == "unattended" and "only create drafts" in upd["message"]
    gate2, draft2, _, _ = wire(rt, fake, Run.cron("someone-else"))
    assert json.loads(draft2({"site": "s", "action": "create", "title": "x"}, session_id="c"))["error"] == "unattended"


def test_stage_makes_a_draft_copy_and_leaves_the_live_post(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB, connector=True)
    live = fake.add_post(title="About", content="<p>old</p>")
    _, draft, _, _ = wire(rt, fake, Run.attended())
    out = json.loads(draft({"site": "s", "action": "stage", "id": live["id"], "content": "New text"}, session_id="x"))
    assert out["ok"] and out["source_id"] == live["id"] and out["live_unchanged"] is True
    copy = fake.posts[out["wp_id"]]
    assert copy["status"] == "draft" and copy["meta"]["_wpbruecke_source"] == live["id"] and "New text" in copy["content"]["raw"]
    assert fake.posts[live["id"]]["content"]["raw"] == "<p>old</p>"
    assert not [w for w in writes(fake) if w["path"] == f"/wp-json/wp/v2/posts/{live['id']}"]
    item = mod("desk").Desk(rt.store).get(out["desk_item"])
    assert item["kind"] == "staged_edit" and item["source_id"] == live["id"]


def test_update_of_a_published_post_is_refused_and_do_not_touch_holds(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB, memory={"do_not_touch": ["impressum"]})
    live = fake.add_post(title="Hello")
    imp = fake.add_post(ptype="page", title="Impressum", status="draft", slug="impressum")
    _, draft, _, _ = wire(rt, fake, Run.attended())
    assert json.loads(draft({"site": "s", "action": "update", "id": live["id"], "title": "x"}, session_id="x"))["error"] == "live_post"
    out = json.loads(draft({"site": "s", "action": "update", "type": "page", "id": imp["id"], "title": "x"}, session_id="x"))
    assert out["error"] == "do_not_touch" and not writes(fake)


def test_seo_without_connector_is_reported_not_silently_dropped(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    _, draft, _, _ = wire(rt, fake, Run.attended())
    out = json.loads(draft({"site": "s", "action": "create", "title": "x", "seo": {"description": "d"}}, session_id="x"))
    assert out["ok"] and any("connector" in n for n in out["notes"])


def test_upload_needs_an_image_and_upload_rights(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    _, draft, _, _ = wire(rt, fake, Run.attended())
    img = tmp_path / "pic.png"
    img.write_bytes(png_bytes())
    out = json.loads(draft({"site": "s", "action": "upload_media", "path": str(img), "alt": "A pic"}, session_id="x"))
    assert out["error"] == "level_no_uploads" and out["sent"] is False and not writes(fake)
    txt = tmp_path / "secret.env"
    txt.write_text("KEY=1")
    fake2, rt2 = setup(tmp_path / "b", DRAFTER)
    _, draft2, _, _ = wire(rt2, fake2, Run.attended())
    bad = json.loads(draft2({"site": "s", "action": "upload_media", "path": str(txt), "alt": "x"}, session_id="x"))
    assert bad["error"] == "bad_args" and "PNG" in bad["message"]
    good = json.loads(draft2({"site": "s", "action": "upload_media", "path": str(img), "alt": "A pic"}, session_id="x"))
    assert good["ok"] and good["media"]["alt"] == "A pic"


def test_guarded_site_fields_need_a_card_free_fields_do_not(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    gate, _, sites, _ = wire(rt, fake, Run.attended())
    free = {"action": "update", "site": "s", "changes": {"memory": {"tone": "warm"}}}
    assert gate("wp_sites", free, "x") is None and json.loads(sites(free, session_id="x"))["ok"]
    widen = {"action": "update", "site": "s", "changes": {"connect_ip": "10.0.0.7"}}
    assert json.loads(sites(widen, session_id="x"))["error"] == "not_approved"
    card = gate("wp_sites", widen, "x")
    assert card["action"] == "approve" and 'connect_ip: "" -> "10.0.0.7"' in card["message"]
    assert json.loads(sites(widen, session_id="x"))["ok"]
    gate_u, _, sites_u, _ = wire(rt, fake, Run.single())
    assert gate_u("wp_sites", {"action": "update", "site": "s", "changes": {"automations": []}}, "x")["action"] == "block"


def test_settings_guard(tmp_path):
    hooks = mod("hooks")
    home = tmp_path / "home"
    home.mkdir()
    guard = hooks.make_settings_guard(lambda: Run.attended(), lambda: True, lambda: home)
    data = home / "plugin-data" / "agent-plugin-wpbruecke-abcd1234" / "sites" / "s.yaml"
    assert guard("write_file", {"path": str(data)})["action"] == "approve"
    assert guard("terminal", {"command": f"cat {home}/config.yaml"}) is None
    assert guard("terminal", {"command": f"sed -i s/a/b/ {home}/config.yaml"})["action"] == "approve"
    assert guard("terminal", {"command": "hermes config set plugins.entries.wpbruecke.settings.agent_writes auto"})["action"] == "approve"
    assert guard("write_file", {"path": str(tmp_path / "notes.md")}) is None
    unattended = hooks.make_settings_guard(lambda: Run.single(), lambda: True, lambda: home)
    assert unattended("patch", {"path": str(home / "config.yaml")})["action"] == "block"
    off = hooks.make_settings_guard(lambda: Run.attended(), lambda: False, lambda: home)
    assert off("patch", {"path": str(home / "config.yaml")}) is None


def test_desk_state_machine(tmp_path):
    desk_mod = mod("desk")
    d = desk_mod.Desk(mod("store").Store(tmp_path))
    item = d.create(site="s", site_url="https://a.example", kind="new_post", wp_type="post", wp_id=5, title="T")
    with pytest.raises(desk_mod.DeskError, match="only the person"):
        d.move(item["id"], "review", "hermes")
    assert d.move(item["id"], "changes", "person", "shorter please")["state"] == "changes_requested"
    assert d.move(item["id"], "revise", "hermes")["state"] == "waiting"
    assert d.move(item["id"], "request_publish", "hermes")["state"] == "publish_requested"
    with pytest.raises(desk_mod.DeskError):
        d.move(item["id"], "review", "person")
    assert d.sync_live(item["id"], "publish")["state"] == "published"
    with pytest.raises(desk_mod.DeskError):
        d.update(item["id"], created_by="person")
    assert d.counts()["all"] == 0


def test_desk_tool(tmp_path):
    fake, rt = setup(tmp_path, CONTRIB)
    _, draft, _, _ = wire(rt, fake, Run.attended())
    out = json.loads(draft({"site": "s", "action": "create", "title": "x"}, session_id="x"))
    tool = mod("tools.desk")
    assert tool.handle(rt, {"action": "list"})["count"] == 1
    assert tool.handle(rt, {"action": "note", "item": out["desk_item"], "text": "proof next"})["item"]["notes"][0]["by"] == "hermes"
    r = tool.handle(rt, {"action": "request_publish", "item": out["desk_item"]})
    assert r["item"]["state"] == "publish_requested" and "publish click" in r["message"]
    fake.posts[out["wp_id"]]["status"] = "publish"
    assert tool.handle(rt, {"action": "refresh", "item": out["desk_item"]})["item"]["state"] == "published"
