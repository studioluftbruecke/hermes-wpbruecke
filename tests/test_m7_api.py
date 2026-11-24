"""Route handlers (api.py): shapes, refusals, live read-back. The FastAPI adapter is proven live."""
import time

from wpb_fakes import FakeWP, Settings, mod, secrets

PW = secrets(WPBRUECKE_KEY1="pw pw pw")


def make(tmp_path, connector=False, **site):
    fake = FakeWP(user="admin", namespaces=["wp/v2"] + (["wpbruecke/v1"] if connector else []))
    if connector:
        fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.1.0"})
    rt = mod("runtime").Runtime(Settings(), PW, tmp_path, transport=fake, wporg_fetch=lambda u: {"version": "1"})
    rt.sites.create({"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1, **site}, "person")
    return fake, rt


def item(rt, fake, status="draft", kind="new_post"):
    post = fake.add_post(status=status, title="wpb-test")
    return mod("desk").Desk(rt.store).create(site="s", site_url="https://test.example", kind=kind, wp_type="post",
                                             wp_id=post["id"], title="wpb-test")


def test_overview_one_site_and_needs(tmp_path):
    fake, rt = make(tmp_path)
    item(rt, fake)
    status, body = mod("api").overview(rt)
    assert status == 200 and body["mode"] == "one" and body["desk_waiting"] == 1
    row = body["sites"][0]
    assert row["mark"] and row["wp_admin_url"] == "https://test.example/wp-admin/" and row["cockpit_url"] is None
    assert any(n["kind"] == "desk" for n in body["needs_you"])
    assert body["strip"]["sites"] == 1


def test_overview_empty_and_down(tmp_path):
    rt = mod("runtime").Runtime(Settings(), PW, tmp_path, transport=FakeWP(), wporg_fetch=lambda u: {})
    assert mod("api").overview(rt)[1]["mode"] == "none"
    fake, rt = make(tmp_path / "b")
    fake.down = True
    body = mod("api").overview(rt)[1]
    assert body["needs_you"][0]["kind"] == "down" and body["needs_you"][0]["urgent"]


def test_review_link_refusals_and_link(tmp_path):
    api = mod("api")
    fake, rt = make(tmp_path, connector=True)
    it = item(rt, fake)
    s, b = api.review_link(rt, it["id"], "publish")
    assert s == 403 and b["error"] == "hard_rule"
    rt.sites.update("s", {"never_publish_never_send": False}, "person")
    s, b = api.review_link(rt, it["id"], "publish")
    assert s == 403 and b["error"] == "publishing_off"
    rt.sites.update("s", {"publishing": "confirm"}, "person")
    assert api.review_link(rt, it["id"], "schedule")[1]["error"] == "publishing_off"
    assert api.review_link(rt, it["id"], "apply")[0] == 400
    assert api.review_link(rt, it["id"], "delete")[0] == 400
    s, b = api.review_link(rt, it["id"], "publish")
    assert s == 200 and b["kind"] == "review"
    assert b["url"] == f"https://test.example/wp-admin/admin.php?page=wpbruecke-review&post={it['wp_id']}&wpb_action=publish"
    assert not [r for r in fake.requests if r["method"] != "GET"]


def test_review_link_without_connector_falls_back_to_the_editor(tmp_path):
    fake, rt = make(tmp_path, never_publish_never_send=False, publishing="confirm")
    it = item(rt, fake)
    s, b = mod("api").review_link(rt, it["id"], "publish")
    assert s == 200 and b["kind"] == "editor" and b["url"] == it["links"]["edit"]


def test_desk_item_reads_the_live_status_back(tmp_path):
    fake, rt = make(tmp_path)
    it = item(rt, fake)
    fake.posts[it["wp_id"]]["status"] = "publish"
    s, b = mod("api").desk_item(rt, it["id"])
    assert s == 200 and b["item"]["state"] == "published"
    assert b["publishing"]["can_link"] is False and "Never publish" in b["publishing"]["why"]
    gone = item(rt, fake)
    del fake.posts[gone["wp_id"]]
    assert mod("api").desk_item(rt, gone["id"])[1]["item"]["live_status"] == "gone"
    assert mod("api").desk_item(rt, "item-20260101-000000-000000")[0] == 404


def test_review_changes_and_listing(tmp_path):
    api = mod("api")
    fake, rt = make(tmp_path)
    a, b = item(rt, fake), item(rt, fake, kind="staged_edit")
    assert api.desk_review(rt, a["id"])[1]["item"]["state"] == "reviewed"
    s, body = api.desk_changes(rt, b["id"], {"note": "shorter"})
    assert s == 200 and body["item"]["state"] == "changes_requested"
    assert f"@wp:s/{b['wp_id']}" in body["draft_text"] and "shorter" in body["draft_text"]
    assert api.desk_review(rt, a["id"])[0] == 409
    listed = api.desk_list(rt, kind="staged")[1]
    assert [i["id"] for i in listed["items"]] == [b["id"]] and listed["items"][0]["proof_verdict"] == "not_proven"


def test_site_routes(tmp_path):
    api = mod("api")
    fake, rt = make(tmp_path)
    assert api.site_detail(rt, "nope")[0] == 404
    s, b = api.site_patch(rt, "s", {"changes": {"label": "New"}, "preview": True})
    assert s == 200 and b["diff"] and rt.site("s")["label"] == ""
    assert api.site_patch(rt, "s", {"changes": {"publishing": "on"}})[1]["error"] == "invalid"
    assert api.site_patch(rt, "s", {})[0] == 400
    s, b = api.site_create(rt, {"fields": {"id": "t", "url": "https://t.example"}})
    assert s == 200 and b["site"]["id"] == "t"
    assert len(api.site_history(rt, "s")[1]["versions"]) == 1


def test_jobs_busy_and_done(tmp_path):
    api = mod("api")
    fake, rt = make(tmp_path)
    with rt.store.chromium_slot():
        s, b = api.job_proof(rt, {}, lambda r, body: {"ok": True})
    assert s == 409 and b["retry_after_s"] == 10
    s, b = api.job_proof(rt, {"site": "s"}, lambda r, body: {"ok": True, "seen": body["site"]})
    assert s == 202
    for _ in range(50):
        job = api.job_get(rt, b["job"]["id"])[1]["job"]
        if job["state"] == "done":
            break
        time.sleep(0.02)
    assert job["state"] == "done" and job["result"] == {"ok": True, "seen": "s"}
    s, b = api.job_seo_site(rt, {"site": "s"}, lambda r, body: 1 / 0)
    for _ in range(50):
        job = api.job_get(rt, b["job"]["id"])[1]["job"]
        if job["state"] == "failed":
            break
        time.sleep(0.02)
    assert job["error"]["error"] == "ZeroDivisionError"
    assert api.job_seo_site(rt, {}, lambda r, body: None)[0] == 400
    assert api.job_get(rt, "../x")[0] == 400


def test_an_applied_copy_reads_back_as_published_and_a_trashed_one_does_not(tmp_path):
    fake, rt = make(tmp_path)
    applied = item(rt, fake, kind="staged_edit")
    fake.posts[applied["wp_id"]]["status"] = "trash"
    fake.posts[applied["wp_id"]]["meta"] = {"_wpbruecke_applied": 1794250000}
    b = mod("api").desk_item(rt, applied["id"])[1]["item"]
    assert b["state"] == "published" and b["live_status"] == "applied"
    trashed = item(rt, fake, kind="staged_edit")
    fake.posts[trashed["wp_id"]]["status"] = "trash"
    b = mod("api").desk_item(rt, trashed["id"])[1]["item"]
    assert b["state"] == "waiting" and b["live_status"] == "trash"


def test_read_back_falls_back_to_the_public_view_when_edit_context_is_refused(tmp_path):
    fake, rt = make(tmp_path)
    it = item(rt, fake)
    fake.posts[it["wp_id"]]["status"] = "publish"
    real = fake.default

    def drafter(method, parsed, headers, body):
        if "context=edit" in (parsed.query or ""):
            return FakeWP.j(403, {"code": "rest_forbidden_context", "message": "Sorry, you are not allowed to edit this post."})
        return real(method, parsed, headers, body)
    fake.default = drafter
    assert mod("api").desk_item(rt, it["id"])[1]["item"]["state"] == "published"
