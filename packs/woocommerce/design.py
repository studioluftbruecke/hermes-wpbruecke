"""Product descriptions in the site's design (D34): a short HTML block from the live design tokens.

The agent sends the words (``body``: heading, lead, points, closing); this module escapes them and lays
them out with the site's accent colour and heading font, sparingly: the heading carries both (the colour
only where it reads on the page background, contrast 4.5 or more), everything
else is plain HTML the theme styles. Plain HTML rather than block markup, because WooCommerce's product
screen edits descriptions in the classic editor. Tokens are read live (``packs.design_tokens``), never
copied into a file; without tokens the layout is the same, uncoloured.
"""
from __future__ import annotations

import html
import re
from typing import Optional

BODY_KEYS = ("heading", "lead", "points", "closing")
MAX_TEXT = 600
MAX_POINTS = 8
ACCENT_FIRST = ("accent", "primary")
ACCENT_LAST = ("secondary", "contrast", "foreground")
BACKGROUND_SLUGS = ("base", "background", "white")
MIN_CONTRAST = 4.5   # WCAG AA for body-size text; the heading must read on the page background
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_FONT = re.compile(r"^[A-Za-z0-9 ,'\"_-]{1,120}$")

SCHEMA = {"type": "object", "additionalProperties": False,
          "description": ("The description's words; WPBrücke lays them out in the site's design (accent colour and "
                          "heading font from the live theme or builder) and sends HTML. Give body or description."),
          "properties": {"heading": {"type": "string", "maxLength": MAX_TEXT},
                         "lead": {"type": "string", "maxLength": MAX_TEXT},
                         "points": {"type": "array", "maxItems": MAX_POINTS,
                                    "items": {"type": "string", "maxLength": MAX_TEXT}},
                         "closing": {"type": "string", "maxLength": MAX_TEXT}}}


class BadBody(ValueError):
    pass


def _luminance(value: str) -> float:
    h = value.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _colors(tokens: dict) -> list:
    return [c for c in (tokens or {}).get("colors") or [] if isinstance(c, dict) and _HEX.match(str(c.get("value")))]


def background(tokens: dict) -> str:
    """The page background the heading sits on: the palette's base or background entry, else white."""
    colors = _colors(tokens)
    return next((str(c["value"]) for slug in BACKGROUND_SLUGS for c in colors if c.get("slug") == slug), "#FFFFFF")


def accent(tokens: dict) -> Optional[dict]:
    """The colour the heading takes: an accent-like palette entry that reads on the page background
    (contrast >= 4.5); accent and primary first, then accent-1, accent-2, ... in palette order, then
    secondary, contrast, foreground. None when no entry reads."""
    colors, page = _colors(tokens), background(tokens)
    by_slug = {str(c.get("slug")): c for c in colors}
    order = [s for s in ACCENT_FIRST if s in by_slug]
    order += [str(c.get("slug")) for c in colors if str(c.get("slug")).startswith("accent-")]
    order += [s for s in ACCENT_LAST if s in by_slug]
    for slug in order:
        if contrast(str(by_slug[slug]["value"]), page) >= MIN_CONTRAST:
            return by_slug[slug]
    return None


def heading_font(tokens: dict) -> Optional[str]:
    fonts = (tokens or {}).get("fonts") or {}
    font = str(fonts.get("headings") or fonts.get("body") or "").strip()
    return font.replace('"', "'") if font and _FONT.match(font) else None


def _text(value, key: str) -> str:
    if not isinstance(value, str):
        raise BadBody(f"body.{key} must be text")
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise BadBody(f"body.{key} is longer than {MAX_TEXT} characters")
    return value


def check(body) -> dict:
    """The body's fields, validated; BadBody names the first problem."""
    if not isinstance(body, dict):
        raise BadBody("body must be an object with heading, lead, points and closing")
    extra = sorted(k for k in body if k not in BODY_KEYS)
    if extra:
        raise BadBody(f"body does not take {', '.join(extra)}; it takes {', '.join(BODY_KEYS)}")
    out: dict = {k: _text(body.get(k) or "", k) for k in ("heading", "lead", "closing")}
    points = body.get("points") or []
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        raise BadBody(f"body.points must be a list of at most {MAX_POINTS} texts")
    out["points"] = [p for p in (_text(p, "points") for p in points) if p]
    if not any(out.values()):
        raise BadBody("body is empty")
    return out


def render(body, tokens: dict) -> "tuple[str, dict]":
    """(HTML, what of the design it used). Every text is escaped: the agent's words are data."""
    b = check(body)
    color, font = accent(tokens), heading_font(tokens)
    parts = []
    if b["heading"]:
        style = ";".join(s for s in (f"color:{color['value']}" if color else "",
                                     f"font-family:{font}" if font else "") if s)
        attr = f' style="{style}"' if style else ""   # only checked hex and font characters, no double quote
        parts.append(f"<h3{attr}>{html.escape(b['heading'], quote=False)}</h3>")
    if b["lead"]:
        parts.append(f"<p>{html.escape(b['lead'], quote=False)}</p>")
    if b["points"]:
        items = "".join(f"<li>{html.escape(p, quote=False)}</li>" for p in b["points"])
        parts.append(f"<ul>{items}</ul>")
    if b["closing"]:
        parts.append(f"<p>{html.escape(b['closing'], quote=False)}</p>")
    used = {"color": {"slug": color.get("slug"), "value": color.get("value")} if color else None,
            "background": background(tokens),
            "heading_font": font, "source": (tokens or {}).get("source"),
            "overridden": list((tokens or {}).get("overridden") or [])}
    if (tokens or {}).get("error"):
        used["tokens_error"] = str(tokens["error"])[:200]
    return "\n".join(parts), used
