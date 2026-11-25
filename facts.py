"""SiteFacts: what a site is and what its key may do, cached in a file both processes share.

Sources (PLAN constraint 7, P1, P6, P9, P10):
- public, no key: ``/wp-json/`` (name, namespaces, routes, auth methods, REST time), the home page's
  HTML (WordPress version meta, theme and plugin slugs, robots meta, description, canonical, the
  third-party hosts it names), ``/robots.txt``, ``/wp-sitemap.xml``;
- with the site's key: ``users/me?context=edit`` (user, roles, rights), the application password's
  name, the active theme, plugins (admin only), counts of drafts, pending posts and held comments;
- the connector, when ``wpbruecke/v1`` is in the namespaces: its version and premium update counts;
- ``api.wordpress.org`` for the newest core, plugin and theme versions (no key; cached a day).

The cache file ``facts/<site>.json`` is refreshed at most every ``MAX_AGE_S`` unless forced, so the
fleet page, the doctor and the tick share one read per five minutes per site.
"""
from __future__ import annotations

import html
import json
import re
import time
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .client import WordPressClient, WPError
from .store import Store, now_iso

MAX_AGE_S = 300
WPORG_MAX_AGE_S = 86400
CONNECTOR_NS = "wpbruecke/v1"
RIGHTS = {"draft": "edit_posts", "upload": "upload_files", "publish": "publish_posts",
          "edit_published": "edit_published_posts", "edit_others": "edit_others_posts",
          "manage": "manage_options", "install": "install_plugins", "moderate": "moderate_comments"}


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict = {}
        self.links: list = []
        self.srcs: list = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta.setdefault(name, a.get("content", ""))
        elif tag == "link":
            self.links.append(a)
            if a.get("href"):
                self.srcs.append(a["href"])
        elif tag in ("script", "img", "iframe", "source"):
            for key in ("src", "data-src"):
                if a.get(key):
                    self.srcs.append(a[key])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse_home(text: str, own_host: str) -> dict:
    p = _HeadParser()
    try:
        p.feed(text[:2_000_000])
    except Exception:
        pass
    gen = p.meta.get("generator", "")
    m = re.search(r"WordPress\s+([\d.]+)", gen)
    themes = sorted(set(re.findall(r"/wp-content/themes/([A-Za-z0-9_.-]+)/", text)))
    plugins = sorted(set(re.findall(r"/wp-content/plugins/([A-Za-z0-9_.-]+)/", text)))
    canonical = next((l.get("href") for l in p.links if "canonical" in (l.get("rel") or "").lower().split()), None)
    hosts = set()
    for src in p.srcs:
        h = urlparse(src if "//" in src else "").hostname
        if h and h != own_host:
            hosts.add(h.lower())
    return {
        "wp_version_meta": m.group(1) if m else None,
        "themes_in_html": themes,
        "plugins_in_html": plugins,
        "robots_meta": p.meta.get("robots"),
        "noindex": "noindex" in (p.meta.get("robots") or "").lower(),
        "meta_description": p.meta.get("description") or None,
        "canonical": canonical,
        "title": html.unescape(p.title.strip())[:200],
        "third_party_hosts": sorted(hosts)[:30],
    }


def rights_from(caps: dict) -> dict:
    caps = caps or {}
    return {k: bool(caps.get(v)) for k, v in RIGHTS.items()}


def can_change_live(rights: dict) -> bool:
    return bool(rights.get("publish") or rights.get("edit_published"))


def _version_tuple(v: Any) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def newer(installed: Any, latest: Any) -> bool:
    return bool(installed and latest and _version_tuple(latest) > _version_tuple(installed))


class WporgVersions:
    """Newest versions from api.wordpress.org, cached a day in facts/_wporg.json."""

    def __init__(self, store: Store, fetch: Optional[Callable[[str], Any]] = None, clock=time.time) -> None:
        self.store, self.clock = store, clock
        self._fetch = fetch or self._default_fetch

    @staticmethod
    def _default_fetch(url: str) -> Any:
        client = WordPressClient("https://api.wordpress.org", timeout=15)
        return client.request("GET", url, auth=False).json()

    def _cached(self, key: str, url: str, pick: Callable[[Any], Any]) -> Any:
        path = self.store.path("facts", "_wporg.json")
        with self.store.lock("facts"):
            cache = self.store.read_json(path, {}) or {}
        hit = cache.get(key)
        if hit and self.clock() - hit.get("t", 0) < WPORG_MAX_AGE_S:
            return hit.get("v")
        try:
            value = pick(self._fetch(url))
        except Exception:
            return hit.get("v") if hit else None
        with self.store.lock("facts"):
            cache = self.store.read_json(path, {}) or {}
            cache[key] = {"t": self.clock(), "v": value}
            self.store.write_json(path, cache)
        return value

    def core(self) -> Optional[str]:
        return self._cached("core", "https://api.wordpress.org/core/version-check/1.7/",
                            lambda d: (d.get("offers") or [{}])[0].get("current"))

    def plugin(self, slug: str) -> Optional[str]:
        if not re.match(r"^[a-z0-9-]{1,80}$", slug or ""):
            return None
        return self._cached(f"plugin:{slug}", "https://api.wordpress.org/plugins/info/1.2/?action=plugin_information"
                            f"&request[slug]={slug}&request[fields][sections]=0",
                            lambda d: d.get("version") if isinstance(d, dict) else None)

    def theme(self, slug: str) -> Optional[str]:
        if not re.match(r"^[a-z0-9-]{1,80}$", slug or ""):
            return None
        return self._cached(f"theme:{slug}", "https://api.wordpress.org/themes/info/1.2/?action=theme_information"
                            f"&request[slug]={slug}", lambda d: d.get("version") if isinstance(d, dict) else None)


def _safe(fn: Callable[[], Any], errors: list, label: str) -> Any:
    try:
        return fn()
    except WPError as e:
        errors.append({"read": label, **e.to_dict()})
        return None


def gather(site: dict, client: WordPressClient, wporg: Optional[WporgVersions] = None) -> dict:
    """One fresh SiteFacts dict for a site. Never raises for a read that fails; it is listed in errors."""
    errors: list = []
    facts: dict = {"site": site["id"], "url": site["url"], "fetched_at": now_iso(), "reachable": False,
                   "rest_ms": None, "keyed": client.authenticated, "errors": errors}
    root = None
    try:
        resp = client.request("GET", "/wp-json/", auth=False)
        root = resp.json()
        facts["reachable"] = True
        facts["rest_ms"] = resp.ms
    except WPError as e:
        errors.append({"read": "/wp-json/", **e.to_dict()})
    if isinstance(root, dict):
        facts["name"] = root.get("name")
        facts["description"] = root.get("description")
        facts["namespaces"] = root.get("namespaces") or []
        facts["routes"] = len(root.get("routes") or {})
        facts["authentication"] = sorted((root.get("authentication") or {}).keys()) if isinstance(root.get("authentication"), dict) else []
        facts["connector_ns"] = CONNECTOR_NS in facts["namespaces"]
    home = _safe(lambda: client.raw("GET", "/", auth=False), errors, "home")
    seo: dict = {}
    if home is not None:
        facts["home_status"] = home.status
        facts["home_ms"] = home.ms
        if home.status < 400:
            seo.update(parse_home(home.text(), client.host))
            facts["reachable"] = True
    for path, key in (("/robots.txt", "robots_txt"), ("/wp-sitemap.xml", "core_sitemap")):
        r = _safe(lambda p=path: client.raw("GET", p, auth=False), errors, path)
        seo[key] = r.status if r is not None else None
    facts["seo_public"] = seo
    facts["wp_version"] = seo.get("wp_version_meta")
    facts["theme"] = (seo.get("themes_in_html") or [None])[0]

    if client.authenticated and facts.get("reachable"):
        me = _safe(lambda: client.get("/wp-json/wp/v2/users/me", context="edit"), errors, "users/me")
        if isinstance(me, dict):
            facts["user"] = {"id": me.get("id"), "username": me.get("username") or me.get("slug"),
                             "name": me.get("name"), "roles": me.get("roles") or []}
            facts["rights"] = rights_from(me.get("capabilities") or {})
            intro = _safe(lambda: client.get(f"/wp-json/wp/v2/users/{me.get('id')}/application-passwords/introspect"),
                          [], "introspect")
            if isinstance(intro, dict):
                facts["key_name"] = intro.get("name")
        themes = _safe(lambda: client.get("/wp-json/wp/v2/themes", status="active"), [], "themes")
        if isinstance(themes, list) and themes:
            t = themes[0]
            facts["theme"] = t.get("stylesheet") or facts["theme"]
            facts["theme_name"] = (t.get("name") or {}).get("rendered") if isinstance(t.get("name"), dict) else t.get("name")
            facts["theme_version"] = t.get("version")
        if (facts.get("rights") or {}).get("install") or (facts.get("rights") or {}).get("manage"):
            plugins = _safe(lambda: client.get("/wp-json/wp/v2/plugins"), [], "plugins")
            if isinstance(plugins, list):
                facts["plugins"] = [{"plugin": p.get("plugin"), "status": p.get("status"), "version": p.get("version"),
                                     "name": p.get("name")} for p in plugins]
        counts = {}
        for label, path, params in (("drafts", "/wp-json/wp/v2/posts", {"status": "draft"}),
                                    ("pending", "/wp-json/wp/v2/posts", {"status": "pending"}),
                                    ("draft_pages", "/wp-json/wp/v2/pages", {"status": "draft"}),
                                    ("comments_hold", "/wp-json/wp/v2/comments", {"status": "hold"})):
            got = _safe(lambda p=path, q=params: client.get_with_total(p, context="edit", per_page=1, **q), [], label)
            counts[label] = got[1] if got else None
        published = _safe(lambda: client.get_with_total("/wp-json/wp/v2/posts", per_page=1, _fields="id,date,title"), [], "published")
        if published:
            counts["published"] = published[1]
            if published[0]:
                facts["last_post"] = {"id": published[0][0].get("id"), "date": published[0][0].get("date")}
        facts["counts"] = counts
    if facts.get("connector_ns") and client.authenticated:  # /info needs a login; without one it answers 401
        info = _safe(lambda: client.get("/wp-json/wpbruecke/v1/info"), errors, "connector")
        if isinstance(info, dict):
            facts["connector"] = {k: info.get(k) for k in ("version", "role", "preview", "seo_field", "seo_plugin",
                                                          "updates", "review_screen") if k in info}
    if wporg is not None and facts.get("reachable"):
        facts["updates"] = updates(facts, wporg)
    return facts


def updates(facts: dict, wporg: WporgVersions) -> dict:
    out: dict = {"core": None, "plugins": [], "themes": [], "source": "api.wordpress.org"}
    latest_core = wporg.core()
    if newer(facts.get("wp_version"), latest_core):
        out["core"] = {"installed": facts.get("wp_version"), "latest": latest_core}
    for p in facts.get("plugins") or []:
        slug = str(p.get("plugin") or "").split("/")[0]
        latest = wporg.plugin(slug)
        if newer(p.get("version"), latest):
            out["plugins"].append({"slug": slug, "installed": p.get("version"), "latest": latest})
    if facts.get("theme") and facts.get("theme_version"):
        latest = wporg.theme(facts["theme"])
        if newer(facts.get("theme_version"), latest):
            out["themes"].append({"slug": facts["theme"], "installed": facts["theme_version"], "latest": latest})
    conn = (facts.get("connector") or {}).get("updates")
    if isinstance(conn, dict):
        out["connector"] = conn  # premium plugins report through the connector (WordPress' own transients)
    out["count"] = (1 if out["core"] else 0) + len(out["plugins"]) + len(out["themes"]) + int(
        (conn or {}).get("count") or 0 if isinstance(conn, dict) else 0)
    return out


class FactsCache:
    def __init__(self, store: Store, clock=time.time) -> None:
        self.store, self.clock = store, clock

    def read(self, site_id: str) -> Optional[dict]:
        return self.store.read_json(self.store.path("facts", f"{site_id}.json"), None)

    def age(self, facts: Optional[dict]) -> Optional[float]:
        if not facts:
            return None
        return self.clock() - float(facts.get("_t", 0))

    def get(self, site: dict, client_factory: Callable[[dict], WordPressClient], refresh: bool = False,
            wporg: Optional[WporgVersions] = None) -> dict:
        cached = self.read(site["id"])
        client = client_factory(site)
        if not refresh and cached and (self.age(cached) or 1e9) < MAX_AGE_S and cached.get("keyed") == client.authenticated:
            cached["cache"] = "hit"
            return cached
        fresh = gather(site, client, wporg)
        fresh["_t"] = self.clock()
        with self.store.lock("facts"):
            self.store.write_json(self.store.path("facts", f"{site['id']}.json"), fresh)
        fresh["cache"] = "fresh"
        return fresh


def summary_line(facts: dict) -> str:
    if not facts.get("reachable"):
        return "unreachable"
    bits = [f"WordPress {facts.get('wp_version') or 'version hidden'}", f"theme {facts.get('theme') or '?'}",
            f"REST {facts.get('rest_ms')} ms"]
    return ", ".join(bits)


def dumps(facts: dict) -> str:
    return json.dumps(facts, ensure_ascii=False)
