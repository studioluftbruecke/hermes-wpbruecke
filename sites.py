"""Site files: one readable YAML file per site, edited by the page and by Hermes alike (F16).

Shape::

    id: test-wp                        # slug, also the file name sites/<id>.yaml
    url: https://test-wp.example       # the site's address
    login: hermes-drafter              # WordPress user name behind the key slot
    key_slot: 1                        # 1..12 -> WPBRUECKE_KEY<n> in the profile .env; 0 = public only
    connect_ip: ""                     # advanced: connect here, keep the host name for TLS (constraint 10)
    group: ""                          # fleet filter chip
    label: ""                          # display name; empty = the site's own title
    publishing: off                    # off | confirm | schedule (F8)
    never_publish_never_send: true     # the hard rule, on by default (A15)
    agent_writes: ""                   # "" = the plugin setting; auto | always
    packs: {generatepress: false}
    proof: {widths: [1440, 768, 390], extra_hosts: []}
    memory: {tone: "", languages: [], do_not_touch: [], notes: ""}
    automations:                       # recurring drafts (F14, D18 a)
      - {id: weekly-notes, kind: recurring_draft, schedule: "0 7 * * 1", prompt: "...", enabled: true}
    gsc_property: ""                   # optional Search Console property (sc-domain:example.com or a URL prefix)
    cockpit_app: ""                    # optional Coolify app uuid: the site card links into Coolifybrücke

Every save keeps a version in ``sites/<id>.history/<n>.yaml`` plus ``<n>.json`` (author, time,
summary); the last ``KEEP_VERSIONS`` are kept. The file never holds a secret.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from . import yamlish
from .store import Store, StoreError, check_id, now_iso

KEEP_VERSIONS = 20
PUBLISHING = ("off", "confirm", "schedule")
AGENT_WRITES = ("", "auto", "always")
DEFAULT_WIDTHS = [1440, 768, 390]
AUTHORS = ("hermes", "person", "page", "cli")
# Fields that widen or redirect what the agent may do: an agent edit of these asks the user first
# (hooks.py), and an unattended run may not change them at all.
GUARDED_FIELDS = frozenset({"url", "login", "key_slot", "connect_ip", "publishing", "never_publish_never_send",
                            "agent_writes", "automations"})
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_GSC_DOMAIN = re.compile(r"^sc-domain:[A-Za-z0-9.-]{1,253}$")
_APP_RE = re.compile(r"^[a-z0-9]{8,40}$")
# Desktop routes hermes://open/<page>?q in-app; a bare path or another scheme would open nothing.
COCKPIT_ROUTE = "hermes://open/coolifybruecke"
_SECRETISH = re.compile(r"(password|secret|token|app_?key|apikey)", re.I)

HEADER = ("WPBrücke site file. Edit through the WPBrücke page or ask Hermes (wp_sites action=update);\n"
          "both keep a version with author and time. Never put a password here: key_slot names the\n"
          "WPBRUECKE_KEY<n> field in Hermes' plugin settings.")


class SiteError(ValueError):
    pass


def defaults(site_id: str = "", publishing: str = "off") -> dict:
    return {
        "id": site_id, "url": "", "login": "", "key_slot": 0, "connect_ip": "", "group": "", "label": "",
        "publishing": publishing, "never_publish_never_send": True, "agent_writes": "",
        "packs": {"generatepress": False},
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


def validate(raw: dict, publishing_default: str = "off") -> dict:
    """A complete, checked site dict, or SiteError naming the first problem."""
    if not isinstance(raw, dict):
        raise SiteError("a site must be a mapping")
    for key in raw:
        if _SECRETISH.search(str(key)):
            raise SiteError(f"{key}: a site file never holds a secret; use key_slot and the plugin settings")
    site = defaults(publishing=publishing_default)
    unknown = set(raw) - set(site)
    if unknown:
        raise SiteError(f"unknown field(s): {', '.join(sorted(unknown))}")
    try:
        site["id"] = check_id(raw.get("id"), "site id")
    except StoreError as e:
        raise SiteError(str(e)) from None
    site["url"] = normalise_url(str(raw.get("url") or ""))
    site["login"] = str(raw.get("login") or "").strip()[:60]
    try:
        slot = int(raw.get("key_slot") or 0)
    except (TypeError, ValueError):
        raise SiteError("key_slot must be a number from 0 (public only) to 12") from None
    if not 0 <= slot <= 12:
        raise SiteError("key_slot must be a number from 0 (public only) to 12")
    if slot and not site["login"]:
        raise SiteError("login (the WordPress user name of the key) is needed when key_slot is set")
    site["key_slot"] = slot
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
    pub = str(raw.get("publishing", site["publishing"]) or "off").strip().lower()
    if pub not in PUBLISHING:
        raise SiteError(f"publishing must be one of {', '.join(PUBLISHING)}")
    site["publishing"] = pub
    site["never_publish_never_send"] = _bool(raw.get("never_publish_never_send", True), "never_publish_never_send")
    aw = str(raw.get("agent_writes") or "").strip().lower()
    if aw not in AGENT_WRITES:
        raise SiteError("agent_writes must be auto, always or empty (the plugin setting)")
    site["agent_writes"] = aw
    packs = raw.get("packs") or {}
    if not isinstance(packs, dict) or set(packs) - {"generatepress"}:
        raise SiteError("packs knows only generatepress: true|false")
    site["packs"] = {"generatepress": _bool(packs.get("generatepress", False), "packs.generatepress")}
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
        extra = set(a) - {"id", "kind", "schedule", "prompt", "post_type", "enabled", "job_id"}
        if extra:
            raise SiteError(f"automation {aid}: unknown field(s) {', '.join(sorted(extra))}")
        ptype = str(a.get("post_type") or "post")
        if ptype not in ("post", "page"):
            raise SiteError("automation post_type: post or page")
        clean.append({"id": aid, "kind": kind, "schedule": str(a.get("schedule") or "")[:60],
                      "prompt": str(a.get("prompt") or "")[:4000], "post_type": ptype,
                      "enabled": _bool(a.get("enabled", True), "automation enabled"),
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
    def __init__(self, store: Store, publishing_default: str = "off") -> None:
        self.store = store
        self.publishing_default = publishing_default

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
        return validate(data, self.publishing_default)

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

    def create(self, raw: dict, author: str) -> dict:
        site = validate(raw, self.publishing_default)
        with self.store.lock("sites"):
            if self._file(site["id"]).exists():
                raise SiteError(f"site {site['id']} exists; use update")
            saved = self._save(site, author, "created")
        return {"site": site, "diff": diff(None, site), **saved}

    def update(self, site_id: str, changes: dict, author: str, preview: bool = False) -> dict:
        if "id" in (changes or {}) and changes["id"] != site_id:
            raise SiteError("the id of a site cannot change; create a new site instead")
        with self.store.lock("sites"):
            old = self.get(site_id)
            if old is None:
                raise SiteError(f"no site {site_id}")
            new = validate(merge(old, changes), self.publishing_default)
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
        data = validate(yamlish.load(path.read_text(encoding="utf-8")), self.publishing_default)
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
