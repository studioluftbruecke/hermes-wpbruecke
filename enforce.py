"""The access level, enforced: one ``require(rt, site, action)`` for every tool, route and the tick.

``standing(rt, site)`` answers what a site may do now: its pairing, the level the person confirmed
(``site.access``), the live reading of the WordPress user (``users/me?context=edit``, classified by
access.py) and ``effective()`` over both. ``require`` looks the action up in access.REQUIRED (the one
registry) and returns None or a ``Refusal`` (the contract's sentence plus the next step) before any
write request. ``Runtime.client`` asks ``require(..., "keyed_read")`` before it attaches a password,
so an unconfirmed or stopped site only ever reads what the public sees.

The live reading is cached in memory per site: 60 s for reads, 5 s for writes (the gate and the
handler of one call share a reading, a promotion is seen by the next write). A connection check, a
Confirm and a revoke forget it; the tick reads it fresh. A reading that failed is never cached.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import access as access_mod
from .client import WPError

READ_TTL_S = 60.0
WRITE_TTL_S = 5.0
CONNECTOR_NS_INFO = "/wp-json/wpbruecke/v1/info"

_CACHE: dict = {}
_LOCK = threading.Lock()
_clock = time.monotonic


@dataclass(frozen=True)
class Live:
    """One reading of the WordPress user: classify's answer, or ``error`` when it could not be read."""
    level: str = "none"
    reason: str = ""
    roles: tuple = ()
    caps: tuple = ()
    caps_fp: str = ""
    connector: Optional[str] = None
    error: Optional[str] = None      # auth (the password no longer works) | unreachable | ...
    detail: str = ""


@dataclass(frozen=True)
class Standing:
    site_id: str
    keyed: bool                      # a password may be sent (key_slot and paired)
    pairing: str
    access: Optional[dict]
    live: Optional[Live]
    effective: Optional[str]         # unconfirmed | confirmed | lowered | stopped | None (public / unreadable)
    level: Optional[str]             # the level in force; None when nothing keyed runs
    stop_reason: Optional[str] = None

    @property
    def uploads(self) -> bool:
        return bool(self.live) and "upload_files" in set(self.live.caps)

    def words(self) -> Optional[str]:
        if not self.keyed:
            return access_mod.level_words("read_public") if not self.access else None
        if self.level is None:
            return None
        return access_mod.level_words(self.level, list(self.live.caps) if self.live else None)

    def row(self) -> dict:
        """The fields the page reads per site (overview, desk)."""
        return {"level": (self.access or {}).get("level"), "effective": self.effective,
                "level_in_force": self.level, "level_words": self.words(), "stop_reason": self.stop_reason,
                "uploads": self.uploads if self.live else None, "can_publish": self.level == "publish"}


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str
    next: dict = field(default_factory=dict)
    effective: Optional[str] = None
    level: Optional[str] = None

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, "next": dict(self.next),
                "effective": self.effective, "level": self.level, "sent": False}


def _key(rt: Any, site: dict) -> tuple:
    n = site.get("key_slot") or 0
    rec = rt.records.get(n) if n else None
    return (str(rt.store.root), site["id"], n, getattr(rec, "fp", None))


def forget(rt: Any, site_id: Optional[str] = None) -> None:
    """Drop the cached reading of one site (or all sites of this data folder)."""
    root = str(rt.store.root)
    with _LOCK:
        for k in list(_CACHE):
            if k[0] == root and (site_id is None or k[1] == site_id):
                del _CACHE[k]


def read_live(rt: Any, site: dict) -> Live:
    """``users/me?context=edit`` with the site's own password (and the connector's version when the user
    holds the reader role, P23). Raises nothing: a failed read is a Live with ``error``."""
    client = rt.key_client(site)
    try:
        me = client.get("/wp-json/wp/v2/users/me", context="edit")
    except WPError as e:
        return Live(error=e.kind, detail=e.message)
    if not isinstance(me, dict):
        return Live(error="bad_response", detail="users/me did not answer with a user")
    roles = tuple(str(r) for r in me.get("roles") or [])
    caps = me.get("capabilities") or {}
    connector = None
    if access_mod.READER_ROLE in roles:
        try:
            info = client.get(CONNECTOR_NS_INFO)
            connector = str(info.get("version")) if isinstance(info, dict) and info.get("version") else None
        except WPError:
            connector = None
    level, reason = access_mod.classify(list(roles), caps, connector) if roles else ("none", "no role")
    true = tuple(c for c in access_mod.true_caps(caps) if c not in set(roles))
    return Live(level=level, reason=reason, roles=roles, caps=true,
                caps_fp=access_mod.caps_fingerprint(caps, roles), connector=connector)


def _live(rt: Any, site: dict, max_age: float, refresh: bool) -> Live:
    key = _key(rt, site)
    now = _clock()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and not refresh and now - hit[0] <= max_age:
        return hit[1]
    live = read_live(rt, site)
    if live.error is None:
        with _LOCK:
            _CACHE[key] = (now, live)
    return live


def stop_reason(access: Optional[dict], live: Live) -> str:
    """Why a confirmed site stopped, in the person's words."""
    if live.error == "auth":
        return "WordPress no longer accepts this site's application password (revoked or changed)"
    if live.level == "refused":
        if live.reason == access_mod.REFUSED_MIXED_READER:
            return "the user has the WPBrücke reader role and another role; give it one role"
        if access and access.get("role") == access_mod.READER_ROLE and access_mod.READER_ROLE in live.roles:
            return "the WPBrücke connector is off or older than 0.2.1, so the reader role is not held read-only"
        named = next((r for r in live.roles if r == "administrator"), None) or ", ".join(live.roles) or "its role"
        return f"the user now has rights Hermes refuses ({named})"
    if live.level == "none":
        if access and access.get("role") == access_mod.READER_ROLE:
            return ("the user has no role on the site any more; the WPBrücke connector removes the reader role "
                    "while it is deactivated")
        return "the user has no role on this site any more"
    return access_mod.stop_reason(access, live.level, live.caps_fp) or "the user's rights changed"


def standing(rt: Any, site: dict, refresh: bool = False, max_age: float = READ_TTL_S) -> Standing:
    access = site.get("access") or None
    if not site.get("key_slot"):
        return Standing(site["id"], False, "public", None, None, None, None)
    pairing = rt.pairing(site)
    if pairing != "paired":
        return Standing(site["id"], False, pairing, access, None, "unconfirmed" if not access else None, None,
                        None if not access else f"Site {site['key_slot']} is not connected ({pairing})")
    if not access:
        return Standing(site["id"], True, pairing, None, None, "unconfirmed", None)
    live = _live(rt, site, max_age, refresh)
    if live.error == "auth":
        return Standing(site["id"], True, pairing, access, live, "stopped", None, stop_reason(access, live))
    if live.error:
        return Standing(site["id"], True, pairing, access, live, None, None,
                        f"WordPress did not tell who this user is now ({live.detail or live.error})")
    eff, level = access_mod.effective(access, live.level, live.caps_fp)
    return Standing(site["id"], True, pairing, access, live, eff, level,
                    stop_reason(access, live) if eff == "stopped" else None)


NEXT_CONFIRM = {"label": "Open the WPBrücke page", "action": "confirm"}


def refusal_for(st: Standing, action: str) -> Optional[Refusal]:
    """The pure half of ``require``: the refusal for ``action`` given a standing, or None."""
    need, uploads = access_mod.need(action)
    if need is None:
        return None
    if not st.keyed:
        return Refusal("no_key", f"site {st.site_id} has no connected password ({st.pairing}); connect it on the "
                                 "WPBrücke page. Nothing was sent.", {"label": "Open the WPBrücke page", "action": "check"},
                       st.effective, None)
    if st.effective is None and st.live is not None and st.live.error:
        return Refusal(st.live.error if st.live.error in ("unreachable", "rate_limited") else "level_unknown",
                       f"WPBrücke could not read this site's WordPress user before acting: {st.stop_reason}. Nothing "
                       "was sent.", {"label": "Try again", "action": "check"}, None, None)
    code = access_mod.allows(st.level, st.effective or "stopped", action, can_upload=st.uploads)
    if code is None:
        return None
    message = access_mod.REFUSALS[code]
    if code == "level_stopped" and st.stop_reason:
        message += f" Why: {st.stop_reason}."
    return Refusal(code, message + " Nothing was sent.", NEXT_CONFIRM, st.effective, st.level)


def require(rt: Any, site: dict, action: str) -> Optional[Refusal]:
    """None when ``site`` may do ``action`` now, else the Refusal. Reads the live role for keyed actions
    (cached, see the module note) and never sends anything else."""
    need, _ = access_mod.need(action)
    if need is None:
        return None
    max_age = READ_TTL_S if access_mod.rank(need) <= access_mod.rank("read") else WRITE_TTL_S
    return refusal_for(standing(rt, site, max_age=max_age), action)
