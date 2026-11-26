"""Access levels: what a site's WordPress user may do, read from its role and capabilities (pure).

The WordPress role is the level (plan D3). ``classify`` maps the true capabilities from
``users/me?context=edit`` (measured per role in P20) to one level, ``effective`` compares that live
reading with the level the person confirmed, and ``REQUIRED`` says which level each action needs
(contract § 7). Nothing here sends a request; enforce.py reads the live role and applies it.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

LEVELS = ("read_public", "read", "drafts", "publish")      # rank 0..3
REFUSE_CAPS = ("manage_options", "edit_users", "install_plugins", "edit_theme_options")
PUBLISH_CAPS = ("publish_posts", "publish_pages", "publish_products")   # WooCommerce's create checks publish_products (P27)
READER_ROLE = "wpbruecke_reader"
READER_CONNECTOR = (0, 2, 1)   # 0.2.0 stored edit caps in the role; 0.2.1 stores read caps only

REFUSED_ADMIN = "Use a user with one of the roles above. Full admin access is a separate plan."
REFUSED_READER = ("The reader role needs the WPBrücke connector 0.2.1 or newer active; without it the role could "
                  "write.")
REFUSED_MIXED_READER = "The Hermes user has the WPBrücke reader role and another role. Give it one role."
EFFECTIVE = ("unconfirmed", "confirmed", "lowered", "stopped")


def rank(level: Optional[str]) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def true_caps(caps: Any) -> list:
    """The capability names whose value is true, sorted (role slugs WordPress mixes in are kept out by
    the caller passing ``roles``)."""
    if isinstance(caps, dict):
        return sorted(str(k) for k, v in caps.items() if v is True or v == 1)
    if isinstance(caps, (list, tuple)):
        return sorted(str(c) for c in caps)
    return []


# Capabilities a plugin's own update steps add to WordPress roles. No WPBrücke path uses them, so they
# are not part of what Hermes may do: Elementor 4.4.2 adds elementor_global_classes_* and
# elementor_atomic_widgets_* to the Editor role on its first wp-admin visit after an install or update,
# which stopped every Editor-keyed site until the person confirmed again.
IGNORED_CAP_PREFIXES = ("elementor_",)


def _fp(names: list) -> str:
    return hashlib.sha256(",".join(names).encode()).hexdigest()[:12]


def caps_fingerprint(caps: Any, roles: Any = ()) -> str:
    """First 12 hex of sha256 of the sorted true caps joined by ``,`` (role-name caps and
    IGNORED_CAP_PREFIXES excluded)."""
    return _fp([c for c in true_caps(caps) if c not in set(roles or ()) and not c.startswith(IGNORED_CAP_PREFIXES)])


def legacy_caps_fingerprint(caps: Any, roles: Any = ()) -> str:
    """The fingerprint 0.3.x recorded at Confirm (no prefix ignored), so an update does not stop a site."""
    return _fp([c for c in true_caps(caps) if c not in set(roles or ())])


def _version(v: Any) -> tuple:
    out = []
    for part in str(v or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def _publishes(held: set) -> str:
    """What a Publish-level user publishes, in words (a shop role adds products)."""
    posts = "all posts and pages" if "publish_pages" in held else \
        "its own posts" if "publish_posts" in held or "publish_products" not in held else ""
    return " and ".join(w for w in (posts, "products" if "publish_products" in held else "") if w)


def classify(roles: Any, caps: Any, connector_version: Optional[str] = None) -> "tuple[str, str]":
    """(level | "refused" | "none", reason). Order per contract § 6; the first match wins."""
    roles = [str(r) for r in (roles or [])]
    held = set(true_caps(caps))
    if any(c in held for c in REFUSE_CAPS):
        return "refused", REFUSED_ADMIN
    if READER_ROLE in roles:  # before the publish caps: the connector treats the reader role as dominant
        if len(set(roles)) > 1:
            return "refused", REFUSED_MIXED_READER
        if connector_version and _version(connector_version) >= READER_CONNECTOR:
            return "read", "the WPBrücke reader role, held read-only by the connector"
        return "refused", REFUSED_READER
    if any(c in held for c in PUBLISH_CAPS):
        return "publish", f"the user can publish {_publishes(held)}"
    if "edit_posts" in held:
        return "drafts", "the user can write drafts" + (" with images" if "upload_files" in held else "") + \
            (" and draft products" if "edit_products" in held else "")
    if "read" in held:
        return "read_public", "the user can only read"
    return "none", "the user has no role on this site"


def uploads(caps: Any) -> bool:
    return "upload_files" in set(true_caps(caps))


def level_words(level: Optional[str], caps: Any = None) -> Optional[str]:
    held = set(true_caps(caps))
    if level == "read":
        return "read posts, pages and drafts, never write"
    products = " and draft products" if "edit_products" in held else ""
    if level == "drafts":
        return ("write drafts with images" if "upload_files" in held else
                "write drafts (no images: Contributor cannot upload)") + products
    if level == "publish":
        return f"write and publish {_publishes(held)}"
    if level == "read_public":
        return "read what the public sees"
    return None


def effective(access: Optional[dict], live_level: str, live_fp: Any = "") -> "tuple[str, Optional[str]]":
    """(effective, level in force). ``live_level`` is classify's answer now, ``live_fp`` its caps
    fingerprint, or a tuple of fingerprints any of which matches (current and legacy)."""
    if not access:
        return "unconfirmed", None
    if live_level in ("refused", "none") or live_level not in LEVELS:
        return "stopped", None
    recorded = access.get("level")
    if rank(live_level) > rank(recorded):
        return "stopped", None
    if rank(live_level) == rank(recorded):
        fps = tuple(f for f in ((live_fp,) if isinstance(live_fp, str) else tuple(live_fp or ())) if f)
        if fps and access.get("caps_fingerprint") and access.get("caps_fingerprint") not in fps:
            return "stopped", None
        return "confirmed", recorded
    return "lowered", live_level


STOP_REASONS = {
    "wider": "someone gave this user more rights; confirm again on the WPBrücke page",
    "changed": "the role's rights changed; confirm again on the WPBrücke page",
    "gone": "the user lost its role or now has rights Hermes refuses",
}


def stop_reason(access: Optional[dict], live_level: str, live_fp: Any = "") -> Optional[str]:
    if not access or effective(access, live_level, live_fp)[0] != "stopped":
        return None
    if live_level not in LEVELS:
        return STOP_REASONS["gone"]
    return STOP_REASONS["wider"] if rank(live_level) > rank(access.get("level")) else STOP_REASONS["changed"]


# --- What each level allows (contract § 7): one registry every tool, route and the tick reads -----

REQUIRED = {  # action -> (level it needs, needs upload_files too)
    "public_read": (None, False),
    "keyed_read": ("read", False),
    "draft_create": ("drafts", False), "draft_update": ("drafts", False), "draft_stage": ("drafts", False),
    "draft_set_seo": ("drafts", False), "upload_media": ("drafts", True), "seo_stage_fix": ("drafts", False),
    "build_write": ("drafts", False),
    "publish": ("publish", False), "schedule": ("publish", False), "apply": ("publish", False),
}
DRAFT_ACTIONS = {"create": "draft_create", "update": "draft_update", "stage": "draft_stage",
                 "set_seo": "draft_set_seo", "upload_media": "upload_media",
                 "publish": "publish", "schedule": "schedule", "apply": "apply"}

REFUSALS = {
    "level_public": "Public read access: this site's Hermes user (Subscriber) cannot read drafts. Give it the "
                    "WPBrücke reader role or a wider one and confirm again on the WPBrücke page.",
    "level_read": "Read access: this site's Hermes user cannot write. Change its role in WordPress and confirm "
                  "again on the WPBrücke page.",
    "level_drafts": "Drafts access: this site's Hermes user cannot publish. Give it the Author or Editor role (for "
                    "products the WPBrücke shop publisher role) in WordPress and confirm again on the WPBrücke page.",
    "level_unconfirmed": "This site's access level is not confirmed yet. Confirm it on the WPBrücke page.",
    "level_stopped": "WPBrücke stopped this site because the WordPress user's rights changed. Confirm again on the "
                     "WPBrücke page.",
    "level_no_uploads": "This site's Hermes user cannot upload images (Contributor). Give it the connector's drafter "
                        "role and confirm again on the WPBrücke page.",
}


def need(action: str) -> "tuple[Optional[str], bool]":
    if action not in REQUIRED:
        raise KeyError(f"unknown action {action!r}")
    return REQUIRED[action]


def required_level(action: str) -> Optional[str]:
    return need(action)[0]


def allows(level_in_force: Optional[str], eff: str, action: str, can_upload: bool = True) -> Optional[str]:
    """None when allowed, else the refusal code (REFUSALS). Pure: callers pass what they read."""
    wanted, uploads_needed = need(action)
    if wanted is None:
        return None
    if eff == "unconfirmed":
        return "level_unconfirmed"
    if eff == "stopped" or level_in_force is None:
        return "level_stopped"
    if rank(level_in_force) < rank(wanted):
        if wanted == "read":
            return "level_public"
        return "level_read" if rank(level_in_force) <= rank("read") else "level_drafts"
    if uploads_needed and not can_upload:
        return "level_no_uploads"
    return None
