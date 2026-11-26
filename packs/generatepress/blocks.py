"""Block markup for GenerateBlocks 2 and core blocks, written so the block editor keeps it byte for byte.

A page is a tree of ``Node``s; ``serialize`` turns it into post content. The rules come from
measuring the block editor (probe P19): markup written over REST is valid and an editor save writes
the same bytes only when

1. a block never carries empty ``styles`` or ``css`` (PHP stores ``{}`` as ``[]``, the editor then
   rewrites the block);
2. sibling blocks are joined with a blank line (``\\n\\n``), the first child sits right after its
   parent's opening tag;
3. an unstyled ``generateblocks/text`` keeps its ``gb-text-<uid>`` class as attribute ``className``
   (its HTML class stays ``gb-text gb-text-<uid>``).

Block attributes are compact JSON in a fixed key order (uniqueId, tagName, styles, css, className,
htmlAttributes) with WordPress' comment escaping (``--``, ``<``, ``>``, ``&``, ``"`` as ``\\uXXXX``).
Text is escaped like the editor serializes it (``&``, ``<``, ``>``), attribute values like its attribute
escaping (``&``, ``"``, ``>``). UniqueIds are 8 characters ``[a-z0-9]`` derived from a seed and a
counter, so the same input always gives the same markup.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable

GB_KINDS = ("element", "text", "media", "shape")
MODES = ("generateblocks", "core")
SECTION_TYPES = ("hero", "features", "text", "cta", "contact")
_UID_RE = re.compile(r"^[a-z0-9]{8}$")
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


@dataclass
class Node:
    """One block. ``kind`` is a GenerateBlocks kind (element, text, media, shape) or ``core``.

    GenerateBlocks nodes keep ``styles`` and get their uniqueId and css at serialize time; core
    nodes carry their comment attributes and the HTML around their inner blocks ready-made.
    """

    kind: str
    name: str
    tag: str = ""
    styles: dict = field(default_factory=dict)
    content: str = ""
    html_attrs: dict = field(default_factory=dict)
    uid: str | None = None
    children: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    before: str = ""
    after: str = ""


# --- escaping ----------------------------------------------------------------------------------

_LONE_AMP = re.compile(r"&(?!([a-z0-9]+|#[0-9]+|#x[a-f0-9]+);)", re.I)


def esc_text(value: str) -> str:
    """Text content as the editor serializes it: a lone ``&``, every ``<`` and ``>`` (measured, P19 re-run)."""
    return _LONE_AMP.sub("&amp;", str(value)).replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(value: str) -> str:
    """An HTML attribute value as the editor writes it: a lone ``&``, ``"`` and ``>``."""
    return _LONE_AMP.sub("&amp;", str(value)).replace('"', "&quot;").replace(">", "&gt;")


def comment_json(attrs: dict) -> str:
    """Block comment attributes as WordPress' serializer writes them."""
    text = json.dumps(attrs, separators=(",", ":"), ensure_ascii=False)
    return (text.replace("--", "\\u002d\\u002d").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace('\\"', "\\u0022"))


# --- uniqueIds ---------------------------------------------------------------------------------

class Uids:
    """Deterministic uniqueIds: the n-th id of a seed is always the same 8 characters."""

    def __init__(self, seed: str = "wpb", taken: Iterable[str] = ()) -> None:
        self.seed, self.n, self.taken = seed, 0, set(taken)

    def next(self) -> str:
        while True:
            self.n += 1
            num = int.from_bytes(hashlib.sha256(f"{self.seed}:{self.n}".encode()).digest()[:8], "big")
            uid = "".join(_B36[(num // 36 ** i) % 36] for i in range(8))
            if uid not in self.taken:
                self.taken.add(uid)
                return uid


def _check_uid(uid: str | None) -> str | None:
    if uid is not None and not _UID_RE.match(uid):
        raise ValueError(f"uniqueId must be 8 characters a-z0-9, got {uid!r}")
    return uid


# --- css ---------------------------------------------------------------------------------------

def _kebab(prop: str) -> str:
    return prop if prop.startswith("--") else re.sub(r"([A-Z])", r"-\1", prop).lower()


def _box(top: str, right: str, bottom: str, left: str) -> str:
    if top == right == bottom == left:
        return top
    if top == bottom and right == left:
        return f"{top} {right}"
    if right == left:
        return f"{top} {right} {bottom}"
    return f"{top} {right} {bottom} {left}"


def _declarations(styles: dict) -> str:
    flat = {k: str(v) for k, v in styles.items() if not isinstance(v, dict) and v not in (None, "")}
    for prop in ("padding", "margin"):
        sides = [f"{prop}{s}" for s in ("Top", "Right", "Bottom", "Left")]
        if prop not in flat and all(s in flat for s in sides):
            flat[prop] = _box(*(flat.pop(s) for s in sides))
    return ";".join(f"{p}:{v}" for p, v in sorted((_kebab(k), v) for k, v in flat.items()))


def css_for(kind: str, uid: str, styles: dict) -> str:
    """GenerateBlocks css for one block: ``.gb-<kind>-<uid>{...}``, kebab-case, sorted, with the
    padding/margin shorthand when all four sides are given. Nested keys: ``&:hover`` style
    selectors and ``@media`` queries follow the main rule in key order. Empty styles give ``""``."""
    if kind not in GB_KINDS:
        raise ValueError(f"kind must be one of {GB_KINDS}, got {kind!r}")
    sel = f".gb-{kind}-{uid}"
    out = []
    main = _declarations(styles or {})
    if main:
        out.append(f"{sel}{{{main}}}")
    for key in sorted(k for k, v in (styles or {}).items() if isinstance(v, dict)):
        inner = _declarations(styles[key])
        if not inner:
            continue
        if key.startswith("@"):
            out.append(f"{key}{{{sel}{{{inner}}}}}")
        else:
            out.append(f"{key.replace('&', sel)}{{{inner}}}")
    return "".join(out)


# --- GenerateBlocks nodes ----------------------------------------------------------------------

def element(tag: str = "div", styles: dict | None = None, children: Iterable[Node] = (), uid: str | None = None,
            html_attrs: dict | None = None) -> Node:
    """``generateblocks/element``: a container (section, div, ...) around other blocks."""
    return Node("element", "generateblocks/element", tag, dict(styles or {}), html_attrs=dict(html_attrs or {}),
                uid=_check_uid(uid), children=list(children))


def text(tag: str = "p", content: str = "", styles: dict | None = None, href: str | None = None,
         uid: str | None = None) -> Node:
    """``generateblocks/text``: a heading, paragraph or (with ``href``, tag ``a``) a link button.
    ``content`` is plain text and gets escaped."""
    attrs = {"href": href} if href is not None else {}
    return Node("text", "generateblocks/text", tag, dict(styles or {}), content=content, html_attrs=attrs,
                uid=_check_uid(uid))


def media(src: str, alt: str, styles: dict | None = None, uid: str | None = None) -> Node:
    """``generateblocks/media``: an image with its alt text."""
    return Node("media", "generateblocks/media", "img", dict(styles or {}), html_attrs={"src": src, "alt": alt},
                uid=_check_uid(uid))


def shape(svg: str, styles: dict | None = None, uid: str | None = None) -> Node:
    """``generateblocks/shape``: an inline SVG (passed through as markup, so only trusted SVG)."""
    return Node("shape", "generateblocks/shape", "span", dict(styles or {}), content=svg, uid=_check_uid(uid))


# --- core nodes --------------------------------------------------------------------------------

def _color_parts(text_color: str | None, background: str | None) -> tuple[dict, str, str]:
    """Core's custom colours: (style attribute, extra classes, inline style), in WordPress' order."""
    color, classes, css = {}, [], []
    if text_color:
        color["text"] = text_color
        classes.append("has-text-color")
        css.append(f"color:{text_color}")
    if background:
        color["background"] = background
        classes.append("has-background")
        css.append(f"background-color:{background}")
    return ({"color": color} if color else {}), " ".join(classes), ";".join(css)


def _class_style(base: str, extra: str, style: str) -> str:
    cls = " ".join(c for c in (base, extra) if c)
    out = f' class="{esc_attr(cls)}"' if cls else ""
    return out + (f' style="{esc_attr(style)}"' if style else "")


def core_group(children: Iterable[Node] = (), layout: str = "constrained", background: str | None = None,
               text_color: str | None = None) -> Node:
    style, extra, css = _color_parts(text_color, background)
    attrs = {}
    if style:
        attrs["style"] = style
    attrs["layout"] = {"type": layout}
    return Node("core", "core/group", attrs=attrs, children=list(children),
                before=f"<div{_class_style('wp-block-group', extra, css)}>", after="</div>")


def core_heading(content: str, level: int = 2, color: str | None = None) -> Node:
    if level not in range(1, 7):
        raise ValueError("heading level must be 1 to 6")
    style, extra, css = _color_parts(color, None)
    attrs = {}
    if level != 2:
        attrs["level"] = level
    if style:
        attrs["style"] = style
    return Node("core", "core/heading", attrs=attrs,
                before=f"<h{level}{_class_style('wp-block-heading', extra, css)}>{esc_text(content)}</h{level}>")


def core_paragraph(content: str, color: str | None = None) -> Node:
    style, extra, css = _color_parts(color, None)
    return Node("core", "core/paragraph", attrs=({"style": style} if style else {}),
                before=f"<p{_class_style('', extra, css)}>{esc_text(content)}</p>")


def core_column(children: Iterable[Node] = ()) -> Node:
    return Node("core", "core/column", children=list(children), before='<div class="wp-block-column">', after="</div>")


def core_columns(columns: Iterable) -> Node:
    """``columns``: each entry is a ``core_column`` node or a list of nodes for one column."""
    cols = [c if isinstance(c, Node) else core_column(c) for c in columns]
    return Node("core", "core/columns", children=cols, before='<div class="wp-block-columns">', after="</div>")


def core_button(label: str, href: str, background: str | None = None, color: str | None = None) -> Node:
    style, extra, css = _color_parts(color, background)
    cls = " ".join(c for c in ("wp-block-button__link", extra, "wp-element-button") if c)
    style_attr = f' style="{esc_attr(css)}"' if css else ""
    return Node("core", "core/button", attrs=({"style": style} if style else {}),
                before=(f'<div class="wp-block-button"><a class="{cls}" href="{esc_attr(href)}"{style_attr}>'
                        f"{esc_text(label)}</a></div>"))


def core_buttons(buttons: Iterable) -> Node:
    """``buttons``: ``core_button`` nodes or ``{"label", "href"}`` dicts."""
    kids = [b if isinstance(b, Node) else core_button(b["label"], b["href"]) for b in buttons]
    return Node("core", "core/buttons", children=kids, before='<div class="wp-block-buttons">', after="</div>")


def core_image(src: str, alt: str) -> Node:
    return Node("core", "core/image",
                before=f'<figure class="wp-block-image"><img src="{esc_attr(src)}" alt="{esc_attr(alt)}"/></figure>')


# --- serializer --------------------------------------------------------------------------------

def _walk(nodes: Iterable[Node]):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def _gb_parts(node: Node, uid: str) -> tuple[dict, str, str]:
    attrs = {"uniqueId": uid, "tagName": node.tag}
    css = css_for(node.kind, uid, node.styles) if node.styles else ""
    if node.styles and css:  # rule 1: never empty styles or css
        attrs["styles"], attrs["css"] = node.styles, css
    if node.kind == "text":
        cls = f"gb-text gb-text-{uid}"
        if "styles" not in attrs:  # rule 3: an unstyled text keeps its uid class as className
            attrs["className"] = f"gb-text-{uid}"
    else:
        cls = f"gb-{node.kind}-{uid}"
    if node.html_attrs:
        attrs["htmlAttributes"] = node.html_attrs
    extra = "".join(f' {k}="{esc_attr(v)}"' for k, v in node.html_attrs.items())
    open_tag = f'<{node.tag} class="{cls}"{extra}'
    if node.kind == "media":
        return attrs, open_tag + "/>", ""
    if node.kind == "shape":
        return attrs, f"{open_tag}>{node.content}", f"</{node.tag}>"
    if node.kind == "text":
        return attrs, f"{open_tag}>{esc_text(node.content)}", f"</{node.tag}>"
    return attrs, open_tag + ">", f"</{node.tag}>"


def _block(node: Node, uids: dict) -> str:
    if node.kind == "core":
        attrs, before, after = node.attrs, node.before, node.after
    else:
        attrs, before, after = _gb_parts(node, uids[id(node)])
    short = node.name[5:] if node.name.startswith("core/") else node.name
    head = f"<!-- wp:{short} {comment_json(attrs)} -->" if attrs else f"<!-- wp:{short} -->"
    inner = "\n\n".join(_block(c, uids) for c in node.children)  # rule 2
    return f"{head}\n{before}{inner}{after}\n<!-- /wp:{short} -->"


def serialize(nodes, seed: str = "wpb") -> str:
    """Post content for a node or a list of top-level nodes. Nodes without a uid get the next
    deterministic id of ``seed`` in document order; explicit uids must be unique."""
    nodes = [nodes] if isinstance(nodes, Node) else list(nodes)
    gb = [n for n in _walk(nodes) if n.kind != "core"]
    fixed = [n.uid for n in gb if n.uid]
    if len(fixed) != len(set(fixed)):
        raise ValueError("duplicate uniqueId in the tree")
    gen = Uids(seed, fixed)
    uids = {id(n): n.uid or gen.next() for n in gb}
    return "\n\n".join(_block(n, uids) for n in nodes)


# --- the Q1 pages (probe P19) ------------------------------------------------------------------

def q1_patterns() -> dict:
    """The four Q1 probe pages, built with this serializer (live/q1 re-run with Q1_SOURCE=plugin)."""
    p15 = element("section", {"paddingTop": "48px", "paddingBottom": "48px", "backgroundColor": "#F7F3EC"},
                  [text("h2", "wpb-test heading", {"color": "#C4472B"}, uid="wpbq0002")], uid="wpbq0001")
    cards = [element("div", {"padding": "16px", "borderRadius": "6px"},
                     [text("h3", f"Card {n}", uid=f"wpbqc{n}b0"), text("p", f"Text {n}", uid=f"wpbqc{n}c0")],
                     uid=f"wpbqc{n}a0") for n in range(1, 4)]
    grid = element("div", {"display": "grid", "gridTemplateColumns": "repeat(3, minmax(0, 1fr))", "columnGap": "16px"},
                   cards, uid="wpbqg001")
    button = text("a", "Order now", {"display": "inline-flex", "paddingTop": "10px", "paddingBottom": "10px",
                                     "paddingLeft": "20px", "paddingRight": "20px", "backgroundColor": "#C4472B",
                                     "color": "#ffffff"}, href="#order", uid="wpbqb001")
    core = core_group([core_heading("wpb-test core heading"), core_paragraph("Plain paragraph."),
                       core_columns([[core_paragraph("Left")], [core_paragraph("Right")]]),
                       core_buttons([{"label": "Order now", "href": "#order"}])])
    return {name: serialize(n) for name, n in (("p15", p15), ("grid", grid), ("button", button), ("core", core))}


# --- section plans -----------------------------------------------------------------------------

def _hex_rgb(color: str) -> tuple[float, float, float] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", (color or "").strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(color: str) -> float | None:
    rgb = _hex_rgb(color)
    if rgb is None:
        return None
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a: str, b: str) -> float | None:
    la, lb = _luminance(a), _luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def palette(brand: dict) -> dict:
    """Roles from a brand's colours (``slug`` accent, contrast, base, text; else the first colour
    is the accent): accent, base, text, contrast and on_accent (base or text, whichever reads
    better on the accent)."""
    colors = {c.get("slug"): c.get("color") for c in (brand or {}).get("colors") or [] if c.get("color")}
    first = next(iter(colors.values()), None)
    accent = colors.get("accent") or first or "#1e5aa8"
    base = colors.get("base") or "#ffffff"
    txt = colors.get("text") or colors.get("contrast") or "#222222"
    on = max((base, txt), key=lambda c: contrast_ratio(c, accent) or 0)
    return {"accent": accent, "base": base, "text": txt, "contrast": colors.get("contrast") or txt, "on_accent": on}


def _paras(body) -> list[str]:
    if not body:
        return []
    if isinstance(body, (list, tuple)):
        return [str(b) for b in body if str(b).strip()]
    return [p.strip() for p in str(body).split("\n\n") if p.strip()]


def _item(it) -> tuple[str, str]:
    if isinstance(it, dict):
        return str(it.get("heading") or it.get("title") or ""), str(it.get("body") or it.get("text") or "")
    return str(it), ""


def _check_plan(plan: list[dict], mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not isinstance(plan, list) or not plan:
        raise ValueError("a section plan is a non-empty list of sections")
    for n, sec in enumerate(plan):
        if not isinstance(sec, dict) or sec.get("type") not in SECTION_TYPES:
            raise ValueError(f"section {n}: type must be one of {SECTION_TYPES}")
        btn = sec.get("button")
        if btn is not None and not (isinstance(btn, dict) and btn.get("label") and btn.get("href")):
            raise ValueError(f"section {n}: a button needs label and href")


def _gb_section(sec: dict, pal: dict) -> Node:
    kind = sec["type"]
    dark = kind == "cta"
    bg, fg = (pal["accent"], pal["on_accent"]) if dark else (pal["base"], pal["text"])
    pad = "80px" if kind == "hero" else "56px"
    kids: list[Node] = []
    if sec.get("heading"):
        level = sec.get("level") or (1 if kind == "hero" else 2)
        kids.append(text(f"h{level}", sec["heading"], {"color": fg, "marginBottom": "16px"}))
    for p in _paras(sec.get("body")):
        kids.append(text("p", p, {"color": fg}))
    items = [_item(i) for i in sec.get("items") or []]
    if items and kind == "features":
        cols = min(len(items), 3)
        cards = [element("div", {"backgroundColor": "#ffffff" if pal["base"].lower() != "#ffffff" else "#f5f5f5",
                                 "borderRadius": "6px", "padding": "24px"},
                         [text("h3", h)] + ([text("p", b)] if b else [])) for h, b in items]
        kids.append(element("div", {"display": "grid", "gridTemplateColumns": f"repeat({cols}, minmax(0, 1fr))",
                                    "columnGap": "24px", "rowGap": "24px",
                                    "@media (max-width:767px)": {"gridTemplateColumns": "1fr"}}, cards))
    elif items:
        kids.extend(text("p", " ".join(x for x in it if x), {"color": fg}) for it in items)
    if sec.get("image"):
        kids.append(media(sec["image"]["src"], sec["image"].get("alt", ""), {"height": "auto", "maxWidth": "100%"}))
    btn = sec.get("button")
    if btn:
        bbg, bfg = (pal["on_accent"], pal["accent"]) if dark else (pal["accent"], pal["on_accent"])
        kids.append(text("a", btn["label"], {"display": "inline-flex", "paddingTop": "12px", "paddingBottom": "12px",
                                             "paddingLeft": "24px", "paddingRight": "24px", "backgroundColor": bbg,
                                             "color": bfg, "borderRadius": "4px", "textDecoration": "none"},
                         href=btn["href"]))
    inner = element("div", {"marginLeft": "auto", "marginRight": "auto", "maxWidth": "1100px"}, kids)
    return element("section", {"backgroundColor": bg, "paddingTop": pad, "paddingBottom": pad,
                               "paddingLeft": "20px", "paddingRight": "20px"}, [inner])


def _core_section(sec: dict, pal: dict) -> Node:
    kind = sec["type"]
    dark = kind == "cta"
    kids: list[Node] = []
    if sec.get("heading"):
        kids.append(core_heading(sec["heading"], sec.get("level") or (1 if kind == "hero" else 2)))
    kids.extend(core_paragraph(p) for p in _paras(sec.get("body")))
    items = [_item(i) for i in sec.get("items") or []]
    if items and kind == "features":
        kids.append(core_columns([[core_heading(h, 3)] + ([core_paragraph(b)] if b else []) for h, b in items]))
    elif items:
        kids.extend(core_paragraph(" ".join(x for x in it if x)) for it in items)
    if sec.get("image"):
        kids.append(core_image(sec["image"]["src"], sec["image"].get("alt", "")))
    btn = sec.get("button")
    if btn:
        bbg, bfg = (pal["on_accent"], pal["accent"]) if dark else (pal["accent"], pal["on_accent"])
        kids.append(core_buttons([core_button(btn["label"], btn["href"], background=bbg, color=bfg)]))
    if dark:
        return core_group(kids, background=pal["accent"], text_color=pal["on_accent"])
    return core_group(kids, background=pal["base"] if kind == "hero" else None)


def page_from_section_plan(plan: list[dict], brand: dict, mode: str, seed: str = "wpb") -> str:
    """Post content for a section plan ``[{type: hero|features|text|cta|contact, heading, body,
    items[], button{label, href}, image{src, alt}?, level?}]`` in ``generateblocks`` or ``core``
    mode, coloured from the brand (``palette``). ``body`` is a string (blank line = new paragraph)
    or a list; ``items`` are strings or ``{heading, body}``. Same input, same seed: same bytes."""
    _check_plan(plan, mode)
    pal = palette(brand)
    build = _gb_section if mode == "generateblocks" else _core_section
    return serialize([build(sec, pal) for sec in plan], seed=seed)
