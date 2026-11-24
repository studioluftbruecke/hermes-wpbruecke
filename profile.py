"""Which Hermes profile this turn belongs to, and whether WPBrücke is on there.

Hermes keeps some registries process-wide (the ``@wp:`` provider, ``pre_tool_call`` hooks): one
profile's load is seen by every profile the multiplexed gateway serves. So the plugin asks at call
time whether it is enabled in the profile whose turn is running, and stays silent where it is not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

PLUGIN = "wpbruecke"


def current_home() -> Optional[Path]:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return None


def read_config(home: Path) -> dict:
    """The profile's config.yaml, read fresh each call (no cache: a flipped switch must be seen)."""
    path = Path(home) / "config.yaml"
    try:
        import hermes_yaml as yaml
    except ImportError:
        import yaml  # type: ignore
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def enabled_in(config: dict, name: str = PLUGIN) -> bool:
    plugins = config.get("plugins") if isinstance(config, dict) else None
    if not isinstance(plugins, dict):
        return False
    return name in (plugins.get("enabled") or []) and name not in (plugins.get("disabled") or [])


def enabled_here() -> bool:
    """True when the running profile enables the plugin, or when that cannot be told (tests, CLI)."""
    home = current_home()
    if home is None:
        return True
    try:
        return enabled_in(read_config(home))
    except Exception:
        return True


def current_name(home: Optional[Path] = None) -> str:
    home = home or current_home()
    if home is None:
        return ""
    home = Path(home)
    return home.name if home.parent.name == "profiles" else "default"
