"""M3: the access level enforced through the tool handlers, the routes and the tick (contract §§ 6 to 8, 10)."""
import json
import struct
import zlib

import pytest

from wpb_fakes import ROLE_CAPS, FakeWP, Settings, create_paired, load_plugin, mod, secrets

ALL = ("subscriber", "wpbruecke_reader", "contributor", "wpbruecke_drafter", "author", "editor", "administrator")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The live-role cache's clock, moved by hand (``clock[0] += 61``)."""
    now = [1000.0]
    monkeypatch.setattr(mod("enforce"), "_clock", lambda: now[0])
    return now


def png(tmp_path):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    path = tmp_path / "pic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))
    return str(path)


def as_role(fake, role):
    fake.caps, fake.roles = dict(ROLE_CAPS[role]), [role]


def setup(tmp_path, role, confirmed=True, **site):
    fake = FakeWP(user="hermes", namespaces=["wp/v2", "wpbruecke/v1"])
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.2.1"})
    as_role(fake, role)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1, **site}, "person",
                  confirmed=confirmed)
    return fake, rt


def run_ctx(**kw):
    return mod("lanes").RunContext(**kw)


def handlers(rt, run=None):
    plugin = load_plugin()
    run = run or run_ctx()
    kw = dict(transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})
    tickets = mod("hooks").Tickets()
    draft = plugin.make_draft_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret, tickets=tickets, **kw)
    content = plugin.make_content_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    gate = mod("hooks").make_gate({"wp_draft": mod("hooks").Spec(
        lambda a: True, lambda a, r: plugin.decide_draft(mod("runtime").Runtime(rt.get_setting, rt.get_secret,
                                                                                rt.store.root, **kw), a, r))},
        lambda: run, lambda: True, tickets)
    return (lambda a: json.loads(draft(a, session_id="x"))), (lambda a: json.loads(content(a))), gate


def writes(fake):
    return [r for r in fake.requests if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]


def try_everything(tmp_path, fake, rt):
    """create, upload, publish, schedule, apply in turn; the error code (or 'ok') of each."""
    draft, _, _ = handlers(rt)
    out = {}
    created = draft({"site": "s", "action": "create", "title": "wpb-test v02 draft", "content": "Hello"})
    out["create"] = "ok" if created.get("ok") else created["error"]
    up = draft({"site": "s", "action": "upload_media", "path": png(tmp_path), "alt": "A pic"})
    out["upload"] = "ok" if up.get("ok") else up["error"]
    target = created.get("wp_id") or fake.add_post(status="draft", title="wpb-test v02 x")["id"]
    pub = draft({"site": "s", "action": "publish", "id": target})
    out["publish"] = "ok" if pub.get("ok") else pub["error"]
    later = fake.add_post(status="draft", title="wpb-test v02 later")["id"]
    sch = draft({"site": "s", "action": "schedule", "id": later, "date": "2030-01-02T09:00"})
    out["schedule"] = "ok" if sch.get("ok") else sch["error"]
    live = fake.add_post(title="wpb-test v02 live", content="<p>old</p>")
    staged = draft({"site": "s", "action": "stage", "id": live["id"], "content": "New text"})
    item = staged.get("desk_item")
    if item is None:
        copy = fake.add_post(status="draft", title="wpb-test v02 live", content="<p>new</p>",
                             meta={"_wpbruecke_source": live["id"]})
        ap = draft({"site": "s", "action": "apply", "id": copy["id"]})
    else:
        ap = draft({"site": "s", "action": "apply", "item": item})
    out["apply"] = "ok" if ap.get("ok") else ap["error"]
    return out


EXPECT = {
    "subscriber": dict(create="level_read", upload="level_read", publish="level_read", schedule="level_read",
                       apply="level_read"),
    "wpbruecke_reader": dict(create="level_read", upload="level_read", publish="level_read", schedule="level_read",
                             apply="level_read"),
    "contributor": dict(create="ok", upload="level_no_uploads", publish="level_drafts", schedule="level_drafts",
                        apply="level_drafts"),
    "wpbruecke_drafter": dict(create="ok", upload="ok", publish="level_drafts", schedule="level_drafts",
                              apply="level_drafts"),
    "author": dict(create="ok", upload="ok", publish="ok", schedule="ok", apply="ok"),
    "editor": dict(create="ok", upload="ok", publish="ok", schedule="ok", apply="ok"),
    "administrator": dict(create="level_unconfirmed", upload="level_unconfirmed", publish="level_unconfirmed",
                          schedule="level_unconfirmed", apply="level_unconfirmed"),
}
LEVEL = {"subscriber": "read_public", "wpbruecke_reader": "read", "contributor": "drafts",
         "wpbruecke_drafter": "drafts", "author": "publish", "editor": "publish", "administrator": None}


@pytest.mark.parametrize("role", ALL)
def test_each_role_may_do_exactly_its_level(tmp_path, role):
    fake, rt = setup(tmp_path, role)
    site = rt.site("s")
    assert (site["access"] or {}).get("level") == LEVEL[role]  # an administrator is never confirmable
    got = try_everything(tmp_path, fake, rt)
    assert got == EXPECT[role]
    if role in ("subscriber", "wpbruecke_reader", "administrator"):
        assert not writes(fake)  # refused before any write request


@pytest.mark.parametrize("role,ok", [("subscriber", False), ("wpbruecke_reader", True), ("administrator", False)])
def test_keyed_reads_need_the_read_level(tmp_path, role, ok):
    fake, rt = setup(tmp_path, role)
    fake.add_post(status="draft", title="wpb-test v02 secret draft")
    _, content, _ = handlers(rt)
    out = content({"site": "s", "action": "list", "status": "draft"})
    if ok:
        assert out["ok"] and out["total"] == 1
    else:
        assert out["ok"] is False and out["error"] == {"subscriber": "level_public",
                                                       "administrator": "level_unconfirmed"}[role]
        drafts = [r for r in fake.requests if r["path"] == "/wp-json/wp/v2/posts" and r["query"].get("status") == ["draft"]]
        assert not drafts
    assert content({"site": "s", "action": "list"})["ok"]  # what the public sees always works


def test_unconfirmed_site_sends_no_password_at_all(tmp_path):
    fake, rt = setup(tmp_path, "editor", confirmed=False)
    fake.requests.clear()
    draft, content, _ = handlers(rt)
    assert draft({"site": "s", "action": "create", "title": "x"})["error"] == "level_unconfirmed"
    assert content({"site": "s", "action": "list"})["ok"]
    mod("tools.doctor").run(rt)
    assert not [r for r in fake.requests if r["auth"]]


def test_a_wider_role_after_confirm_stops_the_site(tmp_path, clock):
    fake, rt = setup(tmp_path, "contributor")
    draft, content, gate = handlers(rt)
    assert draft({"site": "s", "action": "create", "title": "wpb-test v02 a"})["ok"]
    as_role(fake, "editor")
    clock[0] += 6  # past the write cache; a write reads the role again
    n = len(writes(fake))
    out = draft({"site": "s", "action": "create", "title": "wpb-test v02 b"})
    assert out["error"] == "level_stopped" and "more rights" in out["message"] and out["sent"] is False
    verdict = gate("wp_draft", {"site": "s", "action": "create", "title": "c"}, "x")
    assert verdict["action"] == "block" and "level_stopped" in verdict["message"]
    assert len(writes(fake)) == n
    clock[0] += 61
    before = len(fake.requests)
    assert content({"site": "s", "action": "list", "status": "draft"})["error"] == "level_stopped"
    keyed = [r for r in fake.requests[before:] if r["auth"]]
    assert [r["path"] for r in keyed] == ["/wp-json/wp/v2/users/me"]  # only the role is read, nothing else keyed


def test_same_rank_with_other_capabilities_stops(tmp_path, clock):
    fake, rt = setup(tmp_path, "wpbruecke_drafter")
    fake.caps = {**fake.caps, "edit_others_posts": True}  # still drafts, but more than confirmed
    clock[0] += 6
    out = handlers(rt)[0]({"site": "s", "action": "create", "title": "x"})
    assert out["error"] == "level_stopped" and "rights changed" in out["message"] and not writes(fake)


def test_a_narrower_role_lowers_the_level_and_keeps_working(tmp_path, clock):
    fake, rt = setup(tmp_path, "editor")
    as_role(fake, "wpbruecke_drafter")
    clock[0] += 6
    draft, _, _ = handlers(rt)
    created = draft({"site": "s", "action": "create", "title": "wpb-test v02 low"})
    assert created["ok"]
    out = draft({"site": "s", "action": "publish", "id": created["wp_id"]})
    assert out["error"] == "level_drafts" and fake.posts[created["wp_id"]]["status"] == "draft"
    st = rt.standing(rt.site("s"))
    assert st.effective == "lowered" and st.level == "drafts" and st.row()["level"] == "publish"


def test_reader_with_the_connector_off_is_stopped(tmp_path, clock):
    fake, rt = setup(tmp_path, "wpbruecke_reader")
    assert rt.site("s")["access"]["level"] == "read"
    fake.caps, fake.roles = {}, []  # P23: deactivating the connector removes the role
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(404, {"code": "rest_no_route", "message": "no"})
    clock[0] += 61
    st = rt.standing(rt.site("s"))
    assert st.effective == "stopped" and "connector" in st.stop_reason
    out = handlers(rt)[1]({"site": "s", "action": "list", "status": "draft"})
    assert out["error"] == "level_stopped" and "connector" in out["message"]


def test_check_after_widening_to_administrator_reports_stopped(tmp_path, clock):
    api = mod("api")
    fake, rt = setup(tmp_path, "wpbruecke_drafter")
    as_role(fake, "administrator")
    res = api.sites_check(rt, {"group": 1})[1]
    assert res["state"] == "stopped" and res["effective"] == "stopped" and "administrator" in res["message"]
    assert res["next"]["action"] == "confirm"
    n = len(writes(fake))
    assert handlers(rt)[0]({"site": "s", "action": "create", "title": "x"})["error"] == "level_stopped"
    assert len(writes(fake)) == n
    row = api.overview(rt)[1]
    assert row["sites"][0]["effective"] == "stopped" and "administrator" in row["sites"][0]["stop_reason"]
    assert row["needs_you"][0]["kind"] == "stopped" and row["needs_you"][0]["urgent"]


def test_revoked_password_stops_the_site(tmp_path, clock):
    fake, rt = setup(tmp_path, "editor")
    fake.password = "something else"
    clock[0] += 61
    st = rt.standing(rt.site("s"))
    assert st.effective == "stopped" and "no longer accepts" in st.stop_reason


# --- publishing: read back -----------------------------------------------------------------------

def test_publish_schedule_apply_read_back(tmp_path):
    fake, rt = setup(tmp_path, "editor")
    draft, _, gate = handlers(rt)
    assert gate("wp_draft", {"site": "s", "action": "publish", "id": 1}, "x") is None  # no card inside the level
    created = draft({"site": "s", "action": "create", "title": "wpb-test v02 pub", "content": "Body"})
    pub = draft({"site": "s", "action": "publish", "id": created["wp_id"]})
    assert pub["ok"] and pub["confirmed"] and pub["read_back"]["status"] == "publish" and pub["desk_state"] == "published"
    assert len(pub["read_back"]["content_hash"]) == 12
    again = draft({"site": "s", "action": "publish", "id": created["wp_id"]})
    assert again["error"] == "already_published"
    later = draft({"site": "s", "action": "create", "title": "wpb-test v02 later"})
    sch = draft({"site": "s", "action": "schedule", "id": later["wp_id"], "date": "2030-01-02 09:00"})
    assert sch["ok"] and sch["read_back"]["status"] == "future" and sch["read_back"]["date"] == "2030-01-02T09:00:00"
    assert sch["desk_state"] == "scheduled"
    assert draft({"site": "s", "action": "schedule", "id": later["wp_id"], "date": "soon"})["error"] == "bad_args"
    live = fake.add_post(title="wpb-test v02 live", content="<p>old</p>")
    staged = draft({"site": "s", "action": "stage", "id": live["id"], "title": "wpb-test v02 live new",
                    "content": "New text", "seo": {"description": "Fresh"}})
    copy_id = staged["wp_id"]
    ap = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert ap["ok"] and ap["confirmed"] and ap["content_matches"] and ap["copy_deleted"]
    assert ap["wp_id"] == live["id"] and copy_id not in fake.posts
    assert fake.posts[live["id"]]["title"]["raw"] == "wpb-test v02 live new"
    assert "New text" in fake.posts[live["id"]]["content"]["raw"]
    assert fake.posts[live["id"]]["meta"]["_wpbruecke_description"] == "Fresh" and ap["seo_matches"]
    assert mod("desk").Desk(rt.store).get(staged["desk_item"])["state"] == "applied"
    assert ap["applied_fields"] == ["content", "excerpt", "title", "meta._wpbruecke_description"]


def test_publishing_a_staged_copy_is_refused_use_apply(tmp_path):
    fake, rt = setup(tmp_path, "editor")
    draft, _, _ = handlers(rt)
    live = fake.add_post(title="wpb-test v02 live")
    staged = draft({"site": "s", "action": "stage", "id": live["id"], "content": "x"})
    n = len(writes(fake))
    assert draft({"site": "s", "action": "publish", "item": staged["desk_item"]})["error"] == "staged_copy"
    assert len(writes(fake)) == n


# --- unattended runs and automations (D7) ---------------------------------------------------------

def test_unattended_write_is_refused_before_any_request(tmp_path):
    fake, rt = setup(tmp_path, "editor")
    fake.requests.clear()
    draft, _, gate = handlers(rt, run_ctx(single_query=True))
    out = draft({"site": "s", "action": "publish", "id": 5})
    assert out["error"] == "unattended" and out["sent"] is False
    assert gate("wp_draft", {"site": "s", "action": "create", "title": "x"}, "x")["action"] == "block"
    assert fake.requests == []


@pytest.mark.parametrize("role,publish,expect", [
    ("editor", False, ("ok", "unattended")),
    ("editor", True, ("ok", "ok")),
    ("wpbruecke_drafter", True, ("ok", "level_drafts")),
    ("contributor", False, ("ok", "unattended")),
])
def test_automation_writes_drafts_and_publishes_only_with_publish_true(tmp_path, role, publish, expect):
    fake, rt = setup(tmp_path, role, automations=[{"id": "weekly", "prompt": "notes", "publish": publish}])
    draft, _, _ = handlers(rt, run_ctx(cron=True, cron_job_name="wpbruecke:s:weekly", cron_execution_id="exec-1"))
    created = draft({"site": "s", "action": "create", "title": "wpb-test v02 week"})
    assert (created.get("ok") and "ok") == expect[0] and fake.posts[created["wp_id"]]["status"] == "draft"
    assert created["lane"].startswith("unattended, allowed by automation weekly on s")
    pub = draft({"site": "s", "action": "publish", "id": created["wp_id"]})
    assert ("ok" if pub.get("ok") else pub["error"]) == expect[1]
    if expect[1] != "ok":
        assert fake.posts[created["wp_id"]]["status"] == "draft"
    assert draft({"site": "s", "action": "stage", "id": created["wp_id"]})["error"] == "unattended"


def automation_run(rt, execution_id):
    return handlers(rt, run_ctx(cron=True, cron_job_name="wpbruecke:s:weekly", cron_execution_id=execution_id))


def test_automation_with_publish_true_publishes_only_the_post_its_own_run_created(tmp_path):
    fake, rt = setup(tmp_path, "editor", automations=[{"id": "weekly", "prompt": "notes", "publish": True}])
    person, _, _ = handlers(rt)
    waiting = person({"site": "s", "action": "create", "title": "wpb-test v02 waits for review"})
    outside = fake.add_post(status="draft", title="wpb-test v02 made in wp-admin")["id"]
    earlier, _, _ = automation_run(rt, "exec-1")
    last_week = earlier({"site": "s", "action": "create", "title": "wpb-test v02 last week"})
    assert last_week.get("ok")
    draft, _, gate = automation_run(rt, "exec-2")
    mine = draft({"site": "s", "action": "create", "title": "wpb-test v02 this week"})
    assert mine.get("ok")
    item = mod("desk").Desk(rt.store).get(mine["desk_item"])
    assert item["automation_run"] == "wpbruecke:s:weekly#exec-2"
    n = len(writes(fake))
    for target in ({"id": waiting["wp_id"]}, {"item": waiting["desk_item"]}, {"id": outside},
                   {"id": last_week["wp_id"]}, {"item": last_week["desk_item"]}):
        out = draft({"site": "s", "action": "publish", **target})
        assert (out["error"], out["sent"]) == ("unattended", False), target
        assert out["message"].startswith("An automation publishes only the post its own run created; ")
        assert out["message"].endswith("was not created by this run of automation weekly. Nothing was sent.")
        assert gate("wp_draft", {"site": "s", "action": "publish", **target}, "x")["action"] == "block"
    assert len(writes(fake)) == n
    assert all(fake.posts[i]["status"] == "draft" for i in (waiting["wp_id"], outside, last_week["wp_id"]))
    out = draft({"site": "s", "action": "publish", "id": mine["wp_id"]})
    assert out.get("ok") and fake.posts[mine["wp_id"]]["status"] == "publish"


def test_automation_without_an_execution_id_cannot_publish(tmp_path):
    fake, rt = setup(tmp_path, "editor", automations=[{"id": "weekly", "prompt": "notes", "publish": True}])
    draft, _, _ = handlers(rt, run_ctx(cron=True, cron_job_name="wpbruecke:s:weekly"))
    made = draft({"site": "s", "action": "create", "title": "wpb-test v02 no id"})
    assert made.get("ok") and "automation_run" not in mod("desk").Desk(rt.store).get(made["desk_item"])
    out = draft({"site": "s", "action": "publish", "id": made["wp_id"]})
    assert out["error"] == "unattended" and "no execution id" in out["message"]
    assert fake.posts[made["wp_id"]]["status"] == "draft"


def test_run_context_carries_the_cron_execution_id():
    lanes = mod("lanes")

    class Exec:
        job_name, execution_id = "wpbruecke:s:weekly", "e-42"

    class Ctx:
        def current_cron_execution(self):
            return Exec()

        def session_surface(self):
            return "cron"

    run = lanes.run_context_from(Ctx())
    assert run.run_key() == "wpbruecke:s:weekly#e-42"
    assert lanes.RunContext(cron=True, cron_job_name="other", cron_execution_id="e").run_key() == ""


def test_desk_never_changes_automation_run(tmp_path):
    _, rt = setup(tmp_path, "editor")
    d = mod("desk").Desk(rt.store)
    item = d.create(site="s", site_url="https://test.example", kind="new_post", wp_type="post", wp_id=5, title="x",
                    automation_run="wpbruecke:s:weekly#e")
    with pytest.raises(mod("desk").DeskError):
        d.update(item["id"], automation_run="wpbruecke:s:weekly#other")


def test_automation_on_another_site_or_switched_off_is_refused(tmp_path):
    fake, rt = setup(tmp_path, "editor", automations=[{"id": "weekly", "enabled": False}])
    fake.requests.clear()
    off, _, _ = handlers(rt, run_ctx(cron=True, cron_job_name="wpbruecke:s:weekly"))
    assert "switched off" in off({"site": "s", "action": "create", "title": "x"})["message"]
    other, _, _ = handlers(rt, run_ctx(cron=True, cron_job_name="wpbruecke:t:weekly"))
    assert "belongs to site t" in other({"site": "s", "action": "create", "title": "x"})["message"]
    assert fake.requests == []


# --- routes ------------------------------------------------------------------------------------

def test_desk_routes_publish_schedule_apply(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path, "editor")
    draft, _, _ = handlers(rt)
    a = draft({"site": "s", "action": "create", "title": "wpb-test v02 route"})
    s, b = api.desk_act(rt, a["desk_item"], "publish")
    assert s == 200 and b["read_back"]["status"] == "publish" and b["item"]["state"] == "published"
    c = draft({"site": "s", "action": "create", "title": "wpb-test v02 route later"})
    assert api.desk_act(rt, c["desk_item"], "schedule", {"date": ""})[0] == 400
    s, b = api.desk_act(rt, c["desk_item"], "schedule", {"date": "2030-03-04T05:06"})
    assert s == 200 and b["read_back"]["status"] == "future"
    live = fake.add_post(title="wpb-test v02 live")
    st = draft({"site": "s", "action": "stage", "id": live["id"], "content": "route text"})
    assert api.desk_act(rt, a["desk_item"], "apply")[0] == 400  # not a staged copy
    s, b = api.desk_act(rt, st["desk_item"], "apply")
    assert s == 200 and b["item"]["state"] == "applied" and b["content_matches"]
    assert api.desk_act(rt, "item-20260101-000000-000000", "publish")[0] == 404
    rows = api.desk_list(rt)[1]["items"]
    assert rows and all(i["can_publish"] is True for i in rows)


def test_desk_routes_below_publish_answer_403_with_the_wordpress_link(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path, "wpbruecke_drafter")
    draft, _, _ = handlers(rt)
    a = draft({"site": "s", "action": "create", "title": "wpb-test v02 drafter"})
    n = len(writes(fake))
    s, b = api.desk_act(rt, a["desk_item"], "publish")
    assert s == 403 and b["error"] == "level_drafts" and b["ok"] is False
    assert b["review_link"].startswith("https://test.example/wp-admin/admin.php?page=wpbruecke-review")
    assert len(writes(fake)) == n
    assert api.review_link(rt, a["desk_item"], "publish")[0] == 200
    s, item = api.desk_item(rt, a["desk_item"])
    assert item["item"]["can_publish"] is False and item["publishing"]["level"] == "drafts"
    assert api.desk_list(rt)[1]["items"][0]["can_publish"] is False


def test_overview_rows_carry_the_level_fields(tmp_path):
    fake, rt = setup(tmp_path, "author")
    row = mod("api").overview(rt)[1]["sites"][0]
    assert row["level"] == "publish" and row["effective"] == "confirmed" and row["stop_reason"] is None
    assert row["level_words"] == "write and publish its own posts" and row["can_publish"] is True
    fake2, rt2 = setup(tmp_path / "b", "contributor", confirmed=False)
    body = mod("api").overview(rt2)[1]
    assert body["sites"][0]["effective"] == "unconfirmed" and body["sites"][0]["level"] is None
    assert any(n["kind"] == "confirm" for n in body["needs_you"])


def test_revoke_deletes_the_sites_own_password_and_reads_back_401(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path, "wpbruecke_drafter")
    api.sites_check(rt, {"group": 1})  # the check stores the password's uuid
    s, b = api.site_revoke(rt, "s")
    assert s == 200 and b["revoked"] is True and b["http"] == 200 and b["read_back"]["users_me"] == 401
    deleted = [r for r in fake.requests if r["method"] == "DELETE"]
    assert [r["path"] for r in deleted] == ["/wp-json/wp/v2/users/me/application-passwords/u"]
    assert "pw pw pw" not in json.dumps(b)
    assert rt.standing(rt.site("s")).effective == "stopped"
    assert api.site_revoke(rt, "nope")[0] == 404
    api.public_site_create(rt, {"url": "https://pub.example"})
    assert api.site_revoke(rt, "pub-example")[0] == 409
    rt.get_setting["site1_url"] = "https://moved.example"
    assert api.site_revoke(mod("runtime").Runtime(rt.get_setting, rt.get_secret, tmp_path, transport=fake),
                           "s")[1]["error"] == "not_paired"


# --- tick ----------------------------------------------------------------------------------------

def test_tick_reads_the_role_and_reports_a_stop_once(tmp_path):
    fake, rt = setup(tmp_path, "wpbruecke_drafter")
    tick = mod("tick")
    assert tick.run_tick(rt, now=1000)["clean"]
    as_role(fake, "editor")
    before = len(fake.requests)
    lines = tick.run_tick(rt, now=2000)["lines"]
    assert any("is stopped" in line and "more rights" in line for line in lines), lines
    keyed = [r["path"] for r in fake.requests[before:] if r["auth"]]
    assert keyed == ["/wp-json/wp/v2/users/me"]  # the role is read, nothing else keyed runs
    assert not tick.run_tick(rt, now=3000)["lines"]  # reported once
    assert not writes(fake)


def test_confirm_forgets_the_cached_reading(tmp_path, clock):
    api = mod("api")
    fake, rt = setup(tmp_path, "contributor")
    rt.standing(rt.site("s"))
    as_role(fake, "author")
    shown = api.sites_check(rt, {"group": 1})[1]
    assert shown["state"] == "stopped"
    assert api.site_confirm(rt, "s", {"level": "publish", "caps_fp": shown["caps_fp"]})[0] == 200
    created = handlers(rt)[0]({"site": "s", "action": "create", "title": "wpb-test v02 after confirm"})
    assert handlers(rt)[0]({"site": "s", "action": "publish", "id": created["wp_id"]})["ok"]


# --- proof of a draft at Read (review N1) ---------------------------------------------------------

def test_a_read_level_site_proves_a_draft_through_the_connector_preview_link(tmp_path):
    fake, rt = setup(tmp_path, "wpbruecke_reader")
    assert rt.standing(rt.site("s")).effective == "confirmed" and rt.standing(rt.site("s")).level == "read"
    pid = fake.add_post(status="draft", title="wpb-test v02 read proof")["id"]
    link = f"https://test.example/?p={pid}&wpb_preview=abc&wpb_exp=1"
    fake.routes[("POST", "/wp-json/wpbruecke/v1/preview-link")] = FakeWP.j(200, {"url": link, "expires_at": "x"})
    proof = mod("tools.proof")
    r = proof.resolve(rt, {"site": "s", "id": pid})
    assert r["url"] == link and "not_proven" not in r
    asked = [q for q in fake.requests if q["method"] == "POST"]
    assert [q["path"] for q in asked] == ["/wp-json/wpbruecke/v1/preview-link"]
    assert json.loads(asked[0]["body"]) == {"post_id": pid} and asked[0]["auth"] is True
    # Connector 0.2.0 refused the reader's preview link: the proof then says so instead of failing silently.
    fake.routes[("POST", "/wp-json/wpbruecke/v1/preview-link")] = FakeWP.j(
        403, {"code": "wpbruecke_reader_read_only", "message": "read only"})
    r = proof.resolve(rt, {"site": "s", "id": pid})
    assert r["url"] is None and r["not_proven"] == "the connector did not hand out a preview link"
