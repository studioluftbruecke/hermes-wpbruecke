"""What the Elementor pack may change (A31, design constraint 10), and on which Elementor releases.

``EDITABLE`` names, per widget type, the setting paths an ``ElementorEdit`` may set: texts, links (url,
is_external, nofollow) and images (id, url, alt). ``*`` stands for one repeater item, addressed by its
``_id``. The connector holds the same table (``includes/elementor.php`` ``wpbruecke_elementor_editable``)
and refuses any other path itself; a test keeps the two equal.

Every other widget, every layout element and every V4 atomic element is read-only. Outside
``ELEMENTOR_TESTED`` the whole pack is read-only (R26) and names the range.
"""
from __future__ import annotations

import re
from typing import Any, Optional

ELEMENTOR_TESTED = ("4.4.2", "4.4.2")   # lowest and highest release the harness ran green on (inclusive)

_LINK = ("link.url", "link.is_external", "link.nofollow")
EDITABLE: dict = {
    "heading": ("title",) + _LINK,
    "text-editor": ("editor",),
    "button": ("text",) + _LINK,
    "image": ("image.id", "image.url", "image.alt", "caption") + _LINK,
    "icon-list": ("icon_list.*.text", "icon_list.*.link.url", "icon_list.*.link.is_external",
                  "icon_list.*.link.nofollow"),
    "call-to-action": ("title", "description", "button", "ribbon_title", "bg_image.id", "bg_image.url",
                       "bg_image.alt", "graphic_image.id", "graphic_image.url", "graphic_image.alt") + _LINK,
}
WIDGETS = tuple(EDITABLE)
REPEATERS = {"icon-list": "icon_list"}
MEDIA_KEYS = ("image", "bg_image", "graphic_image")
FLAGS = ("is_external", "nofollow")
MAX_VALUE_BYTES = 65536   # one edit value, UTF-8; the connector's WPBRUECKE_ELEMENTOR_MAX_VALUE

SCOPE = ("The Elementor pack changes texts, links and images of existing heading, text-editor, button, image, "
         "icon-list and call-to-action widgets, through a staged copy; it never adds, removes or moves an "
         "element, never creates an Elementor page, and leaves every other widget and every V4 atomic "
         "element as it is.")

_URL_OK = re.compile(r"^(?:https?://|mailto:|tel:|/|#|\?)", re.I)


def version_tuple(v: Any) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


def tested_range() -> str:
    lo, hi = ELEMENTOR_TESTED
    return lo if lo == hi else f"{lo} to {hi}"


def untested_reason(version: Any) -> Optional[str]:
    """None inside ELEMENTOR_TESTED; else the sentence that names the range (R26)."""
    have = version_tuple(version)
    lo, hi = (version_tuple(v) for v in ELEMENTOR_TESTED)
    if have and lo <= have[:len(lo)] <= hi:
        return None
    seen = str(version or "an unknown release")
    return (f"this site runs Elementor {seen}, and the Elementor pack is tested on Elementor {tested_range()} "
            f"only, so Elementor pages are read-only here until a WPBrücke release widens the range")


def is_atomic(element: dict) -> bool:
    """V4 atomic elements: elType or widgetType "e-..." (e-div-block, e-flexbox, e-heading, ...)."""
    return str(element.get("elType") or "").startswith("e-") or str(element.get("widgetType") or "").startswith("e-")


def pattern_for(widget: str, path: str) -> Optional[str]:
    """The EDITABLE pattern a concrete path matches (``icon_list.ab12cd3.text`` -> ``icon_list.*.text``)."""
    parts = str(path).split(".")
    for pattern in EDITABLE.get(widget, ()):
        want = pattern.split(".")
        if len(want) == len(parts) and all(
                re.fullmatch(r"[A-Za-z0-9_-]{1,40}", p) if w == "*" else w == p for w, p in zip(want, parts)):
            return pattern
    return None


def leaf(pattern: str) -> str:
    return pattern.rsplit(".", 1)[-1]


def url_ok(value: str) -> bool:
    text = str(value or "").strip()
    return text == "" or bool(_URL_OK.match(text))
