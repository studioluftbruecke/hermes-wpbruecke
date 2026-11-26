"""The abilities lane: WordPress' Abilities API (``wp-abilities/v1``, WordPress 6.9+) as WPBrücke runs it.

- ``discover(client)`` lists every ability the site registers (the listing is not filtered by the user's
  permission, P29; WordPress checks permission when an ability runs). facts.py keeps the listing with the
  facts (``facts["_abilities"]``, five minutes), so ``wp_doctor`` and ``wp_pack`` share one read.
- ``run(client, ability, input)`` sends the method the ability's annotations ask for (P30): GET with
  ``input[...]`` in the query for a read-only ability, DELETE for a destructive and idempotent one, POST
  ``{"input": ...}`` for everything else. A read-only ability whose input schema wants an object gets the
  op's ``default_input`` when the call has none (P30: FluentCRM's ``list-campaigns`` answers 400 without).
- WordPress' answers are mapped to WPBrücke's refusal shape (``{"ok": False, "error", "message"}``): an
  ability the site does not have, a permission WordPress refuses (named in the sentence), input WordPress
  does not accept.
- Only abilities a pack declares in an ``OpSpec`` are runnable, and only on a site where that pack is on
  (plan D24 a). ``run_declared`` refuses any other name before any request.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

from .client import WordPressClient, WPError

NAMESPACE = "wp-abilities/v1"
BASE = "/wp-json/wp-abilities/v1/abilities"
LIST_LIMIT = 1000
NOT_FOUND_CODES = frozenset({"rest_ability_not_found", "ability_not_found"})
PERMISSION_CODES = frozenset({"ability_invalid_permissions", "rest_ability_cannot_execute", "rest_forbidden", "forbidden"})
INPUT_CODES = frozenset({"ability_invalid_input", "rest_invalid_param", "invalid_param", "rest_missing_callback_param",
                         "ability_missing_input_schema"})
_PERMISSION_TOKEN = re.compile(r"\b((?:fcrm|wc|woocommerce|elementor)_[a-z0-9_]+|(?:read|edit|publish|delete|manage|"
                               r"create|upload|list|moderate)_[a-z0-9_]+)\b")


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Ability:
    """One ability as the site lists it. The annotations decide the method; a missing one counts as False."""
    name: str
    label: str = ""
    description: str = ""
    category: str = ""
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    readonly: bool = False
    destructive: bool = False
    idempotent: bool = False

    @property
    def namespace(self) -> str:
        return self.name.split("/", 1)[0]

    @property
    def method(self) -> str:
        if self.readonly:
            return "GET"
        if self.destructive and self.idempotent:
            return "DELETE"
        return "POST"

    @property
    def wants_object(self) -> bool:
        return self.input_schema.get("type") == "object" or bool(self.input_schema.get("properties"))

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "description": self.description, "category": self.category,
                "input_schema": self.input_schema, "output_schema": self.output_schema,
                "annotations": {"readonly": self.readonly, "destructive": self.destructive,
                                "idempotent": self.idempotent}, "method": self.method}

    @classmethod
    def from_dict(cls, raw: dict) -> "Ability":
        ann = raw.get("annotations") or ((raw.get("meta") or {}).get("annotations") if isinstance(raw.get("meta"), dict) else None) or {}
        return cls(name=str(raw["name"]), label=str(raw.get("label") or ""), description=str(raw.get("description") or ""),
                   category=str(raw.get("category") or ""),
                   input_schema=_dict(raw.get("input_schema")), output_schema=_dict(raw.get("output_schema")),
                   readonly=ann.get("readonly") is True, destructive=ann.get("destructive") is True,
                   idempotent=ann.get("idempotent") is True)


@dataclass(frozen=True)
class AbilityRun:
    """What one run answered. ``sent`` says whether a run request reached WordPress; ``error`` is the
    refusal code (None when WordPress ran the ability)."""
    ability: str
    method: str
    sent: bool
    status: int = 0
    result: Any = None
    error: Optional[str] = None
    message: str = ""
    wp_code: str = ""
    permission: Optional[str] = None
    ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        out: dict = {"ok": self.ok, "ability": self.ability, "method": self.method, "sent": self.sent}
        if self.status:
            out["status"] = self.status
        if self.ok:
            out["result"] = self.result
            return out
        out.update(error=self.error, message=self.message)
        if self.wp_code:
            out["wp_code"] = self.wp_code
        if self.permission:
            out["permission"] = self.permission
        return out


# --- discover ---------------------------------------------------------------------------------------

def parse_listing(items: Any) -> list:
    """The listing's JSON -> Abilities, sorted by name; entries without a name are dropped."""
    out = {}
    for raw in items if isinstance(items, list) else []:
        if isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"]:
            out[raw["name"]] = Ability.from_dict(raw)
    return [out[k] for k in sorted(out)]


def discover(client: WordPressClient) -> list:
    """Every ability the site lists to this user (paged, 100 per page). Raises WPError."""
    return parse_listing(client.get_all(BASE, limit=LIST_LIMIT))


def summary(found: list) -> dict:
    by_ns: dict = {}
    for a in found:
        by_ns[a.namespace] = by_ns.get(a.namespace, 0) + 1
    return {"count": len(found), "namespaces": dict(sorted(by_ns.items())), "error": None}


def listing_from_facts(facts: dict) -> Optional[dict]:
    """{name: Ability} from the facts cache, or None when the site's abilities were not read."""
    raw = (facts or {}).get("_abilities")
    if not isinstance(raw, list):
        return None
    return {a.name: a for a in (Ability.from_dict(r) for r in raw if isinstance(r, dict) and r.get("name"))}


# --- run --------------------------------------------------------------------------------------------

def run_path(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", name or ""):
        raise ValueError(f"not an ability name: {name!r} (namespace/ability, lowercase)")
    return f"{BASE}/{name}/run"


def query_pairs(value: Any, prefix: str = "input") -> list:
    """``input[...]`` query pairs for a GET or DELETE run: nested keys and list indexes in brackets,
    booleans as true/false (WordPress' schema validation turns them back into types)."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += query_pairs(v, f"{prefix}[{k}]")
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for i, v in enumerate(value):
            out += query_pairs(v, f"{prefix}[{i}]")
        return out
    if value is None:
        return []
    if isinstance(value, bool):
        return [(prefix, "true" if value else "false")]
    return [(prefix, str(value))]


def prepared_input(ability: Ability, given: Optional[dict], default_input: Optional[dict] = None) -> Optional[dict]:
    """What the run sends: the call's input; else, for a read-only ability whose schema wants input, the op's
    default (P30); else an empty object, or nothing at all when the ability declares no input schema
    (WordPress refuses input it cannot validate)."""
    data = dict(given or {})
    if data:
        return data
    if not ability.input_schema:
        return None
    if ability.readonly and default_input:
        return dict(default_input)
    return {}


def _body(resp: Any) -> Any:
    try:
        return json.loads(resp.body.decode("utf-8", "replace") or "null")
    except ValueError:
        return {"_raw": resp.body[:200].decode("utf-8", "replace")}


def wp_message(body: Any) -> "tuple[str, list]":
    """WordPress' sentence and any permission names it lists. Some plugins (FluentCRM) put a JSON error into
    ``message`` and the permissions into ``details`` or ``additional_data``."""
    body = body if isinstance(body, dict) else {}
    text = str(body.get("message") or "")
    perms: list = []
    if text.lstrip().startswith("{"):
        try:
            inner = json.loads(text).get("error") or {}
            text = str(inner.get("message") or text)
            perms += list((inner.get("details") or {}).get("required_permissions") or [])
        except (ValueError, AttributeError):
            pass
    for extra in body.get("additional_data") or []:
        if isinstance(extra, dict):
            perms += list(extra.get("required_permissions") or [])
    perms = [p for p in perms if isinstance(p, str) and _PERMISSION_TOKEN.fullmatch(p)]
    return re.sub(r"\s+", " ", text).strip()[:300], perms


def permission_named(message: str, listed: Optional[list] = None, hint: Optional[str] = None) -> Optional[str]:
    """The WordPress capability or plugin permission a refusal is about: what WordPress' answer names (FluentCRM
    names them), else the op's hint."""
    found = list(listed or []) + _PERMISSION_TOKEN.findall(message or "")
    return ", ".join(dict.fromkeys(found)) if found else (hint or None)


def refusal_for(name: str, method: str, status: int, body: Any, permission_hint: Optional[str] = None,
                login: str = "") -> "tuple[str, str, str, Optional[str]]":
    """(code, message, wp_code, permission) for an answer >= 400."""
    body = body if isinstance(body, dict) else {}
    wp_code = str(body.get("code") or "")
    wp_msg, listed = wp_message(body)
    who = f"the Hermes user ({login})" if login else "the Hermes user"
    said = f" WordPress said: {wp_msg}" if wp_msg else ""
    if wp_code == "rest_no_route":
        return ("ability_not_found", f"The site does not offer {NAMESPACE} (WordPress 6.9 or newer has it), so it cannot "
                                     f"run {name}. Nothing changed.", wp_code, None)
    if wp_code in NOT_FOUND_CODES or status == 404:
        return ("ability_not_found", f"The site has no ability {name} (the plugin that registers it is not active, "
                                     f"or its own switch for abilities is off). Nothing changed.{said}", wp_code, None)
    if wp_code in PERMISSION_CODES or status in (401, 403):
        perm = permission_named(wp_msg, listed, permission_hint)
        if perm and "," in perm:
            needs = f" It needs one of the permissions {perm}; the site's admin grants them in wp-admin."
        elif perm:
            needs = f" It needs the permission {perm}, which the site's admin grants in wp-admin."
        else:
            needs = " The site's admin decides this in wp-admin."
        return ("ability_forbidden", f"WordPress refused {name} for {who}.{needs} Nothing changed.{said}", wp_code, perm)
    if wp_code in INPUT_CODES or status == 400:
        return ("ability_invalid_input", f"WordPress did not accept the input for {name}; check it against the op's "
                                         f"input schema (wp_pack action=describe). Nothing changed.{said}", wp_code, None)
    if wp_code == "rest_ability_invalid_method" or status == 405:
        return ("ability_method", f"WordPress refused the method {method} for {name}: the ability's annotations and "
                                  f"its run route disagree. Nothing changed.{said}", wp_code, None)
    return ("ability_failed", f"{name} failed on the site (HTTP {status}).{said}", wp_code, None)


def run(client: WordPressClient, ability: Ability, given: Optional[dict] = None,
        default_input: Optional[dict] = None, permission_hint: Optional[str] = None) -> AbilityRun:
    """One run with the method the annotations ask for. Never raises for WordPress' answer; a transport
    failure (the site did not answer) is an ``unreachable`` run."""
    path = run_path(ability.name)
    data = prepared_input(ability, given, default_input)
    method = ability.method
    try:
        if method in ("GET", "DELETE"):
            pairs = query_pairs(data) if data else []
            if pairs:
                path = f"{path}?{urlencode(pairs)}"
            resp = client.raw(method, path)
        else:
            resp = client.raw("POST", path, body={"input": data} if data is not None else {})
    except WPError as e:
        return AbilityRun(ability.name, method, sent=False, error=e.kind, message=e.message)
    body = _body(resp)
    if resp.status < 400:
        return AbilityRun(ability.name, method, sent=True, status=resp.status, result=body, ms=resp.ms)
    code, message, wp_code, perm = refusal_for(ability.name, method, resp.status, body, permission_hint, client.login)
    return AbilityRun(ability.name, method, sent=True, status=resp.status, error=code, message=message, wp_code=wp_code,
                      permission=perm, ms=resp.ms)


# --- only what a pack declares (D24 a) --------------------------------------------------------------

def declared(site: Optional[dict], registry: Optional[dict] = None) -> dict:
    """{ability name: (pack id, op name)} for every op of the plugin packs switched on for ``site``."""
    from . import packs as packs_mod
    reg = packs_mod.REGISTRY if registry is None else registry
    out: dict = {}
    for pid in sorted(reg):
        spec = reg[pid]
        if not packs_mod.is_on(site, pid):
            continue
        for op_name in sorted(spec.ops or {}):
            op = spec.ops[op_name]
            if op.ability:
                out.setdefault(op.ability, (pid, op_name))
    return out


def declared_anywhere(registry: Optional[dict] = None) -> dict:
    """{ability name: [pack ids]} over every pack in the registry, on or off."""
    from . import packs as packs_mod
    reg = packs_mod.REGISTRY if registry is None else registry
    out: dict = {}
    for pid in sorted(reg):
        for op in (reg[pid].ops or {}).values():
            if op.ability:
                out.setdefault(op.ability, []).append(pid)
    return out


def not_declared(name: str) -> AbilityRun:
    return AbilityRun(name, "", sent=False, error="ability_not_declared",
                      message=(f"WPBrücke runs only the abilities a pack declares, on sites where that pack is on; "
                               f"{name} is not one of them. Nothing was sent."))


def run_declared(rt: Any, site: dict, name: str, given: Optional[dict] = None) -> AbilityRun:
    """Run ``name`` on ``site`` only when a pack that is on declares it; any other name is refused before
    any request."""
    on = declared(site)
    if name not in on:
        return not_declared(name)
    from . import packs as packs_mod
    pid, op_name = on[name]
    return run_op(rt, site, packs_mod.REGISTRY[pid].ops[op_name], given)


def run_op(rt: Any, site: dict, op: Any, given: Optional[dict] = None) -> AbilityRun:
    """One declared op's ability, with the op's default input and permission hint. The method comes from
    the site's own listing (the facts cache; read again once when the name is missing, then refused as not
    found before any run request)."""
    name = str(op.ability)
    if name not in declared(site):
        return not_declared(name)
    listing = listing_from_facts(rt.site_facts(site)) or {}
    if name not in listing:
        listing = listing_from_facts(rt.site_facts(site, refresh=True)) or {}
    if name not in listing:
        code, message, _, _ = refusal_for(name, "", 404, {"code": "rest_ability_not_found"})
        return AbilityRun(name, "", sent=False, error=code, message=message)
    return run(rt.client(site), listing[name], given, op.default_input, op.permission)


def doctor_view(site: dict, facts: dict) -> dict:
    """What wp_doctor shows: per site the abilities a pack enables, and the rest, seen but not enabled."""
    info = (facts or {}).get("abilities")
    listing = listing_from_facts(facts)
    if listing is None:
        why = None
        if isinstance(info, dict) and info.get("error"):
            why = (info["error"] or {}).get("message") or "the listing failed"
        elif NAMESPACE not in ((facts or {}).get("namespaces") or []):
            why = "the site does not offer wp-abilities/v1 (WordPress 6.9 or newer has it)"
        else:
            why = "not read yet (they are read with a key)"
        return {"count": None, "enabled": [], "seen_not_enabled": [], "error": why}
    on = declared(site)
    anywhere = declared_anywhere()
    enabled = [{"ability": n, "pack": on[n][0], "op": on[n][1]} for n in sorted(on) if n in listing]
    rest = [n for n in sorted(listing) if n not in on]
    out = {"count": len(listing), "enabled": enabled, "seen_not_enabled": rest,
           "namespaces": summary(list(listing.values()))["namespaces"], "error": None}
    off = {n: anywhere[n] for n in rest if n in anywhere}
    if off:
        out["declared_by_packs_off"] = off
    missing = sorted(n for n in on if n not in listing)
    if missing:
        out["declared_not_on_site"] = missing
    return out


def doctor_line(view: dict) -> str:
    if view.get("count") is None:
        return f"abilities: {view.get('error')}"
    ns = ", ".join(f"{k} {v}" for k, v in (view.get("namespaces") or {}).items())
    enabled = ", ".join(e["ability"] for e in view["enabled"]) or "none"
    return (f"abilities: {view['count']} seen ({ns}); enabled through packs: {enabled}; "
            f"{len(view['seen_not_enabled'])} seen, not enabled")
