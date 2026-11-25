"""Routes of contract § 10 (GET /groups, POST /sites/check, Confirm, DELETE /sites/{id}, POST /public-sites)
and the fresh secrets in the plugin host child."""
import importlib.util
import pathlib
import sys
import types

import pytest

from wpb_fakes import FakeWP, Settings, mod, secrets

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRAFTER = {"read": True, "edit_posts": True, "upload_files": True, "wpbruecke_drafter": True}
AUTHOR = {**DRAFTER, "publish_posts": True, "edit_published_posts": True, "author": True}
SUBSCRIBER = {"read": True, "level_0": True, "subscriber": True}


def setup(tmp_path, caps=DRAFTER, roles=("wpbruecke_drafter",), **settings):
    fake = FakeWP(user="hermes", caps=caps, roles=list(roles))
    values = {"site1_url": "https://test.example", "site1_user": "hermes", **settings}
    rt = mod("runtime").Runtime(Settings(values), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake)
    return fake, rt


def test_groups_lists_sixteen_and_names_the_first_free_one(tmp_path):
    api = mod("api")
    rt = mod("runtime").Runtime(Settings(), secrets(), tmp_path)
    s, b = api.groups_view(rt, "work")
    assert s == 200 and len(b["groups"]) == 16 and b["free"] == 1 and b["used"] == 0
    assert b["settings_href"] == "/settings?tab=plugins&agent=wpbruecke&profile=work" and b["profile"] == "work"
    row = b["groups"][0]
    assert {"n", "url", "user", "set", "fp", "state", "site_id", "host", "check", "migrate"} <= set(row)
    assert row["state"] == "free" and row["check"] is None and row["migrate"] is None


def test_groups_after_a_check(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    b = api.groups_view(rt)[1]
    assert b["groups"][0]["state"] == "new" and b["free"] == 2 and b["used"] == 1
    assert b["groups"][0]["site_id"] == "test-example" and b["groups"][0]["fp"] and b["groups"][0]["set"]
    api.sites_check(rt, {"group": 1})
    row = api.groups_view(rt)[1]["groups"][0]
    assert row["state"] == "paired" and row["check"]["state"] == "needs_confirm"
    assert "pw pw pw" not in str(api.groups_view(rt)[1])


def test_check_route_codes(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    assert api.sites_check(rt, {})[0] == 400
    assert api.sites_check(rt, {"group": 17})[0] == 404
    s, b = api.sites_check(rt, {"group": 5})
    assert s == 200 and b["state"] == "free"
    s, b = api.sites_check(rt, {"group": 1}, "work")
    assert s == 200 and b["state"] == "needs_confirm"


def test_confirm_records_the_level_only_when_it_still_matches(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    shown = api.sites_check(rt, {"group": 1})[1]
    s, b = api.site_confirm(rt, "test-example", {"level": "drafts", "caps_fp": "000000000000"})
    assert s == 409 and b["error"] == "changed" and rt.site("test-example")["access"] is None
    s, b = api.site_confirm(rt, "test-example", {"level": "publish", "caps_fp": shown["caps_fp"]})
    assert s == 409
    s, b = api.site_confirm(rt, "test-example", {"level": "drafts", "caps_fp": shown["caps_fp"]})
    assert s == 200 and b["state"] == "confirmed" and b["effective"] == "confirmed" and b["ok"]
    access = rt.site("test-example")["access"]
    assert access["level"] == "drafts" and access["role"] == "wpbruecke_drafter" and access["caps_fingerprint"] == shown["caps_fp"]
    assert rt.sites.versions("test-example")[-1]["author"] == "page"
    # someone promotes the user: the site stops until the person confirms the wider level
    fake.caps, fake.roles = AUTHOR, ["author"]
    wider = api.sites_check(rt, {"group": 1})[1]
    assert wider["state"] == "stopped" and wider["effective"] == "stopped" and "more rights" in wider["message"]
    assert wider["next"]["action"] == "confirm"
    s, b = api.site_confirm(rt, "test-example", {"level": "publish", "caps_fp": wider["caps_fp"]})
    assert s == 200 and b["state"] == "confirmed" and b["level"] == "publish"
    # a narrower role lowers the level on its own
    fake.caps, fake.roles = SUBSCRIBER, ["subscriber"]
    low = api.sites_check(rt, {"group": 1})[1]
    assert low["state"] == "lowered" and low["effective"] == "lowered" and low["ok"]


def test_confirm_refusals(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    assert api.site_confirm(rt, "nope", {"level": "drafts", "caps_fp": "x"})[0] == 404
    assert api.site_confirm(rt, "test-example", {"level": "admin", "caps_fp": "x"})[0] == 400
    api.public_site_create(rt, {"url": "https://pub.example"})
    assert api.site_confirm(rt, "pub-example", {"level": "read_public", "caps_fp": "x"})[0] == 409


def test_remove_waits_for_the_group_to_be_empty_and_keeps_history(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    s, b = api.site_delete(rt, "test-example")
    assert s == 409 and b["error"] == "group_filled" and "Site 1 · address" in b["message"]
    rt.get_setting.update(site1_url="", site1_user="")
    row = api.groups_view(rt)[1]["groups"][0]
    assert row["state"] == "group_empty" and row["site_id"] == "test-example"
    res = api.sites_check(rt, {"group": 1})[1]
    assert res["state"] == "group_empty" and res["next"]["action"] == "remove"
    s, b = api.site_delete(rt, "test-example")
    assert s == 200 and b["removed"] and rt.sites.ids() == []
    assert (tmp_path / "sites" / "test-example.history").is_dir()
    assert api.groups_view(rt)[1]["groups"][0]["state"] == "free"
    assert api.site_delete(rt, "test-example")[0] == 404


def test_public_sites(tmp_path):
    api = mod("api")
    fake, rt = setup(tmp_path)
    s, b = api.public_site_create(rt, {"url": "news.example", "connect_ip": "10.0.0.5"})
    assert s == 200 and b["site"]["id"] == "news-example" and b["site"]["key_slot"] == 0
    assert b["site"]["connect_ip"] == "10.0.0.5"
    assert api.public_site_create(rt, {"url": "https://news.example/"})[0] == 409
    assert api.public_site_create(rt, {"url": "https://test.example"})[0] == 409
    assert api.public_site_create(rt, {"url": "ftp://x"})[0] == 400
    assert api.site_delete(rt, "news-example")[0] == 200


# --- dashboard adapter ------------------------------------------------------------------------

def load_api():
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("wpb_plugin_api_v02", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


def test_routes_are_mounted():
    api = load_api()
    have = {(m, r.path) for r in api.router.routes for m in r.methods}
    assert {("GET", "/groups"), ("POST", "/sites/check"), ("POST", "/sites/{site_id}/confirm"),
            ("DELETE", "/sites/{site_id}"), ("POST", "/public-sites"), ("POST", "/sites/{site_id}/revoke"),
            ("POST", "/desk/{item_id}/publish"), ("POST", "/desk/{item_id}/schedule"),
            ("POST", "/desk/{item_id}/apply"), ("GET", "/desk/{item_id}/review-link")} <= have


def test_secrets_are_read_fresh_in_the_plugin_host_child(tmp_path, monkeypatch):
    api = load_api()
    scope = {"WPBRUECKE_KEY1": "first", "OTHER_TOKEN": "never kept"}
    launch_env = {"WPBRUECKE_KEY1": "stale", "WPBRUECKE_KEY2": "stale too"}
    fake = types.ModuleType("agent.secret_scope")
    fake.build_profile_secret_scope = lambda home: dict(scope)
    fake.get_secret = lambda name: launch_env.get(name)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", fake)
    monkeypatch.setattr(api, "_home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    assert api._get_secret("WPBRUECKE_KEY1") == "stale"  # outside a host child: the request's own scope
    monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
    assert api._get_secret("WPBRUECKE_KEY1") == "first"
    assert api._get_secret("WPBRUECKE_KEY2") is None  # missing in the scope is final, not the launch value
    assert set(api._SCOPE_CACHE[str(tmp_path)]["values"]) == {"WPBRUECKE_KEY1"}
    scope["WPBRUECKE_KEY1"] = "saved later"
    assert api._get_secret("WPBRUECKE_KEY1") == "first"  # within the 2 s cache
    api._SCOPE_CACHE.clear()
    assert api._get_secret("WPBRUECKE_KEY1") == "saved later"
