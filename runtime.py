"""Wiring shared by tools, routes, the CLI and the tick: settings, groups, stores, clients, facts.

Built per call (cheap): a gateway turn, a plugin-host route and a cron tick each build their own
``Runtime`` from the profile's settings getter, Hermes' secret scope and the plugin data folder.
Each build syncs the settings groups into site files (groups.sync). ``client()`` attaches a site's
password only when its group is ``paired`` (contract § 3).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import enforce as enforce_mod
from . import facts as facts_mod
from . import groups as groups_mod
from . import migration as migration_mod
from . import settings as settings_mod
from .client import WordPressClient
from .sites import SiteError, SiteStore, host_of
from .store import Store


class NotFound(ValueError):
    pass


class Runtime:
    def __init__(self, get_setting: Callable[[str, Any], Any], get_secret: Callable[[str], Optional[str]],
                 data_dir: Any, transport: Any = None, wporg_fetch: Any = None, clock: Any = None,
                 client_timeout: float = 25.0, sync: bool = True) -> None:
        self.get_setting, self.get_secret = get_setting, get_secret
        self.settings = settings_mod.load_settings(get_setting)
        self.store = Store(data_dir)
        self.sites = SiteStore(self.store, legacy_sink=self._legacy)
        self.records = groups_mod.Records(self.store)
        self.transport = transport
        self.client_timeout = client_timeout
        kw = {"clock": clock} if clock else {}
        self.facts = facts_mod.FactsCache(self.store, **kw)
        self.wporg = facts_mod.WporgVersions(self.store, fetch=wporg_fetch, **kw)
        self.sync_problems: list = []
        if sync:
            self.sync()

    def _legacy(self, site_id: str, legacy: dict) -> None:
        migration_mod.capture_legacy(self.store, site_id, legacy)

    # --- groups ----------------------------------------------------------------------------------
    def groups(self) -> list:
        return groups_mod.load_groups(self.get_setting, self.get_secret)

    def group(self, n: int) -> groups_mod.Group:
        if not 1 <= int(n) <= settings_mod.GROUPS:
            raise NotFound(f"no group {n}; groups are 1 to {settings_mod.GROUPS}")
        return groups_mod.load_group(int(n), self.get_setting, self.get_secret)

    def group_password(self, n: int) -> Optional[str]:
        return settings_mod.secret_for_slot(self.get_secret, n)

    def sync(self) -> list:
        try:
            self.sync_problems = groups_mod.sync(self.groups(), self.sites)
        except Exception as e:  # a broken site file must not take every tool down with it
            self.sync_problems = [{"error": "sync", "message": f"{type(e).__name__}: {e}"}]
        return self.sync_problems

    def pairing(self, site: dict) -> str:
        """``public`` for a site without a group, else the group's pairing state. A site file whose host
        is not the proven host never counts as paired."""
        n = site.get("key_slot") or 0
        if not n:
            return "public"
        group = self.group(n)
        record = self.records.get(n)
        state = groups_mod.pairing(group, record)
        if state == "paired" and (record is None or record.host != host_of(site)):
            return "host_changed"
        return state

    # --- sites -----------------------------------------------------------------------------------
    def site(self, site_id: str) -> dict:
        try:
            site = self.sites.get(site_id)
        except SiteError as e:
            raise NotFound(str(e)) from None
        if site is None:
            known = ", ".join(self.sites.ids()) or "none yet"
            raise NotFound(f"no site {site_id!r}; known sites: {known}")
        return site

    def key_client(self, site: dict) -> WordPressClient:
        """The site's own password when its group is paired, whatever the access level: only for reading
        the live role (enforce.py) and revoking the password itself."""
        password = None
        if site.get("key_slot") and self.pairing(site) == "paired":
            password = self.group_password(site["key_slot"])
        return WordPressClient(site["url"], site.get("login") or "", password, connect_ip=site.get("connect_ip") or "",
                               timeout=self.client_timeout, transport=self.transport)

    def client(self, site: dict, keyed: bool = True) -> WordPressClient:
        """A client with the password only while the site's level allows keyed reads (contract § 7): an
        unconfirmed or stopped site reads what the public sees."""
        if keyed and site.get("key_slot") and self.pairing(site) == "paired" and self.require(site, "keyed_read") is None:
            return self.key_client(site)
        return WordPressClient(site["url"], "", None, connect_ip=site.get("connect_ip") or "",
                               timeout=self.client_timeout, transport=self.transport)

    def standing(self, site: dict, refresh: bool = False):
        return enforce_mod.standing(self, site, refresh=refresh)

    def require(self, site: dict, action: str):
        """None, or the enforce.Refusal for ``action`` on ``site`` (one function for every caller)."""
        return enforce_mod.require(self, site, action)

    def key_present(self, site: dict) -> bool:
        """True when the site's password may be sent (its group is paired)."""
        return bool(site.get("key_slot")) and self.pairing(site) == "paired"

    def site_facts(self, site: dict, refresh: bool = False) -> dict:
        return self.facts.get(site, self.client, refresh=refresh, wporg=self.wporg)

    def host(self, site: dict) -> str:
        return host_of(site)
