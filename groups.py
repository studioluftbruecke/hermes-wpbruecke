"""Setting groups, the pairing of a group's password with the host it was proven against, and the
sync from groups to site files (contract §§ 2, 3, 5).

A Group is derived from the three settings of group n and never stored as a whole. A PairingRecord
(``groups/<n>.json``) is written only by a successful credential check and once by the 0.1.0
migration. ``pairing(group, record)`` is the one place that decides whether a password may be sent:
only ``paired`` lets a tool, route or tick send it; ``new`` and ``password_changed`` let the check
send it once.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import settings as settings_mod
from .sites import DuplicateHost, SiteError, normalise_url
from .store import Store, now_iso

PAIRING_STATES = ("free", "no_user", "no_password", "new", "host_changed", "password_changed", "paired")
CHECK_MAY_SEND = frozenset({"new", "password_changed", "paired"})
USER_MAX = 100


def fingerprint(password: Optional[str]) -> Optional[str]:
    """First 8 hex of sha256(password); None when not set. Never the value."""
    return hashlib.sha256(password.encode()).hexdigest()[:8] if password else None


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


@dataclass(frozen=True)
class Group:
    n: int
    url: str              # normalised (https://host[/path]) or ""
    user: str             # trimmed, <= 100 chars
    env: str
    set: bool             # password present
    fp: Optional[str]     # first 8 hex of sha256(password); never the value
    error: Optional[str]  # address could not be parsed

    @property
    def host(self) -> str:
        return host(self.url)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PairingRecord:
    n: int
    host: str
    fp: Optional[str]
    proven_at: str
    site_id: str = ""
    username: str = ""
    app_password_uuid: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["PairingRecord"]:
        if not isinstance(data, dict) or not data.get("host"):
            return None
        try:
            return cls(n=int(data.get("n") or 0), host=str(data["host"]).lower(), fp=data.get("fp"),
                       proven_at=str(data.get("proven_at") or ""), site_id=str(data.get("site_id") or ""),
                       username=str(data.get("username") or ""),
                       app_password_uuid=data.get("app_password_uuid") or None)
        except (TypeError, ValueError):
            return None


def make_group(n: int, url: Any, user: Any, password: Optional[str]) -> Group:
    raw = str(url or "").strip()
    norm, error = "", None
    if raw:
        try:
            norm = normalise_url(raw)
        except SiteError as e:
            error = str(e)
    return Group(n=n, url=norm, user=str(user or "").strip()[:USER_MAX], env=f"WPBRUECKE_KEY{n}",
                 set=bool(password), fp=fingerprint(password), error=error)


def load_group(n: int, get_setting: Callable[[str, Any], Any], get_secret: Callable[[str], Optional[str]]) -> Group:
    keys = settings_mod.group_keys(n)
    password = settings_mod.secret_for_slot(get_secret, n)
    return make_group(n, get_setting(keys["url"], ""), get_setting(keys["user"], ""), password)


def load_groups(get_setting, get_secret) -> list:
    return [load_group(n, get_setting, get_secret) for n in range(1, settings_mod.GROUPS + 1)]


# --- pairing: one table, first matching row wins ------------------------------------------------

def _no_record(g: Group, r: Optional[PairingRecord]) -> bool:
    return r is None


def _host_moved(g: Group, r: Optional[PairingRecord]) -> bool:
    return r is not None and r.host != g.host and r.fp == g.fp


def _password_moved(g: Group, r: Optional[PairingRecord]) -> bool:
    return r is not None and r.fp != g.fp


PAIRING_TABLE = (
    ("free", lambda g, r: not g.url),
    ("no_user", lambda g, r: not g.user),
    ("no_password", lambda g, r: not g.set),
    ("new", _no_record),
    ("host_changed", _host_moved),
    ("password_changed", _password_moved),
    ("paired", lambda g, r: True),
)


def pairing(group: Group, record: Optional[PairingRecord]) -> str:
    """The group's state (contract § 3). Pure."""
    for state, when in PAIRING_TABLE:
        if when(group, record):
            return state
    return "paired"  # unreachable: the last row always matches


def host_changed_message(group: Group, record: Optional[PairingRecord]) -> str:
    old = record.host if record else "another site"
    return (f"Site {group.n} now points to {group.host}; its stored password belongs to {old}. "
            f"Paste the new site's password into Site {group.n}.")


# --- records on disk ----------------------------------------------------------------------------

class Records:
    def __init__(self, store: Store) -> None:
        self.store = store

    def get(self, n: int) -> Optional[PairingRecord]:
        return PairingRecord.from_dict(self.store.read_json(self.store.path("groups", f"{int(n)}.json"), None))

    def put(self, record: PairingRecord) -> None:
        self.store.write_json(self.store.path("groups", f"{record.n}.json"), record.to_dict())

    def last_check(self, n: int) -> Optional[dict]:
        return self.store.read_json(self.store.path("groups", f"{int(n)}.check.json"), None)

    def put_check(self, n: int, result: dict) -> None:
        self.store.write_json(self.store.path("groups", f"{int(n)}.check.json"), result)


# --- sync: groups to site files (contract § 5) ---------------------------------------------------

def site_id_for(host_name: str, taken: set) -> str:
    base = "".join(ch if ch.isalnum() else "-" for ch in host_name.lower()).strip("-")[:64] or "site"
    if base not in taken:
        return base
    for i in range(2, 1000):
        cand = f"{base[:64 - len(str(i)) - 1]}-{i}"
        if cand not in taken:
            return cand
    raise SiteError("no free site id")


def sync(groups: list, sites_store: Any) -> list:
    """Bring site files in line with the groups. Never deletes. Returns one problem line per group
    that could not be synced (a duplicate host, say); the check reports those as states."""
    problems = []
    for g in groups:
        if not g.url:
            continue
        try:
            all_sites = [s for s in sites_store.all() if "error" not in s]
            mine = next((s for s in all_sites if s.get("key_slot") == g.n), None)
            if mine is not None:
                changes = {k: v for k, v in (("url", g.url), ("login", g.user)) if mine.get(k) != v}
                if changes:
                    sites_store.update(mine["id"], changes, "settings")
                continue
            public = next((s for s in all_sites if not s.get("key_slot") and host(s.get("url", "")) == g.host), None)
            if public is not None:
                sites_store.update(public["id"], {"key_slot": g.n, "url": g.url, "login": g.user}, "settings")
                continue
            if any(host(s.get("url", "")) == g.host for s in all_sites):
                problems.append({"group": g.n, "error": "duplicate", "host": g.host})
                continue
            sid = site_id_for(g.host, set(sites_store.ids()))
            sites_store.create({"id": sid, "url": g.url, "login": g.user, "key_slot": g.n}, "settings")
        except DuplicateHost as e:
            problems.append({"group": g.n, "error": "duplicate", "host": g.host, "message": str(e)})
        except SiteError as e:
            problems.append({"group": g.n, "error": "invalid", "message": str(e)})
    return problems


def new_record(group: Group, site_id: str, username: str, uuid: Optional[str]) -> PairingRecord:
    return PairingRecord(n=group.n, host=group.host, fp=group.fp, proven_at=now_iso(), site_id=site_id,
                         username=username, app_password_uuid=uuid)
