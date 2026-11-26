"""Site files: one readable YAML file per site, edited by the page and by Hermes alike (F16).

Shape::

    id: test-wp                        # slug, also the file name sites/<id>.yaml
    url: https://test-wp.example       # the site's address
    login: hermes                      # the group's WordPress user (login or email), from the settings
    username: hermes                   # the user's name from users/me after the first good check
    key_slot: 1                        # 1..16 = the settings group (WPBRUECKE_KEY<n>); 0 = public only
    access: null                       # {level, role, caps_fingerprint, confirmed_at}, only the Confirm writes it
    connect_ip: ""                     # advanced: connect here, keep the host name for TLS (constraint 10)
    group: ""                          # fleet filter chip
    label: ""                          # display name; empty = the site's own title
    packs: {generatepress: false}      # registry ids only (packs/__init__.py); guarded: switching one on widens writes
    design: {}                         # overrides only, over the live theme tokens: {colors: [{slug, name, value}],
                                       # fonts: {body, headings}, logo}
    proof: {widths: [1440, 768, 390], extra_hosts: []}
    memory: {tone: "", languages: [], do_not_touch: [], notes: ""}
    automations:                       # recurring drafts (F14, D18 a)
      - {id: weekly-notes, kind: recurring_draft, schedule: "0 7 * * 1", prompt: "...", enabled: true,
         publish: false}
    gsc_property: ""                   # optional Search Console property (sc-domain:example.com or a URL prefix)
    cockpit_app: ""                    # optional Coolify app uuid: the site card links into Coolifybrücke

Every save keeps a version in ``sites/<id>.history/<n>.yaml`` plus ``<n>.json`` (author, time,
summary); the last ``KEEP_VERSIONS`` are kept. The file never holds a secret. url, login and
key_slot of a keyed site mirror its settings group (groups.sync). Two site files never share a host.
A 0.1.0 file's ``publishing``, ``never_publish_never_send`` and ``agent_writes`` still load: they are
handed to ``legacy_sink`` once (the migration keeps them for the page) and dropped on the next save.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import packs as packs_mod
from . import yamlish
from .store import Store, StoreError, check_id, now_iso

KEEP_VERSIONS = 20
MAX_SLOT = 16
LOGIN_MAX = 100
DEFAULT_WIDTHS = [1440, 768, 390]
AUTHORS = ("hermes", "person", "page", "cli", "settings", "migration", "check")
LEGACY_FIELDS = ("publishing", "never_publish_never_send", "agent_writes")
LEVELS = ("read_public", "read", "drafts", "publish")
# Fields that widen or redirect what the agent may do: an agent edit of these asks the user first
# (hooks.py), and an unattended run may not change them at all.
GUARDED_FIELDS = frozenset({"url", "login", "key_slot", "connect_ip", "automations", "packs"})
# A keyed site's url, login and key_slot come from its settings group; no tool or request body sets them.
SETTINGS_FIELDS = frozenset({"url", "login", "key_slot"})
# Written by WPBrücke itself: access by the page's Confirm, username by the connection check.
SYSTEM_FIELDS = frozenset({"access", "username"})
_FP_RE = re.compile(r"^[0-9a-f]{12}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_GSC_DOMAIN = re.compile(r"^sc-domain:[A-Za-z0-9.-]{1,253}$")
_APP_RE = re.compile(r"^[a-z0-9]{8,40}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
_CSS_COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{3,8}|(rgb|rgba|hsl|hsla)\([0-9.,%\s/]+\)|[a-z]{3,20})$")
# Desktop routes hermes://open/<page>?q in-app; a bare path or another scheme would open nothing.
COCKPIT_ROUTE = "hermes://open/coolifybruecke"
_SECRETISH = re.compile(r"(password|secret|token|app_?key|apikey)", re.I)

HEADER = ("WPBrücke site file. Edit through the WPBrücke page or ask Hermes (wp_sites action=update);\n"
          "both keep a version with author and time. Never put a password here: key_slot names the\n"
          "Site N group in Hermes' plugin settings, which holds the address, user and password.")


class SiteError(ValueError):
    pass


class DuplicateHost(SiteError):
    pass


class UnknownPackError(SiteError):
    code = "unknown_pack"


def defaults(site_id: str = "") -> dict:
    return {
        "id": site_id, "url": "", "login": "", "username": "", "key_slot": 0, "access": None, "connect_ip": "",
        "group": "", "label": "",
        "packs": {pid: False for pid in packs_mod.ids()},
        "design": {},
        "proof": {"widths": list(DEFAULT_WIDTHS), "extra_hosts": []},
        "memory": {"tone": "", "languages": [], "do_not_touch": [], "notes": ""},
        "automations": [],
        "gsc_property": "", "cockpit_app": "",
    }


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("true", "1", "yes", "on"):
        return True
    if str(value).lower() in ("false", "0", "no", "off", ""):
        return False
    raise SiteError(f"{name} must be true or false")


def _str_list(value: Any, name: str, limit: int = 50) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
        raise SiteError(f"{name} must be a list of strings")
    if len(value) > limit:
        raise SiteError(f"{name} holds at most {limit} entries")
    return [str(v).strip()[:200] for v in value]


def normalise_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or not _HOST_RE.match(parsed.hostname):
        raise SiteError(f"url must be http(s)://host, got {url[:80]!r}")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise SiteError("url must not carry a query, fragment or user name (the key goes into a key slot)")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _access(value: Any) -> Optional[dict]:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict) or set(value) - {"level", "role", "caps_fingerprint", "confirmed_at"}:
        raise SiteError("access is {level, role, caps_fingerprint, confirmed_at} or null")
    level = str(value.get("level") or "")
    if level not in LEVELS:
        raise SiteError(f"access.level must be one of {', '.join(LEVELS)}")
    fp = str(value.get("caps_fingerprint") or "")
    if not _FP_RE.match(fp):
        raise SiteError("access.caps_fingerprint must be 12 hex characters")
    return {"level": level, "role": str(value.get("role") or "")[:60], "caps_fingerprint": fp,
            "confirmed_at": str(value.get("confirmed_at") or "")[:40]}


def _design(value: Any) -> dict:
    """The person's design overrides: only the keys they set (colors, fonts, logo)."""
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict) or set(value) - {"colors", "fonts", "logo"}:
        raise SiteError("design has colors, fonts and logo (overrides of the theme's own tokens)")
    out: dict = {}
    colors = value.get("colors") or []
    if not isinstance(colors, list) or len(colors) > 30:
        raise SiteError("design.colors is a list of at most 30 {slug, name, value}")
    clean, seen = [], set()
    for c in colors:
        if not isinstance(c, dict) or set(c) - {"slug", "name", "value"}:
            raise SiteError("each design colour is {slug, name, value}")
        slug, val = str(c.get("slug") or "").strip().lower(), str(c.get("value") or "").strip()
        if not _SLUG_RE.match(slug) or slug in seen:
            raise SiteError(f"design colour slug {slug!r}: a unique lowercase slug")
        if not _CSS_COLOR_RE.match(val):
            raise SiteError(f"design colour {slug}: value must be a CSS colour such as #1a2b3c")
        seen.add(slug)
        clean.append({"slug": slug, "name": str(c.get("name") or slug).strip()[:100], "value": val})
    if clean:
        out["colors"] = clean
    fonts = value.get("fonts") or {}
    if not isinstance(fonts, dict) or set(fonts) - {"body", "headings"}:
        raise SiteError("design.fonts has body and headings")
    fonts = {k: str(v).strip()[:100] for k, v in fonts.items() if str(v or "").strip()}
    if fonts:
        out["fonts"] = fonts
    logo = str(value.get("logo") or "").strip()
    if logo:
        if not (logo.startswith("https://") or logo.startswith("http://")) or len(logo) > 500:
            raise SiteError("design.logo is the logo's URL (https://...)")
        out["logo"] = logo
    return out


def validate(raw: dict) -> dict:
    """A complete, checked site dict, or SiteError naming the first problem."""
    if not isinstance(raw, dict):
        raise SiteError("a site must be a mapping")
    for key in raw:
        if _SECRETISH.search(str(key)):
            raise SiteError(f"{key}: a site file never holds a secret; use key_slot and the plugin settings")
    site = defaults()
    unknown = set(raw) - set(site)
    if unknown:
        raise SiteError(f"unknown field(s): {', '.join(sorted(unknown))}")
    try:
        site["id"] = check_id(raw.get("id"), "site id")
    except StoreError as e:
        raise SiteError(str(e)) from None
    site["url"] = normalise_url(str(raw.get("url") or ""))
    site["login"] = str(raw.get("login") or "").strip()[:LOGIN_MAX]
    site["username"] = str(raw.get("username") or "").strip()[:LOGIN_MAX]
    try:
        slot = int(raw.get("key_slot") or 0)
    except (TypeError, ValueError):
        raise SiteError(f"key_slot must be a number from 0 (public only) to {MAX_SLOT}") from None
    if not 0 <= slot <= MAX_SLOT:
        raise SiteError(f"key_slot must be a number from 0 (public only) to {MAX_SLOT}")
    site["key_slot"] = slot
    site["access"] = _access(raw.get("access"))
    ip = str(raw.get("connect_ip") or "").strip()
    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise SiteError(f"connect_ip must be an IP address, got {ip[:40]!r}") from None
    site["connect_ip"] = ip
    site["group"] = str(raw.get("group") or "").strip()[:40]
    site["label"] = str(raw.get("label") or "").strip()[:80]
    gsc = str(raw.get("gsc_property") or "").strip()
    if gsc and not (_GSC_DOMAIN.match(gsc) or gsc.startswith("https://") or gsc.startswith("http://")):
        raise SiteError("gsc_property is sc-domain:<domain> or the property's URL prefix (https://...)")
    site["gsc_property"] = gsc[:200]
    app = str(raw.get("cockpit_app") or "").strip()
    if app and not _APP_RE.match(app):
        raise SiteError("cockpit_app is a Coolify app uuid (letters and digits)")
    site["cockpit_app"] = app
    try:
        site["packs"] = packs_mod.validate_site_packs(raw.get("packs"), _bool)
    except packs_mod.UnknownPack as e:
        raise UnknownPackError(str(e)) from None
    site["design"] = _design(raw.get("design"))
    proof = raw.get("proof") or {}
    if not isinstance(proof, dict) or set(proof) - {"widths", "extra_hosts"}:
        raise SiteError("proof has widths and extra_hosts")
    widths = proof.get("widths") or list(DEFAULT_WIDTHS)
    try:
        widths = [int(w) for w in widths]
    except (TypeError, ValueError):
        raise SiteError("proof.widths must be numbers") from None
    if not 1 <= len(widths) <= 4 or any(w < 320 or w > 2560 for w in widths):
        raise SiteError("proof.widths: one to four widths between 320 and 2560")
    hosts = _str_list(proof.get("extra_hosts"), "proof.extra_hosts", 20)
    for h in hosts:
        if not _HOST_RE.match(h):
            raise SiteError(f"proof.extra_hosts: {h!r} is not a host name")
    site["proof"] = {"widths": widths, "extra_hosts": hosts}
    mem = raw.get("memory") or {}
    if not isinstance(mem, dict) or set(mem) - {"tone", "languages", "do_not_touch", "notes"}:
        raise SiteError("memory has tone, languages, do_not_touch and notes")
    site["memory"] = {"tone": str(mem.get("tone") or "")[:300],
                      "languages": _str_list(mem.get("languages"), "memory.languages", 10),
                      "do_not_touch": _str_list(mem.get("do_not_touch"), "memory.do_not_touch"),
                      "notes": str(mem.get("notes") or "")[:4000]}
    autos = raw.get("automations") or []
    if not isinstance(autos, list):
        raise SiteError("automations must be a list")
    seen, clean = set(), []
    for a in autos:
        if not isinstance(a, dict):
            raise SiteError("each automation is a mapping")
        try:
            aid = check_id(a.get("id"), "automation id")
        except StoreError as e:
            raise SiteError(str(e)) from None
        if aid in seen:
            raise SiteError(f"automation id {aid} is used twice")
        seen.add(aid)
        kind = str(a.get("kind") or "recurring_draft")
        if kind != "recurring_draft":
            raise SiteError("automation kind: only recurring_draft exists")
        extra = set(a) - {"id", "kind", "schedule", "prompt", "post_type", "enabled", "job_id", "publish"}
        if extra:
            raise SiteError(f"automation {aid}: unknown field(s) {', '.join(sorted(extra))}")
        ptype = str(a.get("post_type") or "post")
        if ptype not in ("post", "page"):
            raise SiteError("automation post_type: post or page")
        clean.append({"id": aid, "kind": kind, "schedule": str(a.get("schedule") or "")[:60],
                      "prompt": str(a.get("prompt") or "")[:4000], "post_type": ptype,
                      "enabled": _bool(a.get("enabled", True), "automation enabled"),
                      "publish": _bool(a.get("publish", False), "automation publish"),
                      "job_id": str(a.get("job_id") or "")[:64]})
    site["automations"] = clean
    return site


def host_of(site: dict) -> str:
    return urlparse(site["url"]).hostname or ""


def merge(base: dict, changes: dict) -> dict:
    """Changes applied onto a site: nested mappings merge one level deep, lists are replaced."""
    out = copy.deepcopy(base)
    for k, v in (changes or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def diff(old: Optional[dict], new: dict) -> list:
    """Flat, readable lines: `field: old -> new` (nested fields dotted)."""
    def flat(d: Optional[dict], prefix: str = "") -> dict:
        out = {}
        for k, v in (d or {}).items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(flat(v, key + "."))
            else:
                out[key] = v
        return out
    a, b = flat(old), flat(new)
    lines = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            lines.append(f"{k}: {json.dumps(a.get(k), ensure_ascii=False)} -> {json.dumps(b.get(k), ensure_ascii=False)}")
    return lines


class SiteStore:
    def __init__(self, store: Store, legacy_sink: Optional[Callable[[str, dict], None]] = None) -> None:
        self.store = store
        self.legacy_sink = legacy_sink

    def _file(self, site_id: str):
        return self.store.path("sites", f"{check_id(site_id, 'site id')}.yaml")

    def _hist(self, site_id: str):
        path = self.store.path("sites", f"{check_id(site_id, 'site id')}.history")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ids(self) -> list:
        return sorted(p.stem for p in self.store.dir("sites").glob("*.yaml"))

    def get(self, site_id: str) -> Optional[dict]:
        path = self._file(site_id)
        if not path.exists():
            return None
        data = yamlish.load(path.read_text(encoding="utf-8"))
        data.setdefault("id", site_id)
        legacy = {k: data.pop(k) for k in LEGACY_FIELDS if k in data}
        if legacy and self.legacy_sink is not None:
            self.legacy_sink(site_id, legacy)
        return validate(data)

    def all(self) -> list:
        out = []
        for sid in self.ids():
            try:
                out.append(self.get(sid))
            except (SiteError, ValueError) as e:
                out.append({"id": sid, "error": f"site file does not validate: {e}"})
        return out

    def versions(self, site_id: str) -> list:
        hist = self._hist(site_id)
        metas = []
        for p in sorted(hist.glob("*.json"), key=lambda p: int(p.stem)):
            meta = self.store.read_json(p, {}) or {}
            metas.append({"version": int(p.stem), **meta})
        return metas

    def _save(self, site: dict, author: str, summary: str) -> dict:
        if author not in AUTHORS:
            raise SiteError(f"author must be one of {', '.join(AUTHORS)}")
        hist = self._hist(site["id"])
        existing = sorted((int(p.stem) for p in hist.glob("*.json")), reverse=True)
        version = (existing[0] + 1) if existing else 1
        text = yamlish.dump(site, HEADER)
        self.store.write_text(self._file(site["id"]), text)
        self.store.write_text(hist / f"{version}.yaml", text)
        self.store.write_json(hist / f"{version}.json", {"author": author, "at": now_iso(), "summary": summary[:300]})
        for old in existing[KEEP_VERSIONS - 1:]:
            for ext in ("yaml", "json"):
                (hist / f"{old}.{ext}").unlink(missing_ok=True)
        return {"version": version, "author": author}

    def _check_host(self, site: dict) -> None:
        mine = host_of(site)
        for other in self.all():
            if other.get("id") != site["id"] and "error" not in other and host_of(other) == mine:
                raise DuplicateHost(f"site {other['id']} already uses {mine}; one site file per host")

    def create(self, raw: dict, author: str) -> dict:
        site = validate(raw)
        with self.store.lock("sites"):
            if self._file(site["id"]).exists():
                raise SiteError(f"site {site['id']} exists; use update")
            self._check_host(site)
            saved = self._save(site, author, "created")
        return {"site": site, "diff": diff(None, site), **saved}

    def update(self, site_id: str, changes: dict, author: str, preview: bool = False, confirm: bool = False) -> dict:
        """``confirm=True`` is the Confirm route's write; no other caller may change ``access``."""
        if "id" in (changes or {}) and changes["id"] != site_id:
            raise SiteError("the id of a site cannot change; create a new site instead")
        with self.store.lock("sites"):
            old = self.get(site_id)
            if old is None:
                raise SiteError(f"no site {site_id}")
            new = validate(merge(old, {k: v for k, v in (changes or {}).items() if k != "access"}))
            if "access" in (changes or {}):
                wanted = _access(changes["access"])
                if wanted != old.get("access") and not confirm:
                    raise SiteError("access is written only by Confirm on the WPBrücke page")
                new["access"] = wanted
            if host_of(new) != host_of(old):
                self._check_host(new)
            lines = diff(old, new)
            if preview or not lines:
                return {"site": new, "diff": lines, "preview": preview, "changed": bool(lines)}
            saved = self._save(new, author, "; ".join(lines)[:300])
        return {"site": new, "diff": lines, "changed": True, **saved}

    def restore(self, site_id: str, version: int, author: str) -> dict:
        hist = self._hist(site_id)
        path = hist / f"{int(version)}.yaml"
        if not path.exists():
            raise SiteError(f"site {site_id} has no version {version}")
        data = yamlish.load(path.read_text(encoding="utf-8"))
        for k in LEGACY_FIELDS:
            data.pop(k, None)
        data = validate(data)
        current = self.get(site_id)
        data["access"] = (current or {}).get("access")  # a restore never brings back an old confirmation
        if current and current.get("key_slot"):
            data.update({k: current[k] for k in SETTINGS_FIELDS})
        return self.update(site_id, data, author)

    def delete(self, site_id: str) -> bool:
        with self.store.lock("sites"):
            path = self._file(site_id)
            if not path.exists():
                return False
            path.unlink()
            return True


def cockpit_url(site: dict) -> "str | None":
    """The site card's link into Coolifybrücke: its page with the app preselected."""
    app = site.get("cockpit_app") or ""
    return f"{COCKPIT_ROUTE}?app={app}" if app else None
