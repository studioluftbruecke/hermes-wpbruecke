import json

import pytest

from wpb_fakes import FakeWP, Multi, Settings, create_paired, mod, secrets

EDITOR = {"read": True, "edit_posts": True, "upload_files": True, "publish_posts": True, "publish_pages": True,
          "edit_published_posts": True, "edit_others_posts": True, "moderate_comments": True}


def rt_for(tmp_path, fake, key="pw pw pw", **settings):
    runtime = mod("runtime")
    wporg = lambda url: {"offers": [{"current": "7.2.0"}]} if "core" in url else {"version": "9.9"}
    return runtime.Runtime(Settings(settings), secrets(WPBRUECKE_KEY1=key), tmp_path, transport=fake, wporg_fetch=wporg)


# --- yamlish -----------------------------------------------------------------------------------

def test_yamlish_round_trip_of_a_full_site_file():
    y, sites = mod("yamlish"), mod("sites")
    site = sites.validate({"id": "test-wp", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                           "memory": {"tone": "plain: first person, \"quoted\"", "languages": ["en", "de"],
                                      "do_not_touch": ["impressum"]},
                           "automations": [{"id": "weekly", "schedule": "0 7 * * 1", "prompt": "From my notes: a draft"},
                                           {"id": "monthly", "enabled": False}]})
    text = y.dump(site, sites.HEADER)
    assert text.startswith("# WPBrücke site file")
    assert y._subset_load(text) == site
    assert y.load(text) == site


# --- sites -------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,needle", [
    ({"id": "../x", "url": "https://a.example"}, "slug"),
    ({"id": "a", "url": "ftp://a.example"}, "url"),
    ({"id": "a", "url": "https://user:pw@a.example"}, "user name"),
    ({"id": "a", "url": "https://a.example", "key_slot": 17, "login": "x"}, "key_slot"),
    ({"id": "a", "url": "https://a.example", "login": "x" * 101, "access": {"level": "admin"}}, "access.level"),
    ({"id": "a", "url": "https://a.example", "password": "x"}, "never holds a secret"),
    ({"id": "a", "url": "https://a.example", "connect_ip": "not-an-ip"}, "connect_ip"),
    ({"id": "a", "url": "https://a.example", "publishing": "off"}, "unknown field"),
    ({"id": "a", "url": "https://a.example", "proof": {"widths": [100]}}, "widths"),
    ({"id": "a", "url": "https://a.example", "colour": "red"}, "unknown"),
])
def test_invalid_site_files_are_refused_with_the_field(raw, needle):
    with pytest.raises(mod("sites").SiteError, match=needle):
        mod("sites").validate(raw)


def test_site_defaults_have_no_access_until_confirm():
    site = mod("sites").validate({"id": "a", "url": "a.example/"})
    assert site["url"] == "https://a.example"
    assert site["access"] is None and site["username"] == "" and "publishing" not in site
    assert site["proof"]["widths"] == [1440, 768, 390]


def test_versions_diff_author_and_restore(tmp_path):
    sites, store = mod("sites"), mod("store")
    ss = sites.SiteStore(store.Store(tmp_path))
    ss.create({"id": "s1", "url": "https://a.example"}, "person")
    prev = ss.update("s1", {"memory": {"tone": "plain"}}, "hermes", preview=True)
    assert prev["preview"] and prev["diff"] == ['memory.tone: "" -> "plain"']
    assert len(ss.versions("s1")) == 1  # a preview saves nothing
    res = ss.update("s1", {"memory": {"tone": "plain"}}, "hermes")
    assert res["version"] == 2 and res["author"] == "hermes"
    assert ss.get("s1")["memory"]["tone"] == "plain"
    vs = ss.versions("s1")
    assert [v["author"] for v in vs] == ["person", "hermes"] and vs[1]["summary"].startswith("memory.tone")
    ss.restore("s1", 1, "page")
    assert ss.get("s1")["memory"]["tone"] == "" and ss.versions("s1")[-1]["author"] == "page"
    with pytest.raises(sites.SiteError):
        ss.update("s1", {"id": "other"}, "hermes")
    with pytest.raises(sites.SiteError):
        ss.create({"id": "s1", "url": "https://a.example"}, "person")


def test_unchanged_update_saves_no_version(tmp_path):
    sites, store = mod("sites"), mod("store")
    ss = sites.SiteStore(store.Store(tmp_path))
    ss.create({"id": "s1", "url": "https://a.example"}, "person")
    assert ss.update("s1", {"label": ""}, "hermes")["changed"] is False
    assert len(ss.versions("s1")) == 1


# --- store -------------------------------------------------------------------------------------

def test_store_refuses_escaping_ids(tmp_path):
    store = mod("store")
    with pytest.raises(store.StoreError):
        store.check_id("../etc")
    with pytest.raises(store.StoreError):
        store.Store(tmp_path).path("sites", "../../x")


def test_chromium_slot_is_exclusive_across_holders(tmp_path):
    store = mod("store")
    s = store.Store(tmp_path)
    with s.chromium_slot():
        with pytest.raises(store.Busy):
            with store.Store(tmp_path).chromium_slot():
                pass
    with s.chromium_slot():
        pass


WRITER = """
import sys
sys.path.insert(0, {tests!r})
from wpb_fakes import mod
st = mod("store").Store({path!r})
for i in range(40):
    with st.lock("desk"):
        p = st.path("desk", "item.json")
        data = st.read_json(p, {{"n": 0}}) or {{"n": 0}}
        data["n"] += 1
        st.write_json(p, data)
"""


def test_two_processes_writing_one_item_leave_valid_json_and_no_lost_update(tmp_path):
    import pathlib
    import subprocess
    import sys
    script = tmp_path / "writer.py"
    script.write_text(WRITER.format(tests=str(pathlib.Path(__file__).resolve().parent), path=str(tmp_path / "data")))
    procs = [subprocess.Popen([sys.executable, str(script)]) for _ in range(2)]
    assert [p.wait(60) for p in procs] == [0, 0]
    data = json.loads((tmp_path / "data" / "desk" / "item.json").read_text())
    assert data["n"] == 80


# --- client ------------------------------------------------------------------------------------

def test_client_error_kinds_and_no_key_in_repr():
    client = mod("client")
    fake = FakeWP()
    fake.routes[("GET", "/wp-json/x")] = FakeWP.j(429, {"code": "too_many"}, {"Retry-After": "30"})
    c = client.WordPressClient("https://test.example", "admin", "pw pw pw", transport=fake)
    with pytest.raises(client.WPError) as e:
        c.get("/wp-json/x")
    assert e.value.kind == "rate_limited" and e.value.retry_after == 30
    with pytest.raises(client.WPError) as e:
        c.get("/wp-json/nothing")
    assert e.value.kind == "not_found" and e.value.code == "rest_no_route"
    assert "pw pw pw" not in repr(c) and "key=set" in repr(c)
    with pytest.raises(client.WPError, match="talks to test.example only"):
        c.get("https://evil.example/wp-json/")
    fake.down = True
    with pytest.raises(client.WPError) as e:
        c.get("/wp-json/")
    assert e.value.kind == "unreachable"


def test_wrong_password_is_auth_error():
    client = mod("client")
    c = client.WordPressClient("https://test.example", "admin", "wrong", transport=FakeWP())
    with pytest.raises(client.WPError) as e:
        c.get("/wp-json/wp/v2/users/me", context="edit")
    assert e.value.kind == "auth"


# --- facts, features, doctor --------------------------------------------------------------------

def test_doctor_admin_key_is_never_confirmed_and_reads_only_public_facts(tmp_path):
    fake = FakeWP()
    rt = rt_for(tmp_path, fake)
    create_paired(rt, {"id": "test-wp", "url": "https://test.example", "login": "admin", "key_slot": 1})
    assert rt.site("test-wp")["access"] is None  # an administrator is refused, so Confirm never records it
    before = len(fake.requests)
    rep = mod("tools.doctor").run(rt)
    s = rep["sites"][0]
    assert s["reachable"] and s["wp_version"] == "7.1.4" and s["theme"] == "twentytwentyfive"
    assert s["user"] is None and s["access"] == "not confirmed" and s["pairing"] == "paired"
    assert s["effective"] == "unconfirmed" and s["level"] is None and s["can_publish"] is False
    assert any("not confirmed" in line for line in s["lines"])
    assert not [r for r in fake.requests[before:] if r["auth"]]  # unconfirmed: no keyed request at all
    assert set(s["proof_extra_hosts_proposal"]) == {"stats.wp.com", "i0.wp.com"}
    assert rep["mode"] == "one"
    assert "pw pw pw" not in json.dumps(rep)


def test_doctor_contributor_key_names_dark_uploads(tmp_path):
    fake = FakeWP(user="contrib", caps={"edit_posts": True})
    rt = rt_for(tmp_path, fake)
    create_paired(rt, {"id": "t", "url": "https://test.example", "login": "contrib", "key_slot": 1})
    s = mod("tools.doctor").run(rt)["sites"][0]
    assert s["rights"] == ["can draft", "cannot upload", "cannot publish"] and s["access"] == "drafts only"
    dark = {u["feature"]: u["because"] for u in s["not_available"]}
    assert "upload" in dark["media"] and "updates" in dark
    assert not s["warnings"]


def test_doctor_empty_slot_and_public_only(tmp_path):
    rt = rt_for(tmp_path, Multi(FakeWP(), FakeWP(host="pub.example")), key=None)
    create_paired(rt, {"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1})
    rt.sites.create({"id": "pub", "url": "https://pub.example"}, "person")
    rep = mod("tools.doctor").run(rt)
    by = {s["site"]: s for s in rep["sites"]}
    assert "Site 1 · application password" in " ".join(by["t"]["lines"]) and by["t"]["pairing"] == "no_password"
    assert by["pub"]["access"] == "not set up" and "public_facts" in by["pub"]["available"]
    assert rep["mode"] == "many"


def test_doctor_unreachable_site(tmp_path):
    fake = FakeWP()
    fake.down = True
    rt = rt_for(tmp_path, fake)
    rt.sites.create({"id": "t", "url": "https://test.example", "connect_ip": "10.0.0.9"}, "person")
    s = mod("tools.doctor").run(rt)["sites"][0]
    assert not s["reachable"] and "did not answer (connect_ip 10.0.0.9)" in s["lines"][0]


def test_facts_cache_is_shared_for_five_minutes(tmp_path):
    fake = FakeWP()
    clock = [1000.0]
    runtime = mod("runtime")
    rt = runtime.Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                         wporg_fetch=lambda u: {"version": "1"}, clock=lambda: clock[0])
    site = create_paired(rt, {"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1})["site"]
    assert rt.site_facts(site)["cache"] == "fresh"
    n = len(fake.requests)
    clock[0] += 100
    assert rt.site_facts(site)["cache"] == "hit" and len(fake.requests) == n
    clock[0] += 300
    assert rt.site_facts(site)["cache"] == "fresh"


def test_updates_compare_with_wordpress_org(tmp_path):
    rt = rt_for(tmp_path, FakeWP(caps=EDITOR, roles=["editor"]))
    site = create_paired(rt, {"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1})["site"]
    ups = rt.site_facts(site)["updates"]
    assert ups["core"] == {"installed": "7.1.4", "latest": "7.2.0"}
    assert ups["plugins"] == []  # the plugin list is an administrator's read, and an administrator is refused
    assert ups["count"] == 1


# --- wp_sites ------------------------------------------------------------------------------------

def test_wp_sites_actions(tmp_path):
    rt = rt_for(tmp_path, FakeWP())
    tool = mod("tools.sites")
    r = tool.handle(rt, {"action": "add", "fields": {"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1}})
    assert r["error"] == "refused" and "Site N group" in r["message"] and rt.sites.ids() == []
    create_paired(rt, {"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1, "group": "Clients"})
    r = tool.handle(rt, {"action": "list"})
    assert r["sites"][0]["label_group"] == "Clients"  # the site's own group label survives next to Site N
    assert r["mode"] == "one" and r["sites"][0]["access"] == "not confirmed" and r["sites"][0]["noindex"] is True
    assert r["sites"][0]["group"] == 1 and r["sites"][0]["pairing"] == "paired" and r["sites"][0]["level"] is None
    assert r["sites"][0]["effective"] == "unconfirmed"
    r = tool.handle(rt, {"action": "update", "site": "t", "changes": {"connect_ip": "10.0.0.2"}, "preview": True})
    assert r["guarded"] == ["connect_ip"] and r["preview"]
    r = tool.handle(rt, {"action": "update", "site": "t", "changes": {"memory": {"tone": "warm"}}})
    assert r["ok"] and r["version"] == 2
    assert tool.handle(rt, {"action": "get", "site": "nope"})["error"] == "not_found"
    assert tool.handle(rt, {"action": "update", "site": "t", "changes": {"key_slot": 99}})["error"] == "refused"
    assert tool.touches_guarded({"action": "update", "changes": {"memory": {}, "key_slot": 2}}) == ["key_slot"]
    assert tool.handle(rt, {"action": "history", "site": "t"})["versions"][-1]["author"] == "hermes"


def test_handlers_return_json_never_a_traceback(tmp_path):
    plugin = mod("__init__") if False else __import__("wpb_fakes").load_plugin()
    h = plugin.make_sites_handler(Settings(), None)
    assert json.loads(h({"action": "list"}))["error"] == "no_state"
    h = plugin.make_doctor_handler(Settings(), tmp_path, secrets(), transport=object())
    out = json.loads(h({"site": "x"}))
    assert out["ok"] is False


def test_media_fix_names_the_drafter_role_once_the_connector_is_there():
    f = mod("features")
    site = {"id": "s", "key_slot": 2}
    facts = {"reachable": True, "rights": {"draft": True}}
    rule = next(r for r in f.FEATURES if r.key == "media")
    assert "install the connector" in f.check(rule, site, facts)
    assert "drafter role" in f.check(rule, site, {**facts, "connector": {"version": "0.1.0"}})
    assert "install the connector" not in f.check(rule, site, {**facts, "connector": {"version": "0.1.0"}})
