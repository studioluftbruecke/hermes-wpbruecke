"""Wiring shared by tools, routes, the CLI and the tick: settings, stores, clients, facts.

Built per call (cheap): a gateway turn, a plugin-host route and a cron tick each build their own
``Runtime`` from the profile's settings getter, Hermes' secret scope and the plugin data folder.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import facts as facts_mod
from . import settings as settings_mod
from .client import WordPressClient
from .sites import SiteError, SiteStore, host_of
from .store import Store


class NotFound(ValueError):
    pass


class Runtime:
    def __init__(self, get_setting: Callable[[str, Any], Any], get_secret: Callable[[str], Optional[str]],
                 data_dir: Any, transport: Any = None, wporg_fetch: Any = None, clock: Any = None,
                 client_timeout: float = 25.0) -> None:
        self.get_setting, self.get_secret = get_setting, get_secret
        self.settings = settings_mod.load_settings(get_setting)
        self.store = Store(data_dir)
        self.sites = SiteStore(self.store, self.settings.publishing_default)
        self.transport = transport
        self.client_timeout = client_timeout
        kw = {"clock": clock} if clock else {}
        self.facts = facts_mod.FactsCache(self.store, **kw)
        self.wporg = facts_mod.WporgVersions(self.store, fetch=wporg_fetch, **kw)

    def site(self, site_id: str) -> dict:
        try:
            site = self.sites.get(site_id)
        except SiteError as e:
            raise NotFound(str(e)) from None
        if site is None:
            known = ", ".join(self.sites.ids()) or "none yet"
            raise NotFound(f"no site {site_id!r}; known sites: {known}")
        return site

    def client(self, site: dict, keyed: bool = True) -> WordPressClient:
        password = settings_mod.secret_for_slot(self.get_secret, site.get("key_slot")) if keyed else None
        return WordPressClient(site["url"], site.get("login") or "", password, connect_ip=site.get("connect_ip") or "",
                               timeout=self.client_timeout, transport=self.transport)

    def key_present(self, site: dict) -> bool:
        return bool(settings_mod.secret_for_slot(self.get_secret, site.get("key_slot")))

    def site_facts(self, site: dict, refresh: bool = False) -> dict:
        return self.facts.get(site, self.client, refresh=refresh, wporg=self.wporg)

    def agent_writes(self, site: dict) -> str:
        return site.get("agent_writes") or self.settings.agent_writes

    def host(self, site: dict) -> str:
        return host_of(site)
