"""Settings groups, the pairing of a password with its host, and the sync into site files (contract §§ 1-5)."""
import json
import pathlib

import pytest

from wpb_fakes import FakeWP, Multi, Settings, create_paired, mod, secrets

ROOT = pathlib.Path(__file__).resolve().parents[1]


def runtime(tmp_path, settings=None, transport=None, **keys):
    return mod("runtime").Runtime(Settings(settings or {}), secrets(**keys), tmp_path, transport=transport,
                                  wporg_fetch=lambda u: {"version": "1"})


# --- manifest ---------------------------------------------------------------------------------

def test_manifest_has_sixteen_groups_in_order_with_the_old_env_names():
    yaml = pytest.importorskip("yaml")
    schema = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))["config_schema"]
    keys = list(schema)
    groups = mod("settings").config_schema_groups()
    assert keys[:48] == list(groups)
    assert keys[48:] == ["chromium_path", "digest", "psi_key", "gsc_key"]
    for key, want in groups.items():
        assert {k: schema[key][k] for k in want} == want
        assert not schema[key].get("required")
    assert schema["site3_password"]["env"] == "WPBRUECKE_KEY3" and schema["site16_password"]["env"] == "WPBRUECKE_KEY16"
    assert schema["site2_url"]["label"] == "Site 2 · address"
    assert schema["site2_user"]["label"] == "Site 2 · WordPress user"
    assert schema["site2_password"]["label"] == "Site 2 · application password"
    assert "Connect a site" in schema["site1_url"]["description"]
    assert not {"key1", "agent_writes", "publishing_default"} & set(keys)


# --- Group and pairing ------------------------------------------------------------------------

def group(url="https://new.example", user="hermes", password="pw"):
    return mod("groups").make_group(3, url, user, password)


def record(host="new.example", password="pw"):
    g = mod("groups")
    return g.PairingRecord(n=3, host=host, fp=g.fingerprint(password), proven_at="2026-11-24T00:00:00Z")


def test_group_never_holds_the_password():
    g = group(url=" new.example/ ", user="  " + "u" * 120, password="secret value")
    data = json.dumps(g.to_dict())
    assert "secret value" not in data
    assert g.url == "https://new.example" and len(g.user) == 100 and g.set and len(g.fp) == 8
    assert g.env == "WPBRUECKE_KEY3" and g.error is None
    assert mod("groups").make_group(3, "ftp://x", "u", None).error and mod("groups").make_group(3, "", "", None).fp is None


@pytest.mark.parametrize("g,r,state", [
    (dict(url=""), None, "free"),
    (dict(user=""), None, "no_user"),
    (dict(password=None), None, "no_password"),
    (dict(), None, "new"),
    (dict(), dict(host="old.example"), "host_changed"),
    (dict(password="other"), dict(), "password_changed"),
    (dict(password="other"), dict(host="old.example"), "password_changed"),
    (dict(), dict(), "paired"),
])
def test_pairing_every_row(g, r, state):
    rec = record(**r) if r is not None else None
    assert mod("groups").pairing(group(**g), rec) == state


def test_host_changed_sends_no_password_anywhere(tmp_path):
    old, new = FakeWP(host="old.example", user="hermes"), FakeWP(host="new.example", user="hermes")
    rt = runtime(tmp_path, transport=Multi(old, new), WPBRUECKE_KEY1="pw pw pw")
    create_paired(rt, {"id": "s", "url": "https://old.example", "login": "hermes", "key_slot": 1})
    old.requests.clear()
    rt.get_setting["site1_url"] = "https://new.example"   # the person points Site 1 elsewhere
    rt = mod("runtime").Runtime(rt.get_setting, rt.get_secret, tmp_path, transport=rt.transport)
    site = rt.site("s")
    assert site["url"] == "https://new.example" and rt.pairing(site) == "host_changed"
    assert not rt.client(site).authenticated
    res = mod("connect").run_check(rt, 1)
    assert res["state"] == "host_changed" and "belongs to old.example" in res["message"]
    assert not [r for f in (old, new) for r in f.requests if r["auth"]]


# --- client: where a key may go ---------------------------------------------------------------

def test_a_key_never_goes_over_plain_http_except_loopback():
    client = mod("client")
    assert not client.WordPressClient("http://site.example", "u", "pw").authenticated
    assert client.WordPressClient("http://127.0.0.1:8080", "u", "pw").authenticated
    assert client.WordPressClient("http://localhost", "u", "pw").authenticated
    assert client.WordPressClient("https://site.example", "u", "pw").authenticated


def test_the_stdlib_transport_does_not_follow_a_redirect():
    import http.server
    import threading
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/wp-json/"):
                self.send_response(302)
                self.send_header("Location", "/elsewhere/")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = mod("client").WordPressClient(f"http://127.0.0.1:{server.server_port}", "u", "pw")
        resp = c.raw("GET", "/wp-json/")
        assert resp.status == 302 and resp.header("Location") == "/elsewhere/"
        assert [p for p, _ in hits] == ["/wp-json/"]
    finally:
        server.shutdown()


# --- site files -------------------------------------------------------------------------------

def test_site_file_fields_v02(tmp_path):
    sites = mod("sites")
    site = sites.validate({"id": "a", "url": "https://a.example", "login": "x" * 100 + "@mail", "key_slot": 16,
                           "automations": [{"id": "w"}]})
    assert len(site["login"]) == 100 and site["key_slot"] == 16 and site["automations"][0]["publish"] is False
    with pytest.raises(sites.SiteError, match="unknown"):
        sites.validate({"id": "a", "url": "https://a.example", "agent_writes": "auto"})


def test_two_site_files_never_share_a_host(tmp_path):
    ss = mod("sites").SiteStore(mod("store").Store(tmp_path))
    ss.create({"id": "a", "url": "https://a.example"}, "person")
    ss.create({"id": "b", "url": "https://b.example"}, "person")
    with pytest.raises(mod("sites").DuplicateHost):
        ss.create({"id": "c", "url": "https://a.example/blog"}, "person")
    with pytest.raises(mod("sites").DuplicateHost):
        ss.update("b", {"url": "https://a.example"}, "person")


def test_access_is_written_only_by_confirm(tmp_path):
    ss = mod("sites").SiteStore(mod("store").Store(tmp_path))
    ss.create({"id": "a", "url": "https://a.example"}, "person")
    grant = {"level": "drafts", "role": "contributor", "caps_fingerprint": "0123456789ab", "confirmed_at": "now"}
    with pytest.raises(mod("sites").SiteError, match="only by Confirm"):
        ss.update("a", {"access": grant}, "page")
    ss.update("a", {"access": grant}, "page", confirm=True)
    assert ss.get("a")["access"]["level"] == "drafts"
    ss.update("a", {"label": "x"}, "hermes")
    ss.restore("a", 1, "page")  # a restore keeps today's confirmation
    assert ss.get("a")["access"]["level"] == "drafts" and ss.get("a")["label"] == ""


def test_a_010_site_file_still_loads_and_its_old_fields_are_kept_once(tmp_path):
    store = mod("store").Store(tmp_path)
    (tmp_path / "sites").mkdir()
    (tmp_path / "sites" / "old.yaml").write_text(
        "id: old\nurl: https://old.example\nlogin: hermes\nkey_slot: 2\npublishing: confirm\n"
        "never_publish_never_send: false\nagent_writes: always\n", encoding="utf-8")
    rt = runtime(tmp_path)
    site = rt.site("old")
    assert site["key_slot"] == 2 and "publishing" not in site
    kept = json.loads((tmp_path / "migration" / "old.json").read_text())
    assert kept["publishing"] == "confirm" and kept["never_publish_never_send"] is False and kept["agent_writes"] == "always"
    assert store.read_json(tmp_path / "migration" / "old.json")["site"] == "old"


# --- sync -------------------------------------------------------------------------------------

def test_sync_creates_copies_attaches_and_never_deletes(tmp_path):
    rt = runtime(tmp_path, {"site1_url": "https://Blog.Example.org/", "site1_user": "hermes"})
    site = rt.site("blog-example-org")
    assert site["key_slot"] == 1 and site["login"] == "hermes" and site["url"] == "https://blog.example.org"
    assert rt.sites.versions("blog-example-org")[0]["author"] == "settings"
    rt.get_setting["site1_user"] = "hermes@blog.example.org"
    rt.sync()
    assert rt.site("blog-example-org")["login"] == "hermes@blog.example.org"
    rt.sites.create({"id": "shop", "url": "https://shop.example"}, "page")
    rt.get_setting.update(site2_url="https://shop.example", site2_user="h")
    rt.sync()
    assert rt.site("shop")["key_slot"] == 2 and rt.sites.ids() == ["blog-example-org", "shop"]
    rt.get_setting.update(site1_url="", site1_user="")
    rt.sync()
    assert rt.site("blog-example-org")["key_slot"] == 1  # never deleted; the page finishes removing


def test_sync_refuses_a_second_group_on_the_same_host_and_numbers_ids(tmp_path):
    rt = runtime(tmp_path, {"site1_url": "https://a.example", "site1_user": "u",
                            "site2_url": "https://a.example/other", "site2_user": "u"})
    assert [s["key_slot"] for s in rt.sites.all()] == [1]
    assert rt.sync_problems == [{"group": 2, "error": "duplicate", "host": "a.example"}]
    assert mod("groups").site_id_for("a.example", {"a-example"}) == "a-example-2"


# --- wp_sites: keyed fields come from the settings ----------------------------------------------

def test_wp_sites_refuses_access_and_settings_fields_but_adds_public_sites(tmp_path):
    rt = runtime(tmp_path, transport=FakeWP(), WPBRUECKE_KEY1="pw pw pw")
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1})
    tool = mod("tools.sites")
    grant = {"level": "publish", "role": "editor", "caps_fingerprint": "0123456789ab"}
    for changes in ({"access": grant}, {"access": None}, {"username": "x"}, {"url": "https://evil.example"},
                    {"login": "root"}, {"key_slot": 2}):
        out = tool.handle(rt, {"action": "update", "site": "s", "changes": changes})
        assert out["error"] == "refused" and "Nothing was saved" in out["message"], changes
    assert tool.handle(rt, {"action": "remove", "site": "s"})["error"] == "refused"
    assert tool.handle(rt, {"action": "add", "fields": {"id": "p", "url": "https://p.example", "access": grant}})["error"] == "refused"
    assert tool.handle(rt, {"action": "add", "fields": {"id": "p", "url": "https://p.example"}})["ok"]
    assert tool.handle(rt, {"action": "update", "site": "p", "changes": {"key_slot": 3}})["error"] == "refused"
    assert tool.handle(rt, {"action": "update", "site": "p", "changes": {"url": "https://q.example"}})["ok"]
    assert tool.touches_guarded({"action": "update", "changes": {"proof": {"extra_hosts": ["cdn.example"]}}}) == \
        ["proof.extra_hosts"]
    assert rt.site("s")["access"] is None


def test_the_gate_blocks_a_refused_sites_call_before_any_card(tmp_path):
    plugin = __import__("wpb_fakes").load_plugin()
    rt = runtime(tmp_path, transport=FakeWP(), WPBRUECKE_KEY1="pw pw pw")
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1})
    d = plugin.decide_sites(rt, {"action": "update", "site": "s", "changes": {"url": "https://x.example"}},
                            mod("lanes").RunContext())
    assert d["verdict"] == "block" and d["code"] == "refused"
