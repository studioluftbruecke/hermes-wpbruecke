"""SEO post check (F9): title, meta description, headings, internal links, alt text, slug, readability.

Input is a post as WordPress REST gives it (title and content may hold HTML and entities):
``{"title", "slug", "content_html", "excerpt", "images": [{"src", "alt"}]}``. The description is
passed separately because it lives in a meta key that depends on the site (``fields.py``).

Weights (sum 100): title 15, description 20, headings 10, internal_links 15, alt 15, slug 10,
readability 15. Score rule: see ``seo/__init__.py`` (match = full weight, differs = 0, not_proven
left out and the rest scaled to 100).

Rules and their sources:

- Title: 30 to 60 characters (search results cut longer titles near 60). Too long: a shorter title
  (the part before a colon or dash, else cut at a word) as fix ``title``.
- Description: 70 to 160 characters. Missing or out of range: one built from the first paragraph,
  at most 155 characters, cut at a sentence end or a word, as fix ``description``.
- Headings: the theme prints the post title as the page's H1 (block themes, GeneratePress), so an
  H1 inside the content is a second H1; heading levels go down one step at a time (H2, H3, not H2, H4).
- Internal links: links to the site's own host. None: up to two related posts (shared words with the
  title and text) as fix ``internal_links``.
- Image alt text: images from ``images`` and ``<img>`` in the content, deduplicated by src.
- Slug: lowercase, a-z 0-9 and hyphens, at most 60 characters, not more stop words than words.
- Readability (D16): Flesch Reading Ease for English (206.835 - 1.015 * words per sentence - 84.6 *
  syllables per word), Flesch-Amstad for German (180 - words per sentence - 58.5 * syllables per
  word); 50 or more is a match. Syllables come from a vowel-group heuristic per language (German
  counts umlauts and the diphthongs ei, ie, au, eu, äu, ai and doubled vowels as one). Headings and
  code are left out of the text. Other languages: not_proven "no formula for <lang>".
"""
from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from . import row, score

WEIGHTS = {"title": 15, "description": 20, "headings": 10, "internal_links": 15, "alt": 15, "slug": 10,
           "readability": 15}
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX, DESC_PROPOSE_MAX = 70, 160, 155
SLUG_MAX = 60
READABILITY_MIN = 50
MIN_WORDS = 40
THEME_PRINTS_TITLE_H1 = True

STOP = {
    "en": set("a an the and or but of to in on at for with by from as is are was were be been it its this that "
              "these those i me my we our you your he she they them his her their into about over than then so "
              "if not no do does did can will just".split()),
    "de": set("der die das den dem des ein eine einen einem einer eines und oder aber mit von vom zu zum zur im in "
              "am an auf aus bei nach für fuer ist sind war waren sein es sich nicht auch wie was ich du er sie "
              "wir ihr ihre mein meine dein so als um dass da".split()),
}
ALL_STOP = STOP["en"] | STOP["de"]
_BLOCK = {"p", "div", "li", "ul", "ol", "blockquote", "figcaption", "td", "th", "tr", "section", "article",
          "header", "footer", "table", "figure", "br", "hr"}
_SKIP = {"script", "style", "pre", "code", "noscript", "svg"}
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


class _Content(HTMLParser):
    """Headings, links, images, paragraphs and running text of a post body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list = []      # [(level, text)]
        self.links: list = []         # [href]
        self.images: list = []        # [{"src", "alt"}]
        self.paragraphs: list = []
        self.blocks: list = []        # running text blocks, headings excluded
        self._buf: list = []
        self._p: list | None = None
        self._h: tuple | None = None  # (level, [text])
        self._skip = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.blocks.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP:
            self._skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._h = (int(tag[1]), [])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"].strip())
        elif tag == "img":
            self.images.append({"src": a.get("src") or a.get("data-src") or "", "alt": a.get("alt", "")})
        if tag == "p":
            self._flush()
            self._p = []
        elif tag in _BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in _SKIP:
            self._skip -= 1

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) and self._h:
            self.headings.append((self._h[0], " ".join("".join(self._h[1]).split())))
            self._h = None
        elif tag == "p" and self._p is not None:
            text = " ".join("".join(self._p).split())
            if text:
                self.paragraphs.append(text)
            self._p = None
            self._flush()
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._h is not None:
            self._h[1].append(data)
            return
        self._buf.append(data)
        if self._p is not None:
            self._p.append(data)

    def close(self):
        super().close()
        self._flush()


def parse_content(content_html: str) -> _Content:
    p = _Content()
    try:
        p.feed(content_html or "")
        p.close()
    except Exception:
        pass
    return p


def plain(value) -> str:
    """Text of a REST field: a string or {"rendered"|"raw": ...}, tags stripped, entities decoded."""
    if isinstance(value, dict):
        value = value.get("raw") or value.get("rendered") or ""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def lang_code(lang: str | None) -> str:
    return (lang or "").strip().lower().replace("_", "-").split("-")[0]


# --- readability -----------------------------------------------------------------------------------

def syllables_en(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    if w.endswith("ed") and w[-3] not in "td":
        w = w[:-2]
    elif w.endswith("es") and not re.search(r"(s|x|z|ch|sh|ce|ge)es$", w):
        w = w[:-2]
    elif w.endswith("e") and not w.endswith("le"):
        w = w[:-1]
    if w.startswith("y"):
        w = w[1:]
    return max(1, len(re.findall(r"[aeiouy]+", w)))


_DE_NUCLEUS = re.compile(r"äu|eu|au|ei|ai|ey|ay|ie|aa|ee|oo|[aeiouyäöü]")


def syllables_de(word: str) -> int:
    w = word.lower()
    return max(1, len(_DE_NUCLEUS.findall(w))) if re.search(r"[^\W\d_]", w) else 0


FORMULAS = {
    "en": ("flesch", "Flesch", syllables_en, lambda wps, spw: 206.835 - 1.015 * wps - 84.6 * spw),
    "de": ("flesch_amstad", "Flesch-Amstad", syllables_de, lambda wps, spw: 180 - wps - 58.5 * spw),
}
BANDS = ((90, "very easy"), (80, "easy"), (70, "fairly easy"), (60, "plain"), (50, "fairly hard"),
         (30, "hard"), (float("-inf"), "very hard"))


def band(value: float) -> str:
    return next(label for floor, label in BANDS if value >= floor)


def sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?…])\s+|[.!?…]+$|\n+", text or "")
    return [p for p in (s.strip() for s in parts) if _WORD.search(p)]


def readability(text: str, lang: str) -> dict:
    """{"formula": "flesch"|"flesch_amstad"|None, "score", "label"} plus "words" and "sentences"."""
    code = lang_code(lang)
    if code not in FORMULAS:
        return {"formula": None, "score": None, "label": f"no formula for {code or 'an unknown language'}",
                "words": None, "sentences": None}
    formula, _name, count, calc = FORMULAS[code]
    sents = sentences(text)
    words = [w for s in sents for w in _WORD.findall(s)]
    if not words:
        return {"formula": formula, "score": None, "label": "no text", "words": 0, "sentences": 0}
    syl = sum(count(w) for w in words)
    value = round(calc(len(words) / len(sents), syl / len(words)), 1)
    return {"formula": formula, "score": value, "label": band(value), "words": len(words),
            "sentences": len(sents)}


# --- helpers ---------------------------------------------------------------------------------------

def cut_at_word(text: str, limit: int, ellipsis: bool = False) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    room = limit - (1 if ellipsis else 0)
    head = text[:room + 1]
    head = head[:head.rfind(" ")] if " " in head else text[:room]
    head = head.rstrip(" ,;:–—-")
    return head + ("…" if ellipsis else "")


def shorter_title(title: str, lang: str = "en") -> str:
    for sep in (": ", " – ", " — ", " - ", " | "):
        if sep in title:
            head = title.split(sep)[0].strip()
            if TITLE_MIN <= len(head) <= TITLE_MAX:
                return head
    no_paren = " ".join(re.sub(r"\s*\([^)]*\)", "", title).split())
    if TITLE_MIN <= len(no_paren) <= TITLE_MAX:
        return no_paren
    words = cut_at_word(no_paren, TITLE_MAX).split()
    stop = STOP.get(lang_code(lang), ALL_STOP)
    while len(words) > 1 and words[-1].lower().strip(",.;:") in stop:
        words.pop()
    return " ".join(words).rstrip(",;:–—-")


def propose_description(paragraphs: list) -> str | None:
    text = ""
    for p in paragraphs:
        text = f"{text} {p}".strip()
        if len(text) >= DESC_MIN:
            break
    if not text:
        return None
    if len(text) <= DESC_PROPOSE_MAX:
        return text
    head = text[:DESC_PROPOSE_MAX]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head + " ")]
    good = [e for e in ends if e >= DESC_MIN]
    if good:
        return head[:good[-1]].strip()
    return cut_at_word(text, DESC_PROPOSE_MAX, ellipsis=True)


def slugify(text: str, lang: str = "en") -> str:
    t = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    words = [w for w in re.split(r"[^a-z0-9]+", t) if w]
    stop = STOP.get(lang_code(lang), ALL_STOP) | {"fuer"}
    kept = [w for w in words if w not in stop]
    if len(kept) >= 2:
        words = kept
    out = ""
    for w in words:
        nxt = f"{out}-{w}" if out else w
        if len(nxt) > SLUG_MAX:
            break
        out = nxt
    return out


def _host(netloc: str) -> str:
    h = (netloc or "").lower().split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def _tokens(text: str) -> set:
    out = set()
    for w in _WORD.findall((text or "").lower()):
        if len(w) >= 4 and w not in ALL_STOP:
            out.add(w[:6])
    return out


def _file_alt(src: str) -> str | None:
    name = unquote(urlparse(src).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.[a-z0-9]{2,4}$", "", name, flags=re.I)
    name = re.sub(r"-\d+x\d+$|-scaled$", "", name)
    if not name or re.match(r"^(img|dsc|dscn|pxl|image|photo|screenshot|bildschirmfoto)?[-_ ]?\d", name, re.I):
        return None
    words = re.sub(r"[-_]+", " ", name).strip()
    return words[:1].upper() + words[1:] if len(re.sub(r"[^a-zA-Z]", "", words)) >= 3 else None


# --- the check -------------------------------------------------------------------------------------

def _title_row(title: str, lang: str) -> dict:
    n = len(title)
    if not title:
        return row("title", "Title length", "differs", "No title yet.", WEIGHTS["title"])
    if TITLE_MIN <= n <= TITLE_MAX:
        return row("title", "Title length", "match", f"{n} characters", WEIGHTS["title"])
    if n < TITLE_MIN:
        return row("title", "Title length", "differs",
                   f"{n} characters; aim for {TITLE_MIN} to {TITLE_MAX}. Say what the reader gets.", WEIGHTS["title"])
    short = shorter_title(title, lang)
    return row("title", "Title length", "differs",
               f"{n} characters; aim for {TITLE_MIN} to {TITLE_MAX}. Suggested: \"{short}\" ({len(short)})",
               WEIGHTS["title"],
               fix={"kind": "title", "value": short,
                    "why": f"Search results cut titles near {TITLE_MAX} characters; this keeps the start of yours."})


def _description_row(description, source, paragraphs) -> dict:
    w = WEIGHTS["description"]
    desc = " ".join((description or "").split())
    where = f" ({source})" if source else ""
    proposal = propose_description(paragraphs)
    if desc and DESC_MIN <= len(desc) <= DESC_MAX:
        return row("description", "Meta description", "match", f"{len(desc)} characters{where}", w)
    if desc:
        problem = (f"{len(desc)} characters{where}; aim for {DESC_MIN} to {DESC_MAX}"
                   + (": search results cut it." if len(desc) > DESC_MAX else ": too short to tell what the post is."))
        if len(desc) > DESC_MAX and (not proposal or len(proposal) < DESC_MIN):
            proposal = cut_at_word(desc, DESC_PROPOSE_MAX, ellipsis=True)
    else:
        problem = "None; search engines then pick a random snippet."
    if not proposal:
        return row("description", "Meta description", "differs", problem + " No paragraph to build one from.", w)
    return row("description", "Meta description", "differs",
               f"{problem} Suggested {len(proposal)} characters from the first paragraph.", w,
               fix={"kind": "description", "value": proposal,
                    "why": f"Built from the first paragraph, at most {DESC_PROPOSE_MAX} characters, cut at a word."})


def _headings_row(headings: list) -> dict:
    w = WEIGHTS["headings"]
    problems = []
    h1 = [t for lvl, t in headings if lvl == 1]
    if h1 and THEME_PRINTS_TITLE_H1:
        problems.append(f"{len(h1)} H1 in the content (\"{h1[0][:40]}\"); the theme already prints the title as H1")
    prev = 1
    for lvl, text in headings:
        if lvl > prev + 1:
            problems.append(f"H{lvl} \"{text[:40]}\" follows H{prev} (skips H{prev + 1})")
        prev = lvl
    if problems:
        return row("headings", "Headings", "differs", "; ".join(problems) + ".", w)
    if not headings:
        return row("headings", "Headings", "match", "No subheadings; the title is the one H1.", w)
    levels = sorted({lvl for lvl, _ in headings})
    return row("headings", "Headings", "match",
               f"One H1 (the title), {len(headings)} subheading{'s' if len(headings) > 1 else ''} ({', '.join(f'H{l}' for l in levels)}) in order.", w)


def _links_row(links, site_url, post, text, internal_posts) -> dict:
    w = WEIGHTS["internal_links"]
    site = urlparse(site_url if "//" in site_url else f"https://{site_url}")
    own, internal, external = _host(site.netloc), 0, 0
    for href in links:
        if href.startswith("#") or re.match(r"^(mailto|tel|javascript|data):", href, re.I):
            continue
        u = urlparse(urljoin(site_url.rstrip("/") + "/", href))
        if u.scheme not in ("http", "https"):
            continue
        if _host(u.netloc) == own:
            internal += 1
        else:
            external += 1
    if internal:
        return row("internal_links", "Internal links", "match",
                   f"{internal} internal, {external} external", w)
    mine = _tokens(f"{plain(post.get('title'))} {plain(post.get('title'))} {text}")
    slug = post.get("slug") or ""
    ranked = []
    for cand in internal_posts or ():
        url = cand.get("url") or cand.get("link") or ""
        if (slug and cand.get("slug") == slug) or (url and post.get("url") == url):
            continue
        ctitle = plain(cand.get("title"))
        shared = mine & _tokens(f"{ctitle} {plain(cand.get('excerpt'))}")
        if shared:
            ranked.append((-len(shared), ctitle, {"id": cand.get("id"), "title": ctitle, "url": url,
                                                  "shared": sorted(shared)}))
    picks = [p for _, _, p in sorted(ranked, key=lambda r: (r[0], r[1]))[:2]]
    if not picks:
        return row("internal_links", "Internal links", "differs",
                   f"None yet ({external} external); no related post found to suggest.", w)
    return row("internal_links", "Internal links", "differs",
               f"None yet ({external} external); {len(picks)} related post{'s' if len(picks) > 1 else ''} suggested.",
               w, fix={"kind": "internal_links", "value": picks,
                       "why": "Links to your own posts help readers stay and search engines find older posts; "
                              "these share the most words with this one."})


def _alt_row(images: list) -> dict:
    w = WEIGHTS["alt"]
    seen, uniq = set(), []
    for img in images:
        src = (img.get("src") or "").strip()
        key = src or f"#{len(uniq)}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append({"src": src, "alt": (img.get("alt") or "").strip()})
    if not uniq:
        return row("alt", "Image alt text", "match", "No images.", w)
    missing = [i for i in uniq if not i["alt"]]
    have = len(uniq) - len(missing)
    if not missing:
        return row("alt", "Image alt text", "match", f"{have} of {len(uniq)} images", w)
    names = [unquote(urlparse(i["src"]).path.rsplit("/", 1)[-1]) or "(no src)" for i in missing]
    return row("alt", "Image alt text", "differs",
               f"{have} of {len(uniq)} images; missing on {', '.join(names[:5])}{' …' if len(names) > 5 else ''}.", w,
               fix={"kind": "alt", "value": [{"src": i["src"], "file": n, "suggested": _file_alt(i["src"])}
                                             for i, n in zip(missing, names)],
                    "why": "Alt text describes the image to screen readers and image search."})


def _slug_row(slug: str, title: str, lang: str) -> dict:
    w = WEIGHTS["slug"]
    suggestion = slugify(title, lang)
    if not slug:
        return row("slug", "Slug", "not_proven",
                   f"No slug yet; WordPress makes one from the title when it publishes. Suggested: {suggestion}", w)
    shown = unquote(slug)
    problems = []
    if "%" in slug or re.search(r"[^\x00-\x7f]", shown):
        problems.append("encoded or non-ASCII letters")
    if shown != shown.lower():
        problems.append("capital letters")
    if re.search(r"[^a-z0-9-]", shown.lower()) and not problems:
        problems.append("characters other than a-z, 0-9 and hyphens")
    if "--" in shown or shown.startswith("-") or shown.endswith("-"):
        problems.append("stray hyphens")
    if len(shown) > SLUG_MAX:
        problems.append(f"{len(shown)} characters (at most {SLUG_MAX})")
    words = [x for x in shown.lower().split("-") if x]
    stops = [x for x in words if x in STOP.get(lang_code(lang), ALL_STOP)]
    if len(stops) >= 3 or (len(words) >= 2 and 2 * len(stops) > len(words)):
        problems.append(f"{len(stops)} of {len(words)} words are filler ({', '.join(stops)})")
    if not problems:
        return row("slug", "Slug", "match", shown, w)
    better = slugify(shown.replace("-", " "), lang) or suggestion
    if better == shown or not better:
        better = suggestion
    return row("slug", "Slug", "differs", f"{shown}: {'; '.join(problems)}. Suggested: {better}", w)


def _readability_row(text: str, lang: str) -> dict:
    w = WEIGHTS["readability"]
    r = readability(text, lang)
    if r["formula"] is None:
        return row("readability", "Readability", "not_proven", r["label"], w, readability=r)
    name = FORMULAS[lang_code(lang)][1]
    if (r["words"] or 0) < MIN_WORDS:
        return row("readability", "Readability", "not_proven",
                   f"{r['words']} words of running text; {name} needs at least {MIN_WORDS}.", w, readability=r)
    detail = f"{name} {round(r['score'])}, {r['label']} ({r['words']} words, {r['sentences']} sentences)"
    if r["score"] >= READABILITY_MIN:
        return row("readability", "Readability", "match", detail, w, readability=r)
    return row("readability", "Readability", "differs",
               f"{detail}; aim for {READABILITY_MIN} or more: shorter sentences, shorter words.", w, readability=r)


def check_post(post: dict, *, site_url: str, lang: str = "en", description: str | None,
               description_source: str | None, internal_posts: list[dict] = ()) -> dict:
    """SeoReport-like dict without id: {"scope": "post", "lang", "score", "checks"}."""
    code = lang_code(lang) or "en"
    c = parse_content(post.get("content_html") or "")
    title = plain(post.get("title"))
    text = "\n".join(c.blocks)
    images = list(post.get("images") or []) + c.images
    checks = [
        _title_row(title, code),
        _description_row(description, description_source, c.paragraphs or c.blocks),
        _headings_row(c.headings),
        _links_row(c.links, site_url, post, text, internal_posts),
        _alt_row(images),
        _slug_row(str(post.get("slug") or ""), title, code),
        _readability_row(text, code),
    ]
    return {"scope": "post", "lang": code, "score": score(checks), "checks": checks}
