"""Email bodies for FluentCRM campaigns, in the site's design (v0.5 PK4).

The body is block markup that FluentCRM's campaign editor keeps as separate editable blocks (P43: the
``simple``, ``plain`` and ``classic`` designs; never ``raw_html``). It is built from the templates in
``templates/`` (header, sections, footer, and three parts), each filled with escaped text only. The design
comes from the site's live tokens (``packs.design_tokens``: theme.json, GeneratePress or Elementor, with
the site file's overrides): colours and fonts go into the campaign's ``template_config``, which FluentCRM's
templates apply, and the accent colour into the buttons. Every colour pair is checked against WCAG AA
before it is used; a pair that fails falls back to the darkest text on white.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

TEMPLATES = Path(__file__).resolve().parent / "templates"
DESIGN_TEMPLATES = ("simple", "plain", "classic")
FALLBACK_FONTS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
GENERIC_FAMILIES = ("serif", "sans-serif", "monospace", "cursive", "system-ui")
MAX_SECTIONS = 12
TEXT_MAX = 6000

ACCENT_SLUGS = ("accent", "primary", "accent-1", "brand", "secondary", "accent-2", "vivid-cyan-blue")
TEXT_SLUGS = ("contrast", "foreground", "text", "dark", "contrast-2", "black")
BACKGROUND_SLUGS = ("base", "background", "base-2", "light", "white")

# Blocks the templates write and the block names an email_body may carry (FluentCRM's email editor offers
# them, get-crm-context email_blocks; P43). Anything else in a body is refused before any request.
ALLOWED_BLOCKS = frozenset({
    "core/block", "core/buttons", "core/button", "core/code", "core/columns", "core/column", "core/group",
    "core/row", "core/heading", "core/image", "core/list", "core/list-item", "core/media-text", "core/paragraph",
    "core/preformatted", "core/pullquote", "core/quote", "core/separator", "core/spacer", "core/table",
    "core/verse", "fluentcrm/conditional-group", "fluentcrm/woo-product", "fluent-crm/woo-product"})
_BLOCK_COMMENT = re.compile(r"<!--\s+(/?)wp:([a-z0-9-]+(?:/[a-z0-9-]+)?)(\s[^>]*?)?\s*(/?)-->")


class ComposeError(ValueError):
    pass


def _template(name: str) -> str:
    return (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def fill(name: str, values: dict) -> str:
    """A template with each ``[[key]]`` replaced by its value (already escaped or markup)."""
    out = _template(name)
    for key, value in values.items():
        out = out.replace(f"[[{key}]]", str(value))
    left = re.findall(r"\[\[([a-z_]+)\]\]", out)
    if left:
        raise ComposeError(f"template {name} left unfilled: {', '.join(sorted(set(left)))}")
    return out


def esc(text: Any) -> str:
    """Text for an element or attribute: HTML-escaped; SmartCodes such as {{contact.first_name}} stay."""
    return html.escape(str(text or ""), quote=True)


def safe_url(url: Any, what: str) -> str:
    u = str(url or "").strip()
    if not re.match(r"^(https?://|mailto:|##crm\.[a-z_]+##$|\{\{[a-z_.]+\}\}$)", u, re.I):
        raise ComposeError(f"{what} must be an http(s) or mailto: link, got {u[:80]!r}")
    return esc(u)


# --- colours ----------------------------------------------------------------------------------------------

def parse_color(value: Any) -> Optional[tuple]:
    """(r, g, b) for #rgb, #rrggbb, #rrggbbaa or rgb()/rgba(); None for anything else (var(), names)."""
    v = str(value or "").strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:[\s,/]+[\d.]+%?)?\s*\)", v)
    if m:
        return tuple(min(255, int(x)) for x in m.groups())
    return None


def hex_of(rgb: tuple) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


def luminance(rgb: tuple) -> float:
    def ch(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple, b: tuple) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _saturation(rgb: tuple) -> float:
    hi, lo = max(rgb), min(rgb)
    return 0.0 if hi == 0 else (hi - lo) / hi


WHITE, NEAR_BLACK = (255, 255, 255), (26, 26, 26)


def roles(tokens: dict) -> dict:
    """The email's colours from the site's tokens: text, content background, page background, accent and the
    text on the accent, each ``#rrggbb``; plus the fonts. Pairs below WCAG AA (4.5:1 text, 3:1 headings and
    button text) fall back so the email stays readable."""
    by_slug: dict = {}
    parsed: list = []
    for c in tokens.get("colors") or []:
        rgb = parse_color(c.get("value"))
        if rgb is not None:
            by_slug.setdefault(str(c.get("slug") or "").lower(), rgb)
            parsed.append(rgb)

    def pick(slugs: tuple) -> Optional[tuple]:
        return next((by_slug[s] for s in slugs if s in by_slug), None)

    content_bg = max(parsed, key=luminance) if parsed and luminance(max(parsed, key=luminance)) > 0.8 else WHITE
    page_bg = pick(BACKGROUND_SLUGS)
    if page_bg is None or luminance(page_bg) < 0.6:
        page_bg = content_bg
    text = pick(TEXT_SLUGS) or (min(parsed, key=luminance) if parsed else NEAR_BLACK)
    if contrast(text, content_bg) < 4.5:
        text = NEAR_BLACK
    accent = pick(ACCENT_SLUGS)
    if accent is None:
        colourful = [c for c in parsed if 0.08 < luminance(c) < 0.7]
        accent = max(colourful, key=_saturation) if colourful else text
    on_accent = max((WHITE, text, NEAR_BLACK), key=lambda c: contrast(c, accent))
    heading = accent if contrast(accent, content_bg) >= 3 else text
    link = accent if contrast(accent, content_bg) >= 4.5 else text
    fonts = tokens.get("fonts") or {}
    return {"text": hex_of(text), "content_bg": hex_of(content_bg), "page_bg": hex_of(page_bg),
            "accent": hex_of(accent), "on_accent": hex_of(on_accent), "heading": hex_of(heading),
            "link": hex_of(link), "body_font": font_stack(fonts.get("body")),
            "heading_font": font_stack(fonts.get("headings") or fonts.get("body"))}


def font_stack(font: Any) -> str:
    f = re.sub(r"[;{}<>\"]", "", str(font or "")).strip()
    if not f or f.lower().startswith("var("):
        return FALLBACK_FONTS
    if any(g in f.lower() for g in GENERIC_FAMILIES):
        return f
    return f"{f}, {FALLBACK_FONTS}"


def template_config(r: dict) -> dict:
    """FluentCRM's ``settings.template_config`` keys for the simple, plain and classic designs."""
    return {"body_bg_color": r["page_bg"], "content_bg_color": r["content_bg"], "text_color": r["text"],
            "headings_color": r["heading"], "link_color": r["link"], "footer_text_color": r["text"],
            "headings_font_family": r["heading_font"], "content_font_family": r["body_font"]}


# --- the body ---------------------------------------------------------------------------------------------

def _paragraphs(text: Any) -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    return "".join(fill("part-paragraph", {"text": esc(p).replace("\n", "<br>")}) for p in parts)


def _image(image: Any, what: str) -> str:
    if not image:
        return ""
    if not isinstance(image, dict) or not image.get("url"):
        raise ComposeError(f"{what} is an object with url and alt")
    if not str(image.get("alt") or "").strip():
        raise ComposeError(f"{what} needs alt text (what the image shows), for readers who see no images")
    return fill("part-image", {"src": safe_url(image["url"], f"{what}.url"), "alt": esc(image["alt"])})


def _button(button: Any, r: dict, what: str) -> str:
    if not button:
        return ""
    if not isinstance(button, dict) or not button.get("text") or not button.get("url"):
        raise ComposeError(f"{what} is an object with text and url")
    return fill("part-button", {"text": esc(button["text"]), "url": safe_url(button["url"], f"{what}.url"),
                                "background": r["accent"], "color": r["on_accent"]})


def body(*, headline: str, sections: list, r: dict, site_name: str, site_url: str, intro: str = "",
         logo: Optional[dict] = None) -> str:
    """Header, sections and footer as block markup, every text escaped."""
    if not str(headline or "").strip():
        raise ComposeError("headline (or title) is needed for the email's first heading")
    if not isinstance(sections, list) or not sections:
        raise ComposeError("sections is a list of {heading, text, image?, button?}, at least one")
    if len(sections) > MAX_SECTIONS:
        raise ComposeError(f"at most {MAX_SECTIONS} sections")
    out = [fill("header", {"logo": _image(logo, "logo").lstrip("\n") + "\n\n" if logo else "",
                           "headline": esc(headline), "intro": _paragraphs(intro)})]
    for i, s in enumerate(sections):
        what = f"sections[{i}]"
        if not isinstance(s, dict) or not str(s.get("heading") or "").strip():
            raise ComposeError(f"{what} needs a heading")
        if len(str(s.get("text") or "")) > TEXT_MAX:
            raise ComposeError(f"{what}.text is longer than {TEXT_MAX} characters")
        out.append(fill("section", {"heading": esc(s["heading"]), "image": _image(s.get("image"), f"{what}.image"),
                                    "paragraphs": _paragraphs(s.get("text")),
                                    "button": _button(s.get("button"), r, f"{what}.button")}))
    host = urlparse(site_url).hostname or site_url
    out.append(fill("footer", {"site_name": esc(site_name or host), "site_url": safe_url(site_url, "site url"),
                               "site_host": esc(host)}))
    return "".join(out)


def check_markup(text: Any) -> list:
    """What is wrong with a block-markup body (empty when it is fine): text outside blocks, unknown block
    names, unbalanced comments. FluentCRM's editor turns text outside a block into a Classic block."""
    s = str(text or "")
    problems: list = []
    stack: list = []
    pos = 0
    for m in _BLOCK_COMMENT.finditer(s):
        closing, name, _, self_closing = m.group(1), m.group(2), m.group(3), m.group(4)
        full = name if "/" in name else f"core/{name}"
        if not stack and s[pos:m.start()].strip():
            problems.append("text outside a block (it would open as one Classic block)")
        pos = m.end()
        if full not in ALLOWED_BLOCKS:
            problems.append(f"block {full} is not one FluentCRM's email editor offers")
        if self_closing:
            continue
        if closing:
            if not stack or stack[-1] != full:
                problems.append(f"closing {full} without its opening comment")
            else:
                stack.pop()
        else:
            stack.append(full)
    if s[pos:].strip() and not stack:
        problems.append("text outside a block (it would open as one Classic block)")
    if stack:
        problems.append(f"{stack[-1]} is never closed")
    if "<!-- wp:" not in s:
        problems = ["email_body is not block markup (<!-- wp:paragraph --> ... comments); write sections instead"]
    return list(dict.fromkeys(problems))
