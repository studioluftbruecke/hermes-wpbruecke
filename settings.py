"""Typed settings for WPBrücke, read from the plugin's ``config_schema`` values.

Secrets are never settings: each site names a key slot (1 to 12), and slot n is the ``.env`` name
``WPBRUECKE_KEY<n>`` (PLAN constraint 9, P16). Read at call time with Hermes' secret scope.
Optional keys: ``WPBRUECKE_PSI_KEY`` (PageSpeed) and ``WPBRUECKE_GSC_KEY`` (Search Console service
account JSON). The site files hold everything else per site (sites.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

KEY_SLOTS = 12
PSI_KEY_ENV = "WPBRUECKE_PSI_KEY"
GSC_KEY_ENV = "WPBRUECKE_GSC_KEY"
AGENT_WRITE_POLICIES = ("auto", "always")
PUBLISHING = ("off", "confirm", "schedule")

SettingGetter = Callable[[str, Any], Any]
SecretGetter = Callable[[str], Optional[str]]


def slot_env(slot: Any) -> Optional[str]:
    try:
        n = int(slot)
    except (TypeError, ValueError):
        return None
    return f"WPBRUECKE_KEY{n}" if 1 <= n <= KEY_SLOTS else None


@dataclass(frozen=True)
class Settings:
    agent_writes: str = "auto"          # auto: ask only when the key can change live content (D14 a)
    publishing_default: str = "off"     # what a new site file starts with (F8)
    chromium_path: str = ""             # empty: Hermes' own Chromium
    digest: bool = True                 # the tick reports changes (F13)
    warnings: tuple = field(default_factory=tuple)


def load_settings(get_setting: SettingGetter) -> Settings:
    warnings = []

    def pick(key: str, choices: tuple, default: str) -> str:
        value = str(get_setting(key, default) or default).strip().lower()
        if value not in choices:
            warnings.append(f"setting {key}={value!r} is not one of {', '.join(choices)}; using {default}")
            return default
        return value

    digest = get_setting("digest", True)
    return Settings(
        agent_writes=pick("agent_writes", AGENT_WRITE_POLICIES, "auto"),
        publishing_default=pick("publishing_default", PUBLISHING, "off"),
        chromium_path=str(get_setting("chromium_path", "") or "").strip(),
        digest=digest if isinstance(digest, bool) else str(digest).lower() in ("1", "true", "yes", "on"),
        warnings=tuple(warnings),
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


def config_schema_slots() -> dict:
    """The 12 key-slot fields as they appear in plugin.yaml (kept in sync by a test)."""
    return {f"key{n}": {"type": "secret", "env": f"WPBRUECKE_KEY{n}"} for n in range(1, KEY_SLOTS + 1)}
