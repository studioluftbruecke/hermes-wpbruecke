"""From 0.1.0 to groups (contract § 12): runs once per site file in ``register(ctx)``.

A 0.1.0 site file with ``key_slot: N`` (1 to 12) fills group N's address and user when that group is
empty (``ctx.set_config``), writes group N's pairing record (the file's host and the fingerprint of
the stored password: 0.1.0 used them together), and moves ``publishing``, ``never_publish_never_send``
and ``agent_writes`` into ``migration/<site>.json`` for the page's card. ``access`` stays null, so the
person confirms the level. When ``set_config`` fails, ``migration/pending.json`` keeps the values and
``GET /groups`` offers them as ``migrate: {url, login}``. Marker: ``migration/done.json``.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import groups as groups_mod
from . import settings as settings_mod
from . import yamlish
from .sites import LEGACY_FIELDS, SiteError, SiteStore, normalise_url
from .store import Store, StoreError, now_iso


def capture_legacy(store: Store, site_id: str, legacy: dict) -> None:
    """SiteStore's legacy sink: keep a 0.1.0 file's old values once, before any save drops them."""
    try:
        path = store.path("migration", f"{site_id}.json")
    except StoreError:
        return
    if path.exists():
        return
    store.write_json(path, {"site": site_id, "captured_at": now_iso(),
                            **{k: legacy[k] for k in LEGACY_FIELDS if k in legacy}})


def legacy_of(store: Store, site_id: str) -> Optional[dict]:
    try:
        data = store.read_json(store.path("migration", f"{site_id}.json"), None)
    except StoreError:
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in LEGACY_FIELDS}


def pending(store: Store) -> dict:
    """{n: {url, login}} for groups the migration could not fill through ctx.set_config."""
    data = store.read_json(store.path("migration", "pending.json"), {}) or {}
    return {int(k): v for k, v in data.items() if str(k).isdigit() and isinstance(v, dict)}


def migrate(store: Store, get_setting: Callable[[str, Any], Any], get_secret: Callable[[str], Optional[str]],
            set_config: Callable[[str, Any], None]) -> dict:
    """Idempotent. Returns {migrated: [site ids], pending: {n: {url, login}}}."""
    marker = store.path("migration", "done.json")
    done = set((store.read_json(marker, {}) or {}).get("sites") or [])
    sites = SiteStore(store, legacy_sink=lambda sid, legacy: capture_legacy(store, sid, legacy))
    waiting = pending(store)
    migrated = []
    with store.lock("sites"):
        files = sorted(store.dir("sites").glob("*.yaml"))
    for path in files:
        sid = path.stem
        if sid in done:
            continue
        try:
            raw = yamlish.load(path.read_text(encoding="utf-8")) or {}
            slot = int(raw.get("key_slot") or 0)
            url = normalise_url(str(raw.get("url") or ""))
        except (OSError, ValueError, TypeError):
            continue
        login = str(raw.get("login") or "").strip()
        if 1 <= slot <= settings_mod.LEGACY_SLOTS:
            group = groups_mod.load_group(slot, get_setting, get_secret)
            keys = settings_mod.group_keys(slot)
            if not group.url:
                try:
                    set_config(keys["url"], url)
                    set_config(keys["user"], login)
                    waiting.pop(slot, None)
                except Exception:
                    waiting[slot] = {"url": url, "login": login}
            target = group.host if group.url else groups_mod.host(url)
            records = groups_mod.Records(store)
            password = settings_mod.secret_for_slot(get_secret, slot)
            if target == groups_mod.host(url) and password and records.get(slot) is None:
                records.put(groups_mod.PairingRecord(n=slot, host=target, fp=groups_mod.fingerprint(password),
                                                     proven_at=now_iso(), site_id=sid, username=login))
        legacy = {k: raw[k] for k in LEGACY_FIELDS if k in raw}
        if legacy:
            capture_legacy(store, sid, legacy)
            try:
                site = sites.get(sid)
                if site is not None:
                    with store.lock("sites"):
                        sites._save(site, "migration", "0.1.0 fields publishing, never_publish_never_send, "
                                                       "agent_writes moved out (the access level replaces them)")
            except (SiteError, StoreError):
                continue
        done.add(sid)
        migrated.append(sid)
    store.write_json(store.path("migration", "pending.json"), {str(k): v for k, v in waiting.items()})
    store.write_json(marker, {"sites": sorted(done), "at": now_iso()})
    return {"migrated": migrated, "pending": waiting}
