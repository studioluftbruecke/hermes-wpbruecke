"""Shared fakes for WPBrücke's tests (imported by name: conftest.py only puts this folder on sys.path)."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = "wpbruecke_under_test"


def php_command() -> list | None:
    """A PHP 8 command: WPB_PHP, php on PATH, or the official alpine image's binary the lint script uses."""
    if os.environ.get("WPB_PHP"):
        return [os.environ["WPB_PHP"]]
    if shutil.which("php"):
        return ["php"]
    base = pathlib.Path(os.environ.get("TMPDIR") or "/tmp") / "fakedocker" / "bases" / "php_8.3-cli-alpine"
    loader = sorted((base / "lib").glob("ld-musl-*.so.1")) if base.is_dir() else []
    if loader and (base / "usr" / "local" / "bin" / "php").exists():
        return [str(loader[0]), "--library-path", f"{base}/lib:{base}/usr/lib:{base}/usr/local/lib",
                str(base / "usr" / "local" / "bin" / "php")]
    return None


def load_plugin():
    """The plugin as a package (relative imports work), loaded once per test session."""
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


def mod(name: str):
    load_plugin()
    return importlib.import_module(f"{PKG}.{name}")


HOME_HTML = """<!doctype html><html><head><title>Wordpress Test Page</title>
<meta name='robots' content='noindex, nofollow' />
<meta name="generator" content="WordPress 7.1.4" />
<link rel='stylesheet' href='https://test.example/wp-content/themes/twentytwentyfive/style.css' />
<script src="https://stats.wp.com/e.js"></script>
</head><body><h1>Hello</h1><img src="https://i0.wp.com/x.png"></body></html>"""

ADMIN_CAPS = {"edit_posts": True, "upload_files": True, "publish_posts": True, "edit_published_posts": True,
              "edit_others_posts": True, "manage_options": True, "install_plugins": True, "moderate_comments": True}
CONTRIB_CAPS = {"edit_posts": True}
# True capabilities per role as users/me?context=edit lists them (P20), role-name caps included.
ROLE_CAPS = {
    "subscriber": {"read": True, "level_0": True, "subscriber": True},
    "wpbruecke_reader": {"read": True, "wpbruecke_reader": True},
    "contributor": {"read": True, "edit_posts": True, "delete_posts": True, "level_1": True, "contributor": True},
    "wpbruecke_drafter": {"read": True, "edit_posts": True, "delete_posts": True, "upload_files": True,
                          "wpbruecke_drafter": True},
    "author": {"read": True, "edit_posts": True, "delete_posts": True, "upload_files": True, "publish_posts": True,
               "edit_published_posts": True, "delete_published_posts": True, "author": True},
    "editor": {"read": True, "edit_posts": True, "delete_posts": True, "upload_files": True, "publish_posts": True,
               "publish_pages": True, "edit_pages": True, "edit_published_posts": True, "edit_others_posts": True,
               "edit_others_pages": True, "moderate_comments": True, "manage_categories": True, "editor": True},
    "administrator": {**ADMIN_CAPS, "edit_users": True, "edit_theme_options": True, "administrator": True},
}


class FakeWP:
    """A tiny WordPress: routes keyed by (METHOD, path); records every request."""

    def __init__(self, host: str = "test.example", user: str = "admin", caps: dict | None = None,
                 password: str = "pw pw pw", namespaces: list | None = None, roles: list | None = None) -> None:
        self.host, self.user, self.password, self.roles = host, user, password, roles
        self.caps = ADMIN_CAPS if caps is None else caps
        self.namespaces = namespaces or ["oembed/1.0", "wp/v2", "wp-site-health/v1", "wp-abilities/v1"]
        self.requests: list = []
        self.routes: dict = {}
        self.down = False
        self.posts: dict = {}
        self.next_id = 100

    def authed(self, headers: dict) -> bool:
        import base64
        want = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        return headers.get("Authorization") == want

    def __call__(self, method, url, headers, body, timeout):
        parsed = urlparse(url)
        self.requests.append({"method": method, "path": parsed.path, "query": parse_qs(parsed.query),
                              "auth": "Authorization" in headers, "body": body})
        if self.down:
            raise OSError("connection refused")
        if parsed.hostname != self.host and parsed.hostname != "api.wordpress.org":
            raise AssertionError(f"unexpected host {parsed.hostname}")
        key = (method, parsed.path)
        if key in self.routes:
            r = self.routes[key]
            return r(method, parsed, headers, body) if callable(r) else r
        return self.default(method, parsed, headers, body)

    @staticmethod
    def j(status: int, data, headers: dict | None = None):
        return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(data).encode()

    def add_post(self, ptype="post", status="publish", title="Hello world!", content="<p>Hi</p>", **extra) -> dict:
        self.next_id += 1
        post = {"id": self.next_id, "type": ptype, "status": status, "title": {"raw": title, "rendered": title},
                "content": {"raw": content, "rendered": content}, "excerpt": {"raw": "", "rendered": ""},
                "slug": title.lower().replace(" ", "-").strip("!"), "link": f"https://{self.host}/?p={self.next_id}",
                "modified_gmt": "2026-11-01T10:00:00", "author": 1, "meta": {}, "categories": [], "tags": [],
                "featured_media": 0, **extra}
        self.posts[post["id"]] = post
        return post

    def post_route(self, method, ptype, pid, body):
        data = json.loads(body.decode()) if body else {}
        if pid is None:
            if data.get("status") in ("publish", "future", "private") and not self.caps.get("publish_posts"):
                return self.j(403, {"code": "rest_cannot_publish", "message": "Sorry, you are not allowed to publish."})
            self.next_id += 1
            post = {"id": self.next_id, "type": ptype, "status": data.get("status", "draft"),
                    "title": {"raw": data.get("title", ""), "rendered": data.get("title", "")},
                    "content": {"raw": data.get("content", ""), "rendered": data.get("content", "")},
                    "excerpt": {"raw": data.get("excerpt", ""), "rendered": ""}, "slug": data.get("slug", ""),
                    "link": f"https://{self.host}/?p={self.next_id}", "modified_gmt": "2026-11-09T10:00:00",
                    "author": 1, "meta": data.get("meta", {}), "categories": data.get("categories", []),
                    "tags": data.get("tags", []), "featured_media": data.get("featured_media", 0)}
            self.posts[post["id"]] = post
            return self.j(201, post)
        post = self.posts.get(pid)
        if post is None or post["type"] != ptype:
            return self.j(404, {"code": "rest_post_invalid_id", "message": "Invalid post ID."})
        if method == "GET":
            return self.j(200, post)
        if method == "DELETE":
            self.posts.pop(pid, None)
            return self.j(200, {"deleted": True, "previous": post})
        if data.get("status") in ("publish", "future") and not self.caps.get("publish_posts"):
            return self.j(403, {"code": "rest_cannot_publish", "message": "Sorry, you are not allowed to publish."})
        if data.get("status") == "future" and data.get("date"):
            post["date"] = data["date"]
        for k, v in data.items():
            post[k] = {"raw": v, "rendered": v} if k in ("title", "content", "excerpt") else v
        post["modified_gmt"] = "2026-11-09T11:00:00"
        return self.j(200, post)

    def default(self, method, parsed, headers, body):
        p = parsed.path
        q = parse_qs(parsed.query)
        authed = self.authed(headers)
        m = re.match(r"^/wp-json/wp/v2/(posts|pages)(?:/(\d+))?$", p)
        if m and authed and (method == "POST" or m.group(2)):
            return self.post_route(method, m.group(1)[:-1], int(m.group(2)) if m.group(2) else None, body)
        mm = re.match(r"^/wp-json/wp/v2/media(?:/(\d+))?$", p)
        if mm and method == "POST" and authed:
            if not self.caps.get("upload_files"):
                return self.j(403, {"code": "rest_cannot_create", "message": "Sorry, you are not allowed to upload media."})
            if mm.group(1):
                return self.j(200, {"id": int(mm.group(1))})
            self.next_id += 1
            return self.j(201, {"id": self.next_id, "source_url": f"https://{self.host}/wp-content/uploads/x.png"})
        if p in ("/wp-json/wp/v2/categories", "/wp-json/wp/v2/tags") and method == "GET":
            names = {"categories": [{"id": 3, "name": "News"}], "tags": [{"id": 7, "name": "selfhost"}]}
            return self.j(200, names[p.rsplit("/", 1)[1]])
        if p == "/wp-json/":
            return self.j(200, {"name": "Wordpress Test Page", "description": "", "namespaces": self.namespaces,
                                "authentication": {"application-passwords": {}}, "routes": {"/": {}, "/wp/v2": {}}})
        if p == "/":
            return 200, {"Content-Type": "text/html"}, HOME_HTML.replace("test.example", self.host).encode()
        if p == "/robots.txt":
            return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\n"
        if p == "/wp-sitemap.xml":
            return 404, {}, b"not found"
        if p.startswith("/wp-json/") and "Authorization" in headers and not authed:
            return self.j(401, {"code": "incorrect_password", "message": "The provided password is an invalid application password."})
        if p == "/wp-json/wp/v2/users/me":
            if not authed:
                return self.j(401, {"code": "rest_not_logged_in", "message": "You are not currently logged in."})
            roles = self.roles if self.roles is not None else \
                (["administrator"] if self.caps.get("manage_options") else ["contributor"])
            return self.j(200, {"id": 1, "username": self.user, "name": self.user, "roles": roles, "capabilities": self.caps})
        if p.endswith("/application-passwords/introspect"):
            return self.j(200, {"name": "hermes-test", "uuid": "u"})
        if re.match(r"^/wp-json/wp/v2/users/me/application-passwords/[\w-]+$", p) and method == "DELETE":
            self.password = None  # the key is gone: every later call with it answers 401
            return self.j(200, {"deleted": True, "previous": {"uuid": p.rsplit("/", 1)[1]}})
        if p == "/wp-json/wp/v2/themes":
            if not self.caps.get("manage_options"):
                return self.j(403, {"code": "rest_cannot_view_themes", "message": "no"})
            return self.j(200, [{"stylesheet": "twentytwentyfive", "name": {"rendered": "Twenty Twenty-Five"}, "version": "1.2"}])
        if p == "/wp-json/wp/v2/plugins":
            return self.j(200, [{"plugin": "akismet/akismet", "status": "inactive", "version": "5.3", "name": "Akismet"}])
        if p in ("/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages", "/wp-json/wp/v2/comments") and method == "GET":
            status = (q.get("status") or ["publish"])[0]
            items = [x for x in self.posts.values() if x.get("status") == status and p.endswith(x.get("type", "post") + "s")]
            if p.endswith("posts") and status == "publish" and not items:
                items = [{"id": 1, "date": "2026-11-01T10:00:00", "title": {"rendered": "Hello world!"}}]
            return self.j(200, items[:1], {"X-WP-Total": str(len(items)), "X-WP-TotalPages": "1"})
        if parsed.hostname == "api.wordpress.org" or p.startswith("/core/") or p.startswith("/plugins/info"):
            if "core" in p:
                return self.j(200, {"offers": [{"current": "7.1.4"}]})
            if "plugins" in p:
                return self.j(200, {"version": "5.4"})
            return self.j(200, {"version": "1.2"})
        return self.j(404, {"code": "rest_no_route", "message": "No route was found matching the URL and request method."})


class Settings(dict):
    def __call__(self, key, default=None):
        return self.get(key, default)


def secrets(**values):
    def get(name):
        return values.get(name)
    return get


class Multi:
    """One transport in front of several FakeWP sites, routed by host (wordpress.org goes to the first)."""

    def __init__(self, *sites: FakeWP) -> None:
        self.sites = {f.host: f for f in sites}
        self.first = sites[0]

    def __call__(self, method, url, headers, body, timeout):
        return self.sites.get(urlparse(url).hostname, self.first)(method, url, headers, body, timeout)


def pair(rt, site_id: str = "s"):
    """Connect a keyed test site the v0.2 way: fill its settings group (rt.get_setting must be a
    Settings) and write the pairing record a good connection check writes."""
    groups = mod("groups")
    site = rt.sites.get(site_id)
    n = site["key_slot"]
    rt.get_setting.update({f"site{n}_url": site["url"], f"site{n}_user": site["login"]})
    password = rt.get_secret(f"WPBRUECKE_KEY{n}")
    if password:
        rt.records.put(groups.PairingRecord(n=n, host=groups.host(site["url"]), fp=groups.fingerprint(password),
                                            proven_at="2026-11-24T00:00:00Z", site_id=site_id,
                                            username=site["login"]))
    return site


def confirm(rt, site_id: str = "s", level: str | None = None) -> dict | None:
    """What the page's Confirm writes: the live reading of the site's user as its access level (or ``level``
    when given). None when WordPress' answer is a level Hermes refuses."""
    enforce = mod("enforce")
    site = rt.sites.get(site_id)
    live = enforce.read_live(rt, site)
    if level is None and live.level not in mod("access").LEVELS:
        return None
    granted = {"level": level or live.level, "role": (live.roles or ("",))[0], "caps_fingerprint": live.caps_fp,
               "confirmed_at": "2026-11-24T00:00:00Z"}
    rt.sites.update(site_id, {"access": granted}, "page", confirm=True)
    enforce.forget(rt, site_id)
    return granted


def create_paired(rt, fields: dict, author: str = "person", confirmed: bool = True) -> dict:
    """rt.sites.create, then pair() when the site names a group, then confirm() its live level (the page's
    Confirm) unless ``confirmed=False``."""
    res = rt.sites.create(fields, author)
    if fields.get("key_slot"):
        pair(rt, fields["id"])
        if confirmed:
            confirm(rt, fields["id"])
    return res


# --- v0.3 watcher: api.wordpress.org, the connector's update list, the home page's health ----------

class WporgFake:
    """api.wordpress.org: newest versions per kind, a switch that fails every read, and a clock that moves
    two days per read so the facts' day cache never hides a change between ticks."""

    def __init__(self, core: str = "7.1.4", plugins: dict | None = None, themes: dict | None = None) -> None:
        self.core, self.plugins, self.themes = core, dict(plugins or {}), dict(themes or {})
        self.fail = False
        self.reads = 0
        self._t = 0.0

    def fetch(self, url: str):
        self.reads += 1
        if self.fail:
            raise OSError("api.wordpress.org did not answer")
        if "core/version-check" in url:
            return {"offers": [{"current": self.core}]}
        slug = (parse_qs(urlparse(url).query).get("request[slug]") or [""])[0]
        table = self.plugins if "/plugins/" in url else self.themes
        return {"version": table[slug]} if slug in table else {"error": "Not found."}

    def clock(self) -> float:
        self._t += 2 * 86400
        return self._t

    def attach(self, rt) -> "WporgFake":
        rt.wporg = mod("facts").WporgVersions(rt.store, fetch=self.fetch, clock=self.clock)
        return self

    @staticmethod
    def forget(rt) -> None:
        """Drop the day cache, as a new day or a fresh install does."""
        path = rt.store.path("facts", "_wporg.json")
        if path.exists():
            path.unlink()


CONNECTOR_NAMESPACES = ["oembed/1.0", "wp/v2", "wp-site-health/v1", "wp-abilities/v1", "wpbruecke/v1"]


def connector_updates(fake: FakeWP, plugins: dict | None = None, themes: dict | None = None,
                      core: str | None = None, installed: str = "1.0") -> None:
    """The connector's /info with the update list it reads from WordPress' transients (premium plugins
    included): ``plugins`` and ``themes`` map slug to the offered version."""
    ups = {"core": {"installed": "7.1.4", "latest": core} if core else None,
           "plugins": [{"slug": k, "installed": installed, "latest": v} for k, v in (plugins or {}).items()],
           "themes": [{"slug": k, "installed": installed, "latest": v} for k, v in (themes or {}).items()]}
    ups["count"] = len(ups["plugins"]) + len(ups["themes"]) + (1 if core else 0)
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.2.1", "updates": ups})


# WordPress' wp_die page as core renders it (German site: the markup ids stay English).
WP_ERROR_PAGE = """<!DOCTYPE html>
<html lang="de-DE"><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<meta name="viewport" content="width=device-width"><meta name='robots' content='noindex, follow' />
<title>WordPress &rsaquo; Fehler</title></head>
<body id="error-page">\n\t<div class="wp-die-message"><p>Es gab einen kritischen Fehler auf deiner Website.</p>
<p><a href="https://wordpress.org/documentation/article/faq-troubleshooting/">Erfahre mehr über die Fehlerbehebung in WordPress.</a></p></div></body></html>"""
WP_MAINTENANCE_PAGE = WP_ERROR_PAGE.replace(
    "Es gab einen kritischen Fehler auf deiner Website.", "Wegen Wartungsarbeiten ist dies vorübergehend nicht verfügbar.")


def home_fault(fake: FakeWP, mode: str) -> None:
    """What the public home page answers. ``fatal``: 500 with the error page while REST still answers (a
    front-end fatal); ``maintenance``: WordPress' maintenance mode, 503 with Retry-After on every URL;
    ``plain500``, ``plain503`` (no Retry-After), ``die200`` (the error page with 200); ``off`` restores."""
    for key in (("GET", "/"), ("GET", "/wp-json/")):
        fake.routes.pop(key, None)
    fake.__dict__.pop("default", None)
    html_h = {"Content-Type": "text/html; charset=utf-8"}
    if mode == "fatal":
        fake.routes[("GET", "/")] = (500, html_h, WP_ERROR_PAGE.encode())
    elif mode == "maintenance":
        answer = (503, {**html_h, "Retry-After": "600"}, WP_MAINTENANCE_PAGE.encode())
        fake.routes[("GET", "/")] = answer
        fake.routes[("GET", "/wp-json/")] = answer
        fake.default = lambda method, parsed, headers, body: answer
    elif mode == "plain500":
        fake.routes[("GET", "/")] = (500, html_h, b"<html><body>Internal Server Error</body></html>")
    elif mode == "plain503":
        fake.routes[("GET", "/")] = (503, html_h, b"<html><body>Service Unavailable</body></html>")
    elif mode == "die200":
        fake.routes[("GET", "/")] = (200, html_h, WP_ERROR_PAGE.encode())
    elif mode != "off":
        raise ValueError(mode)
