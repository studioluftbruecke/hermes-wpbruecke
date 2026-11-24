"""wp_sites: the sites WPBrücke knows, their overview, and their site files (F3, F16)."""
from __future__ import annotations

from typing import Any

from .. import features as features_mod
from ..runtime import NotFound, Runtime
from ..sites import GUARDED_FIELDS, SiteError

ACTIONS = ("list", "get", "add", "update", "history", "restore", "remove")

SCHEMA = {
    "name": "wp_sites",
    "description": (
        "The WordPress sites WPBrücke knows and their site files. action=list: every site with reachability, "
        "WordPress version, theme, access (drafts only, can publish, not set up), drafts and items waiting, "
        "updates (cached facts, refresh=true reads now). action=get: one site's file, facts and which features "
        "are available. action=add: a new site (id slug, url; login and key_slot 1-12 when the user stored an "
        "application password in the plugin settings field WPBRUECKE_KEY<n>; connect_ip for a site reachable "
        "only at a private address). action=update: change fields of the site file (changes: {field: value}; "
        "nested memory/proof/packs merge; preview=true shows the diff without saving); every save keeps a "
        "version with author and time. action=history: the versions. action=restore: back to a version. "
        "action=remove: delete the site file (WordPress is untouched). Fields: url, login, key_slot, "
        "connect_ip, group, label, publishing (off|confirm|schedule), never_publish_never_send (the hard rule, "
        "default true), agent_writes (auto|always), packs.generatepress, proof.widths, proof.extra_hosts, "
        "memory.tone, memory.languages, memory.do_not_touch, memory.notes, automations. Never put a password "
        "into a site file. Changing url, login, key_slot, connect_ip, publishing, the hard rule, agent_writes "
        "or automations asks the user first."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "site": {"type": "string", "description": "site id (get, update, history, restore, remove)"},
        "fields": {"type": "object", "description": "add: the new site's fields (id, url, login, key_slot, ...)"},
        "changes": {"type": "object", "description": "update: the fields to change"},
        "preview": {"type": "boolean", "description": "update: return the diff without saving"},
        "version": {"type": "integer", "description": "restore: the version number from history"},
        "refresh": {"type": "boolean", "description": "list/get: read the sites now instead of the 5-minute cache"},
    }, "required": ["action"]},
}


def overview_row(rt: Runtime, site: dict, refresh: bool = False) -> dict:
    facts = rt.site_facts(site, refresh=refresh)
    counts = facts.get("counts") or {}
    ups = facts.get("updates") or {}
    return {
        "id": site["id"], "url": site["url"], "label": site.get("label") or facts.get("name") or site["id"],
        "group": site.get("group") or "", "reachable": bool(facts.get("reachable")), "rest_ms": facts.get("rest_ms"),
        "wp_version": facts.get("wp_version"), "theme": facts.get("theme_name") or facts.get("theme"),
        "access": features_mod.access_label(facts, site), "drafts": counts.get("drafts"),
        "pending": counts.get("pending"), "comments_hold": counts.get("comments_hold"),
        "published": counts.get("published"), "last_post": facts.get("last_post"),
        "updates": ups.get("count") if ups else None, "connector": (facts.get("connector") or {}).get("version"),
        "hard_rule": bool(site.get("never_publish_never_send", True)), "publishing": site.get("publishing"),
        "noindex": (facts.get("seo_public") or {}).get("noindex"), "fetched_at": facts.get("fetched_at"),
    }


def handle(rt: Runtime, args: dict, author: str = "hermes") -> dict:
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return {"ok": False, "error": "bad_args", "message": f"action must be one of {', '.join(ACTIONS)}"}
    refresh = bool(args.get("refresh", False))
    try:
        if action == "list":
            sites = [s for s in rt.sites.all() if "error" not in s]
            rows = [overview_row(rt, s, refresh) for s in sites]
            return {"ok": True, "mode": "one" if len(rows) == 1 else "many", "count": len(rows), "sites": rows,
                    "broken_site_files": [s for s in rt.sites.all() if "error" in s]}
        if action == "add":
            fields = args.get("fields") or {}
            if not isinstance(fields, dict):
                return {"ok": False, "error": "bad_args", "message": "fields must be an object"}
            res = rt.sites.create(fields, author)
            return {"ok": True, "site": res["site"], "version": res["version"], "diff": res["diff"],
                    "next": "run wp_doctor for this site to see who the key is and what it may do"}
        site_id = str(args.get("site") or "")
        if not site_id:
            return {"ok": False, "error": "bad_args", "message": f"{action} needs site"}
        if action == "get":
            site = rt.site(site_id)
            facts = rt.site_facts(site, refresh=refresh)
            ev = features_mod.evaluate(site, facts)
            return {"ok": True, "site": site, "overview": overview_row(rt, site), "rights": features_mod.rights_words(facts),
                    "available": [f["feature"] for f in ev["available"]], "not_available": ev["unavailable"],
                    "versions": rt.sites.versions(site_id)[-5:]}
        if action == "update":
            changes = args.get("changes") or {}
            if not isinstance(changes, dict) or not changes:
                return {"ok": False, "error": "bad_args", "message": "update needs changes: {field: value}"}
            rt.site(site_id)
            res = rt.sites.update(site_id, changes, author, preview=bool(args.get("preview")))
            res["guarded"] = sorted(set(changes) & GUARDED_FIELDS)
            return {"ok": True, **res}
        if action == "history":
            rt.site(site_id)
            return {"ok": True, "site": site_id, "versions": rt.sites.versions(site_id)}
        if action == "restore":
            if args.get("version") is None:
                return {"ok": False, "error": "bad_args", "message": "restore needs version"}
            rt.site(site_id)
            return {"ok": True, **rt.sites.restore(site_id, int(args["version"]), author)}
        if action == "remove":
            rt.site(site_id)
            return {"ok": True, "removed": rt.sites.delete(site_id), "site": site_id,
                    "note": "only the site file is gone; nothing changed on WordPress; versions stay in history"}
    except NotFound as e:
        return {"ok": False, "error": "not_found", "message": str(e)}
    except SiteError as e:
        return {"ok": False, "error": "invalid", "message": str(e)}
    return {"ok": False, "error": "bad_args", "message": "unhandled action"}


def touches_guarded(args: Any) -> list:
    """Which guarded fields an agent call would change (the M4 gate asks the user for these)."""
    if not isinstance(args, dict):
        return []
    action = args.get("action")
    if action == "add":
        f = args.get("fields") or {}
        return sorted(k for k in GUARDED_FIELDS if k in f) if isinstance(f, dict) else []
    if action == "update" and not args.get("preview"):
        c = args.get("changes") or {}
        return sorted(set(c) & GUARDED_FIELDS) if isinstance(c, dict) else []
    if action in ("restore", "remove"):
        return ["(whole site file)"]
    return []
