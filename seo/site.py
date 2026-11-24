"""SEO site check (F10): indexing, sitemap, robots.txt, home description and canonical, mobile layout,
console errors, speed, broken links.

Inputs: SiteFacts (``facts.gather``; the ``seo_public`` fields come from the public home page,
``/robots.txt`` and ``/wp-sitemap.xml``), optionally a fresh home page HTML, a ProofRun of the home
page, and a PageSpeed result (``pagespeed()``, only with the person's own key: keyless PageSpeed
answers 429, PLAN constraint 8 / D12).

Weights (sum 100): indexing 25, sitemap 10, robots_txt 5, home_description 10, canonical 5,
mobile_layout 10, console 5, speed 15, broken_links 15. Score rule: see ``seo/__init__.py``.

A row may carry ``action`` instead of ``fix``: a person's click outside the review desk. "Discourage
search engines" (``blog_public``) is not in REST (constraint 6), so the indexing row only links to
Settings → Reading and never becomes a desk item.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse

from . import row, score
from .fields import SEO_PLUGINS, description_target, has_connector, seo_plugin

WEIGHTS = {"indexing": 25, "sitemap": 10, "robots_txt": 5, "home_description": 10, "canonical": 5,
           "mobile_layout": 10, "console": 5, "speed": 15, "broken_links": 15}
PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
KEYLESS = "PageSpeed needs a key (keyless answers 429)"
NO_PROOF = "no proof run yet"
LCP_GOOD_MS, CLS_GOOD = 2500, 0.1   # Google's "good" thresholds for Core Web Vitals


class PageSpeedError(RuntimeError):
    """A PageSpeed call failed; the message never contains the key."""


def pagespeed(url: str, key: str, fetch) -> dict:
    """Google PageSpeed (mobile, performance) through ``fetch(url) -> parsed JSON``.

    -> {"performance": 0-100, "lcp_ms", "cls", "source": "google", "strategy": "mobile", "url"}.
    The key goes only into the request URL; it is never logged, returned or put into an error.
    """
    if not key:
        raise PageSpeedError(KEYLESS)
    query = urlencode({"url": url, "strategy": "mobile", "category": "performance", "key": key})

    def scrub(text: str) -> str:
        return str(text).replace(key, "***")

    try:
        data = fetch(f"{PAGESPEED_API}?{query}")
    except Exception as e:  # noqa: BLE001 - any transport error, re-raised without the key
        raise PageSpeedError(scrub(f"PageSpeed request failed: {type(e).__name__}: {e}")) from None
    if not isinstance(data, dict):
        raise PageSpeedError("PageSpeed answered no JSON object")
    if isinstance(data.get("error"), dict):
        err = data["error"]
        raise PageSpeedError(scrub(f"PageSpeed answered {err.get('code')}: {err.get('message') or ''}".strip()))
    lh = data.get("lighthouseResult") or {}
    perf = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    audits = lh.get("audits") or {}
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    if perf is None and lcp is None and cls is None:
        raise PageSpeedError("PageSpeed answer has no lighthouseResult")
    return {"performance": round(float(perf) * 100) if perf is not None else None,
            "lcp_ms": round(float(lcp)) if lcp is not None else None,
            "cls": round(float(cls), 3) if cls is not None else None,
            "source": "google", "strategy": "mobile", "url": url}


# --- rows ------------------------------------------------------------------------------------------

def _plugin_name(facts: dict) -> str | None:
    p = seo_plugin(facts)
    return f"{SEO_PLUGINS[p]['name']} ({SEO_PLUGINS[p]['namespace']})" if p else None


def _indexing_row(seo: dict, site_url: str, plugin: str | None, read: bool) -> dict:
    w = WEIGHTS["indexing"]
    if not read:
        return row("indexing", "Indexing", "not_proven", "The home page was not read (site unreachable).", w)
    meta = seo.get("robots_meta")
    if seo.get("noindex"):
        cause = ("Settings → Reading → \"Discourage search engines\" is ticked"
                 + (f", or {plugin} sets noindex on the home page" if plugin else ""))
        return row("indexing", "Indexing is off", "differs",
                   f"The home page sends \"{meta}\". {cause}. Fine for a test site, fatal for a live one. "
                   "This is a site setting: your click in wp-admin.", w,
                   action={"kind": "wp_admin", "label": "Open setting",
                           "url": f"{site_url}/wp-admin/options-reading.php",
                           "why": "WordPress does not offer this setting over REST; Hermes never changes it."})
    return row("indexing", "Indexing", "match",
               f"Search engines may index the home page (robots meta: {meta or 'none'}).", w)


def _sitemap_row(seo: dict, plugin: str | None) -> dict:
    w = WEIGHTS["sitemap"]
    code = seo.get("core_sitemap")
    if code is None:
        return row("sitemap", "Sitemap", "not_proven", "/wp-sitemap.xml was not read.", w)
    if code == 200:
        return row("sitemap", "Sitemap", "match", "/wp-sitemap.xml answers 200.", w)
    if 300 <= code < 400:
        return row("sitemap", "Sitemap", "match",
                   f"/wp-sitemap.xml redirects ({code}){f', likely to {plugin}' if plugin else ''}.", w)
    if seo.get("noindex"):
        return row("sitemap", "No sitemap", "differs",
                   f"/wp-sitemap.xml answers {code} because indexing is off. It appears on its own once indexing is on.", w)
    extra = (f" {plugin} may serve its own sitemap; check it there." if plugin
             else " A plugin may have switched the core sitemap off.")
    return row("sitemap", "No sitemap", "differs", f"/wp-sitemap.xml answers {code} while indexing is on.{extra}", w)


def _robots_row(seo: dict) -> dict:
    w = WEIGHTS["robots_txt"]
    code = seo.get("robots_txt")
    if code is None:
        return row("robots_txt", "robots.txt", "not_proven", "/robots.txt was not read.", w)
    if code == 200:
        return row("robots_txt", "robots.txt", "match", "Present (200).", w)
    return row("robots_txt", "robots.txt", "differs", f"/robots.txt answers {code}.", w)


def _install_action(facts: dict) -> dict | None:
    if has_connector(facts):
        return None
    return {"kind": "install_connector", "label": "Connector",
            "why": "The WPBrücke connector adds a description field and prints it, with canonical, in the page head; "
                   "an SEO plugin (Yoast SEO, Rank Math, SEOPress) does the same."}


def _description_row(facts: dict, seo: dict, plugin: str | None, read: bool) -> dict:
    w = WEIGHTS["home_description"]
    if not read:
        return row("home_description", "Home description", "not_proven", "The home page was not read.", w)
    desc = " ".join((seo.get("meta_description") or "").split())
    if desc:
        return row("home_description", "Home description", "match", f"{len(desc)} characters", w)
    target = description_target(facts)
    if plugin:
        how = f"{plugin} is active; set the home page description in its settings."
    elif target["where"]:
        how = "The WPBrücke connector is installed; set the home page description in its field."
    else:
        how = "No SEO plugin on this site. The WPBrücke connector adds a description field and prints it."
    return row("home_description", "No meta description on the home page", "differs",
               f"The home page has no meta description. {how}", w, action=_install_action(facts))


def _canonical_row(facts: dict, seo: dict, plugin: str | None, read: bool) -> dict:
    w = WEIGHTS["canonical"]
    if not read:
        return row("canonical", "Canonical", "not_proven", "The home page was not read.", w)
    if seo.get("canonical"):
        return row("canonical", "Canonical", "match", f"rel=canonical {seo['canonical']}", w)
    how = (f"{plugin} is active; its canonical setting is off for the home page." if plugin else
           "WordPress core prints canonical only on single posts and pages; the WPBrücke connector or an SEO "
           "plugin adds it to the home page.")
    return row("canonical", "No canonical on the home page", "differs", how, w, action=_install_action(facts))


def _proof_checks(proof_run: dict, *needles: str) -> list:
    return [c for c in proof_run.get("checks") or [] if any(n in str(c.get("check", "")).lower() for n in needles)]


def _mobile_row(proof_run: dict | None) -> dict:
    w = WEIGHTS["mobile_layout"]
    if not proof_run:
        return row("mobile_layout", "Mobile layout", "not_proven", NO_PROOF, w)
    rows = _proof_checks(proof_run, "overflow")
    if not rows:
        return row("mobile_layout", "Mobile layout", "not_proven", "the proof run has no overflow check", w)
    widths = sorted({c.get("width") for c in rows if c.get("width")} | set(proof_run.get("widths") or []))
    bad = [c for c in rows if c.get("verdict") == "differs"]
    unknown = [c for c in rows if c.get("verdict") == "not_proven"]
    if bad:
        return row("mobile_layout", "Mobile layout", "differs",
                   "; ".join(c.get("detail") or f"overflow at {c.get('width')} px" for c in bad), w,
                   proof_id=proof_run.get("id"))
    if unknown and len(unknown) == len(rows):
        return row("mobile_layout", "Mobile layout", "not_proven",
                   "; ".join(c.get("detail") or "overflow not measured" for c in unknown), w)
    at = ", ".join(str(x) for x in widths)
    return row("mobile_layout", "Mobile layout", "match",
               f"No horizontal overflow{f' at {at} px' if at else ''}.", w, proof_id=proof_run.get("id"))


def _console_row(proof_run: dict | None) -> dict:
    w = WEIGHTS["console"]
    if not proof_run:
        return row("console", "Console errors", "not_proven", NO_PROOF, w)
    errors = proof_run.get("console_errors")
    rows = _proof_checks(proof_run, "console")
    if errors is None and not rows:
        return row("console", "Console errors", "not_proven", "the proof run recorded no console", w)
    errors = list(errors or [])
    if errors or any(c.get("verdict") == "differs" for c in rows):
        first = str(errors[0])[:120] if errors else next(c.get("detail") for c in rows if c.get("verdict") == "differs")
        return row("console", "Console errors", "differs", f"{len(errors) or 'some'} console error(s); first: {first}", w)
    return row("console", "Console errors", "match", "0 console errors.", w)


def _local_perf(proof_run: dict | None) -> dict | None:
    perf = (proof_run or {}).get("perf") or {}
    vals = [v for v in perf.values() if isinstance(v, dict)]
    if not vals:
        return None

    def span(key):
        xs = [v[key] for v in vals if isinstance(v.get(key), (int, float))]
        return (min(xs), max(xs)) if xs else None

    return {"load_ms": span("load_ms"), "lcp_ms": span("lcp_ms"), "cls": span("cls"),
            "widths": sorted(int(k) for k in perf.keys() if str(k).isdigit())}


def _secs(span) -> str:
    lo, hi = span
    return f"{lo / 1000:.1f} s" if round(lo / 100) == round(hi / 100) else f"{lo / 1000:.1f} to {hi / 1000:.1f} s"


def _speed_row(pagespeed: dict | None, proof_run: dict | None) -> dict:
    w = WEIGHTS["speed"]
    if pagespeed and (pagespeed.get("lcp_ms") is not None or pagespeed.get("performance") is not None):
        lcp, cls, perf = pagespeed.get("lcp_ms"), pagespeed.get("cls"), pagespeed.get("performance")
        bits = [f"performance {perf}" if perf is not None else None,
                f"LCP {lcp / 1000:.1f} s" if lcp is not None else None,
                f"CLS {cls}" if cls is not None else None]
        detail = "Google PageSpeed, mobile: " + ", ".join(b for b in bits if b)
        if lcp is None or cls is None:
            return row("speed", "Speed", "not_proven", detail + "; LCP or CLS missing in the answer.", w, source="google",
                       measure=pagespeed)
        good = lcp <= LCP_GOOD_MS and cls <= CLS_GOOD
        verdict = "match" if good else "differs"
        tail = "." if good else f"; Google's good: LCP ≤ {LCP_GOOD_MS / 1000:.1f} s, CLS ≤ {CLS_GOOD}."
        return row("speed", "Speed", verdict, detail + tail, w, source="google", measure=pagespeed)
    local = _local_perf(proof_run)
    if not local:
        return row("speed", "Speed", "not_proven", f"{KEYLESS}; {NO_PROOF} for a local measure.", w)
    bits = []
    if local["load_ms"]:
        bits.append(f"page loaded in {_secs(local['load_ms'])}")
    if local["lcp_ms"]:
        bits.append(f"LCP {_secs(local['lcp_ms'])}")
    if local["cls"]:
        lo, hi = local["cls"]
        bits.append(f"CLS {lo:g}" if lo == hi else f"CLS {lo:g} to {hi:g}")
    return row("speed", "Speed", "not_proven",
               f"{KEYLESS}. Local measure instead (local, not Google): {', '.join(bits) or 'no numbers'} in Hermes' "
               "browser. Add a PageSpeed key in Settings for Google's numbers.", w,
               source="local, not Google", measure=local)


def _links_row(proof_run: dict | None) -> dict:
    w = WEIGHTS["broken_links"]
    if not proof_run:
        return row("broken_links", "Broken links", "not_proven", NO_PROOF, w)
    links = proof_run.get("links")
    rows = _proof_checks(proof_run, "link")
    if links is None and not rows:
        return row("broken_links", "Broken links", "not_proven", "the proof run checked no links", w)
    checked = [l for l in links or [] if l.get("status") is not None]
    broken = [l for l in checked if not l.get("status") or int(l["status"]) >= 400]
    flagged = [c for c in rows if c.get("verdict") == "differs"]
    if broken:
        first = ", ".join(f"{l.get('href')} ({l.get('status')})" for l in broken[:3])
        return row("broken_links", "Broken links", "differs", f"{len(broken)} of {len(checked)} links broken: {first}", w)
    if flagged:
        return row("broken_links", "Broken links", "differs", "; ".join(c.get("detail") or "broken link" for c in flagged), w)
    if not checked and not any(c.get("verdict") == "match" for c in rows):
        return row("broken_links", "Broken links", "not_proven",
                   "no same-host link was checked (other hosts are not fetched)", w)
    n = len(checked)
    return row("broken_links", "Broken links", "match",
               f"{n} same-host links answer below 400." if n else "; ".join(c.get("detail") or "ok" for c in rows), w)


def check_site(facts: dict, *, home_html: str | None, proof_run: dict | None,
               pagespeed: dict | None) -> dict:
    """SeoReport-like dict without id: {"scope": "site", "site", "url", "score", "checks"}."""
    site_url = str(facts.get("url") or "").rstrip("/")
    seo = dict(facts.get("seo_public") or {})
    if home_html is not None:
        from ..facts import parse_home
        seo.update(parse_home(home_html, (urlparse(site_url).hostname or "").lower()))
    read = home_html is not None or bool(seo.get("robots_meta") is not None or "noindex" in seo)
    plugin = _plugin_name(facts)
    checks = [
        _indexing_row(seo, site_url, plugin, read),
        _sitemap_row(seo, plugin),
        _robots_row(seo),
        _description_row(facts, seo, plugin, read),
        _canonical_row(facts, seo, plugin, read),
        _mobile_row(proof_run),
        _console_row(proof_run),
        _speed_row(pagespeed, proof_run),
        _links_row(proof_run),
    ]
    return {"scope": "site", "site": facts.get("site"), "url": site_url or None, "score": score(checks),
            "checks": checks}
