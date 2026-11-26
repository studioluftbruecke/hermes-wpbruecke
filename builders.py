"""Which page builder made a post, and whether WPBrücke may change its body (plan WG4, D20, D21).

``detect(post, facts, site)`` reads, strongest first:

1. ``connector``: the WPBrücke connector's REST field ``wpbruecke_builder`` (``includes/builders.php``),
   registered for ``context=edit`` only and filled from post meta read server side;
2. ``rest_meta``: builder meta WordPress itself opens to REST in the edit context (Elementor registers
   ``_elementor_edit_mode`` and its other keys, P25; a site may expose others);
3. ``markup``: markers in the stored body (Divi 4 shortcodes, Divi 5 blocks, WPBakery shortcodes) or in
   the rendered body.

Beaver Builder, Bricks, Oxygen and Breakdance keep the layout in meta REST does not show and render it
outside the REST body (probe Q11), so without the connector field a page on a site that runs one of them
is ``unknown_builder`` unless its body is block markup: the guard then refuses a body change rather than
write content no visitor sees.

A builder page's body is editable only through its pack (``packs.for_builder``) when that pack exists and
is on for the site; title, excerpt, SEO fields and the featured image are core fields and always work.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

from . import packs as packs_mod

BUILDERS = ("blocks", "classic", "elementor", "divi", "wpbakery", "beaver", "bricks", "oxygen", "breakdance",
            "unknown_builder")
CORE = ("blocks", "classic")
LABELS = {"blocks": "the block editor", "classic": "the classic editor", "elementor": "Elementor", "divi": "Divi",
          "wpbakery": "WPBakery", "beaver": "Beaver Builder", "bricks": "Bricks", "oxygen": "Oxygen",
          "breakdance": "Breakdance", "unknown_builder": "a page builder WPBrücke cannot name"}
SERVER_ONLY = ("beaver", "bricks", "oxygen", "breakdance")
STILL_WORKS = "title, excerpt, SEO fields and the featured image"


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() not in ("", "0", "false", "off", "no", "none", "null", "[]", "{}")


def _nonempty(v: Any) -> bool:
    return v not in (None, "", [], {}) and _truthy(v)


# (meta key, builder, test): keys seen in public samples of each builder (probe Q11)
META = (
    ("_elementor_edit_mode", "elementor", lambda v: str(v) == "builder"),
    ("_et_pb_use_builder", "divi", lambda v: str(v) == "on"),
    ("_wpb_vc_js_status", "wpbakery", lambda v: str(v).lower() == "true"),
    ("_fl_builder_enabled", "beaver", _truthy),
    ("_bricks_editor_mode", "bricks", lambda v: str(v) == "bricks"),
    ("_bricks_page_content_2", "bricks", _nonempty),
    ("_breakdance_data", "breakdance", _nonempty),
    ("_oxygen_data", "oxygen", _nonempty),
    ("ct_builder_json", "oxygen", _nonempty),
    ("_ct_builder_json", "oxygen", _nonempty),
    ("ct_builder_shortcodes", "oxygen", _nonempty),
    ("_ct_builder_shortcodes", "oxygen", _nonempty),
)
# markers in the stored body (content.raw)
RAW = (
    ("divi", re.compile(r"\[et_pb_[a-z_]+"), "marker [et_pb_"),
    ("divi", re.compile(r"<!--\s+wp:divi/"), "marker <!-- wp:divi/"),
    ("wpbakery", re.compile(r"\[vc_row[\s\]]"), "marker [vc_row"),
)
# markers in the rendered body; most builders render outside the REST body, these catch the ones that do not
RENDERED = (
    ("elementor", re.compile(r"\bdata-elementor-type="), "rendered data-elementor-type"),
    ("divi", re.compile(r"class=\"[^\"]*\bet-l--post\b"), "rendered et-l--post"),
    ("divi", re.compile(r"class=\"[^\"]*\bet_pb_section\b"), "rendered et_pb_section"),
    ("wpbakery", re.compile(r"class=\"[^\"]*\bvc_row\b[^\"]*\bwpb_row\b"), "rendered vc_row wpb_row"),
    ("beaver", re.compile(r"class=\"[^\"]*\bfl-builder-content\b"), "rendered fl-builder-content"),
    ("bricks", re.compile(r"\b(?:id|class)=\"[^\"]*\bbrxe-[a-z0-9]"), "rendered brxe-"),
    ("oxygen", re.compile(r"class=\"[^\"]*\bct-section\b"), "rendered ct-section"),
    ("breakdance", re.compile(r"class=\"[^\"]*\bbde-[a-z]"), "rendered bde-"),
)
# site-level hints from public facts: REST namespaces and asset folders in the home page
_HINT_NAMESPACES = {"elementor/v1": "elementor", "bricks/v1": "bricks"}
_HINT_PLUGINS = {"elementor": "elementor", "elementor-pro": "elementor", "js_composer": "wpbakery",
                 "divi-builder": "divi", "bb-plugin": "beaver", "beaver-builder-lite-version": "beaver",
                 "oxygen": "oxygen", "breakdance": "breakdance"}
_HINT_THEMES = {"divi": "divi", "extra": "divi", "bricks": "bricks"}


def site_hints(facts: Optional[dict]) -> list:
    """Builder ids the site runs, from its public facts (no request). Sorted, no duplicates."""
    facts = facts or {}
    seo = facts.get("seo_public") or {}
    found = set()
    for ns in facts.get("namespaces") or []:
        if ns in _HINT_NAMESPACES:
            found.add(_HINT_NAMESPACES[ns])
    plugins = [str(p).lower() for p in seo.get("plugins_in_html") or []]
    plugins += [str(p.get("plugin") or "").split("/")[0].lower() for p in facts.get("plugins") or []
                if isinstance(p, dict) and p.get("status") == "active"]
    for slug in plugins:
        if slug in _HINT_PLUGINS:
            found.add(_HINT_PLUGINS[slug])
    for slug in [str(facts.get("theme") or "")] + [str(t) for t in seo.get("themes_in_html") or []]:
        if slug.lower() in _HINT_THEMES:
            found.add(_HINT_THEMES[slug.lower()])
    return sorted(found)


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= 24 else f"<{len(text)} characters>"


def _body(post: dict, key: str) -> Optional[str]:
    v = (post.get("content") or {}).get(key) if isinstance(post.get("content"), dict) else None
    return v if isinstance(v, str) else None


def _make(bid: str, evidence: list, source: str, site: Optional[dict]) -> dict:
    spec = packs_mod.for_builder(bid)
    pack = spec.id if spec is not None and packs_mod.is_on(site, spec.id) else None
    return {"id": bid, "pack": pack, "evidence": [str(e)[:160] for e in evidence][:6], "source": source}


def detect(post: Optional[dict], facts: Optional[dict] = None, site: Optional[dict] = None) -> dict:
    """The plan's Builder shape: {id, pack, evidence, source}."""
    post = post if isinstance(post, dict) else {}
    field = post.get("wpbruecke_builder")
    if isinstance(field, dict) and str(field.get("id")) in BUILDERS:
        bid = str(field.get("id"))
        ev = [str(e) for e in field.get("evidence") or []] or [f"connector says {bid}"]
        return _make(bid, ev, "connector", site)
    meta: dict = post["meta"] if isinstance(post.get("meta"), dict) else {}
    hits = [(bid, f"meta {key}={_short(meta[key])}") for key, bid, test in META if key in meta and test(meta[key])]
    if hits:
        first = hits[0][0]
        return _make(first, [e for b, e in hits if b == first], "rest_meta", site)
    raw, rendered = _body(post, "raw"), _body(post, "rendered") or ""
    marks = [(bid, ev) for bid, rx, ev in RAW if raw and rx.search(raw)]
    marks += [(bid, ev) for bid, rx, ev in RENDERED if rx.search(rendered)]
    if marks:
        first = marks[0][0]
        return _make(first, [e for b, e in marks if b == first], "markup", site)
    if raw is not None:
        blocks = "<!-- wp:" in raw
        empty = not raw.strip()
    else:
        blocks = "wp-block-" in rendered
        empty = not rendered.strip()
    hinted = [h for h in site_hints(facts) if h in SERVER_ONLY]
    if hinted and not blocks:
        return _make("unknown_builder", [f"the site runs {', '.join(LABELS[h] for h in hinted)}",
                                         "no wpbruecke_builder field: only the WPBrücke connector reads that "
                                         "builder's meta", "the body holds no block markup"], "markup", site)
    if blocks or empty:
        return _make("blocks", ["block markup" if blocks else "empty body"], "markup", site)
    return _make("classic", ["no block markup and no builder marker"], "markup", site)


def body_editable(builder: dict) -> bool:
    return builder.get("id") in CORE or bool(builder.get("pack"))


def refusal(builder: dict, what: str) -> str:
    """The builder_page message: names the builder, why, and what still works (D20 a)."""
    bid = str(builder.get("id") or "")
    label = LABELS.get(bid, bid)
    because = "; ".join(builder.get("evidence") or [])
    if bid == "unknown_builder":
        head = (f"{what} looks like a page builder layout WPBrücke cannot name ({because}). Install the WPBrücke "
                "connector so it can read the builder on the site, or edit the body in WordPress.")
    else:
        spec = packs_mod.for_builder(bid)
        why = (f"the {spec.label} pack is off for this site (packs.{spec.id}: true switches it on; the person "
               f"approves that). {spec.scope}" if spec is not None else f"WPBrücke has no {label} pack yet")
        head = f"{what} is built with {label} ({because}), and {why}."
    return (f"{head} Its body is read-only here: a content change would not show or would break the layout. "
            f"Still possible: {STILL_WORKS} (call wp_draft without content). Nothing was sent.")


# --- blocks an edit would drop (D21 a) ----------------------------------------------------------------

_BLOCK_RE = re.compile(r"<!--\s+wp:([a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)?)(?=[\s/]|-->)")


def block_names(markup: Optional[str]) -> Counter:
    out: Counter = Counter()
    for m in _BLOCK_RE.finditer(markup or ""):
        name = m.group(1)
        out[name if "/" in name else f"core/{name}"] += 1
    return out


def _named(name: str, allowed: list) -> bool:
    ns = name.split("/", 1)[0]
    return any(a == name or a in (ns, f"{ns}/*") for a in allowed)


def dropped_blocks(old: Optional[str], new: Optional[str], known_namespaces: set, allowed: Any = None) -> dict:
    """Blocks of a namespace Hermes does not know that ``new`` has fewer of than ``old`` (name -> count
    lost), minus those the call names in ``drop_blocks`` (exact name, ``ns`` or ``ns/*``)."""
    allowed = [str(a).strip().lower() for a in (allowed or []) if str(a).strip()]
    before, after = block_names(old), block_names(new)
    out = {}
    for name, n in sorted(before.items()):
        lost = n - after.get(name, 0)
        if lost > 0 and name.split("/", 1)[0] not in known_namespaces and not _named(name, allowed):
            out[name] = lost
    return out


def dropped_message(dropped: dict, what: str) -> str:
    names = ", ".join(f"{k} ({v})" for k, v in dropped.items())
    return (f"This change to {what} would drop blocks WPBrücke does not know: {names}. Keep them in content, or "
            "name them in drop_blocks to drop them on purpose. Nothing was sent.")
