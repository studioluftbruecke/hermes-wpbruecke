"""Typed settings for WPBrücke, read from the plugin's ``config_schema`` values.

A site with a password lives in one of 16 setting groups: ``site{N}_url``, ``site{N}_user`` and the
secret ``site{N}_password`` whose ``.env`` name is ``WPBRUECKE_KEY{N}`` (the 0.1.0 key slot names,
so an upgrade re-enters nothing). groups.py derives a Group from them. Optional keys:
``WPBRUECKE_PSI_KEY`` (PageSpeed) and ``WPBRUECKE_GSC_KEY`` (Search Console service account JSON).
The site files hold everything else per site (sites.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

GROUPS = 16
KEY_SLOTS = GROUPS  # a site file's key_slot is its group number
LEGACY_SLOTS = 12   # 0.1.0 had key1..key12
PSI_KEY_ENV = "WPBRUECKE_PSI_KEY"
GSC_KEY_ENV = "WPBRUECKE_GSC_KEY"

SettingGetter = Callable[[str, Any], Any]
SecretGetter = Callable[[str], Optional[str]]


def slot_env(slot: Any) -> Optional[str]:
    try:
        n = int(slot)
    except (TypeError, ValueError):
        return None
    return f"WPBRUECKE_KEY{n}" if 1 <= n <= KEY_SLOTS else None


def group_keys(n: int) -> dict:
    """The three settings of group n: {url, user, password} -> config key."""
    return {"url": f"site{n}_url", "user": f"site{n}_user", "password": f"site{n}_password"}


@dataclass(frozen=True)
class Settings:
    chromium_path: str = ""             # empty: Hermes' own Chromium
    digest: bool = True                 # the tick reports changes (F13)
    warnings: tuple = field(default_factory=tuple)


def load_settings(get_setting: SettingGetter) -> Settings:
    digest = get_setting("digest", True)
    return Settings(
        chromium_path=str(get_setting("chromium_path", "") or "").strip(),
        digest=digest if isinstance(digest, bool) else str(digest).lower() in ("1", "true", "yes", "on"),
    )


def secret_for_slot(get_secret: SecretGetter, slot: Any) -> Optional[str]:
    name = slot_env(slot)
    if not name:
        return None
    try:
        value = get_secret(name)
    except Exception:
        return None
    value = (value or "").strip()
    return value or None


def config_schema_groups() -> dict:
    """The 48 group fields as they appear in plugin.yaml, in order (kept in sync by a test)."""
    out = {}
    for n in range(1, GROUPS + 1):
        keys = group_keys(n)
        out[keys["url"]] = {"type": "string"}
        out[keys["user"]] = {"type": "string"}
        out[keys["password"]] = {"type": "secret", "env": f"WPBRUECKE_KEY{n}"}
    return out
