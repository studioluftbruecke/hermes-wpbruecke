"""The 0.1.0 migration in register(ctx) (contract § 12): once, idempotent, never fatal."""
import json

import pytest

from wpb_fakes import FakeWP, Settings, load_plugin, mod, secrets

OLD_FILE = ("id: shop\nurl: https://shop.example\nlogin: hermes-drafter\nkey_slot: 3\npublishing: confirm\n"
            "never_publish_never_send: false\nagent_writes: always\nlabel: Shop\n")


class State:
    def __init__(self, data_dir):
        self.data_dir = data_dir

    def get(self, *a):
        return None

    def set(self, *a):
        return None


class Ctx:
    """Enough of PluginContext for register(): config, state, and every register_* accepted."""

    def __init__(self, data_dir, config=None, fail=False):
        self.config = dict(config or {})
        self.state = State(data_dir)
        self.fail = fail
        self.set_calls = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.set_calls.append((key, value))
        if self.fail:
            raise PermissionError("config.yaml is read-only")
        self.config[key] = value

    def __getattr__(self, attr):
        return lambda *a, **k: None


@pytest.fixture
def old_install(tmp_path, monkeypatch):
    (tmp_path / "sites").mkdir()
    (tmp_path / "sites" / "shop.yaml").write_text(OLD_FILE, encoding="utf-8")
    (tmp_path / "sites" / "news.yaml").write_text("id: news\nurl: https://news.example\npublishing: \"off\"\n",
                                                  encoding="utf-8")
    plugin = load_plugin()
    monkeypatch.setattr(plugin, "_default_secret", lambda name: {"WPBRUECKE_KEY3": "pw pw pw"}.get(name))
    return tmp_path, plugin


def register(plugin, ctx):
    plugin.register(ctx)


def test_a_010_site_moves_into_its_group_once(old_install):
    tmp_path, plugin = old_install
    ctx = Ctx(tmp_path)
    register(plugin, ctx)
    assert ctx.config == {"site3_url": "https://shop.example", "site3_user": "hermes-drafter"}
    rec = mod("groups").Records(mod("store").Store(tmp_path)).get(3)
    assert rec.host == "shop.example" and rec.fp == mod("groups").fingerprint("pw pw pw") and rec.site_id == "shop"
    moved = json.loads((tmp_path / "migration" / "shop.json").read_text())
    assert moved["publishing"] == "confirm" and moved["never_publish_never_send"] is False and moved["agent_writes"] == "always"
    text = (tmp_path / "sites" / "shop.yaml").read_text()
    assert "publishing" not in text and "agent_writes" not in text and "access: null" in text
    assert "publishing" not in (tmp_path / "sites" / "news.yaml").read_text()
    assert json.loads((tmp_path / "migration" / "done.json").read_text())["sites"] == ["news", "shop"]
    calls = len(ctx.set_calls)
    register(plugin, ctx)
    assert len(ctx.set_calls) == calls  # idempotent


def test_after_migration_the_site_is_paired_and_asks_for_confirm(old_install):
    tmp_path, plugin = old_install
    ctx = Ctx(tmp_path)
    register(plugin, ctx)
    fake = FakeWP(host="shop.example", user="hermes-drafter", caps={"read": True, "edit_posts": True,
                                                                     "upload_files": True}, roles=["wpbruecke_drafter"])
    rt = mod("runtime").Runtime(Settings(ctx.config), secrets(WPBRUECKE_KEY3="pw pw pw"), tmp_path, transport=fake)
    site = rt.site("shop")
    assert rt.pairing(site) == "paired" and site["access"] is None and site["label"] == "Shop"
    s, res = mod("api").sites_check(rt, {"group": 3})
    assert res["state"] == "needs_confirm" and res["site_id"] == "shop"
    assert res["migrated"] == {"publishing": "confirm", "never_publish_never_send": False, "agent_writes": "always"}


def test_a_failing_set_config_does_not_break_register_and_groups_offers_the_values(old_install):
    tmp_path, plugin = old_install
    ctx = Ctx(tmp_path, fail=True)
    register(plugin, ctx)
    rt = mod("runtime").Runtime(Settings(ctx.config), secrets(WPBRUECKE_KEY3="pw pw pw"), tmp_path)
    row = mod("api").groups_view(rt)[1]["groups"][2]
    assert row["migrate"] == {"url": "https://shop.example", "login": "hermes-drafter"}
    assert row["state"] == "group_empty" and row["site_id"] == "shop"


def test_a_group_already_holding_another_site_is_left_alone(old_install):
    tmp_path, plugin = old_install
    ctx = Ctx(tmp_path, config={"site3_url": "https://elsewhere.example", "site3_user": "x"})
    register(plugin, ctx)
    assert ctx.set_calls == []
    assert mod("groups").Records(mod("store").Store(tmp_path)).get(3) is None


def test_register_survives_a_migration_that_raises(tmp_path, monkeypatch):
    plugin = load_plugin()
    monkeypatch.setattr(mod("migration"), "migrate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert plugin._migrate(Ctx(tmp_path), lambda k, d=None: d, tmp_path) is None
    plugin.register(Ctx(tmp_path))
