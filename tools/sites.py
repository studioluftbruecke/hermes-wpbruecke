"""wp_sites: the sites WPBrücke knows, their overview, and their site files (F3, F16)."""
from __future__ import annotations

from typing import Any

from .. import activity as activity_mod
from .. import builders as builders_mod
from .. import features as features_mod
from .. import packs as packs_mod
from ..runtime import NotFound, Runtime
from ..sites import GUARDED_FIELDS, SETTINGS_FIELDS, SYSTEM_FIELDS, SiteError, UnknownPackError

ACTIONS = ("list", "get", "add", "update", "history", "restore", "remove")

SCHEMA = {
    "name": "wp_sites",
    "description": (
        "The WordPress sites WPBrücke knows and their site files. action=list: every site with reachability, "
        "WordPress version, theme, access (drafts only, can publish, not set up), drafts and items waiting, "
        "updates (cached facts, refresh=true reads now). action=get: one site's file, facts and which features "
        "are available, plus the site's design tokens (colours, fonts, logo) read live from the theme with the "
        "site file's design overrides marked in overridden. action=add: a public site without a password (id slug, url; connect_ip for a site "
        "reachable only at a private address). A site with a password is connected by the person in a Site N "
        "group of the plugin settings (address, WordPress user, application password; the WPBrücke page, Connect "
        "a site, walks them through it); its url, login and key_slot come from there and cannot be changed here, "
        "and its access level is confirmed only on the WPBrücke page. action=update: change fields of the site "
        "file (changes: {field: value}; "
        "nested memory/proof/packs merge; preview=true shows the diff without saving); every save keeps a "
        "version with author and time. action=history: the versions. action=restore: back to a version. "
        "action=remove: delete a public site's file (WordPress is untouched; a connected site is removed on the "
        "WPBrücke page). Fields: url (public sites), connect_ip, group, label, packs.<id> (known packs: "
        f"{', '.join(packs_mod.ids())}; any other id is refused), design (overrides only: colors [{{slug, name, "
        "value}], fonts {body, headings}, logo), proof.widths, proof.extra_hosts, memory.tone, memory.languages, "
        "memory.do_not_touch, memory.notes, automations (each may say publish: true). Never put a password into a "
        "site file. Changing url, connect_ip, proof.extra_hosts, packs or automations asks the user first and is "
        "refused in unattended runs."),
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
    st = rt.standing(site)
    facts = rt.site_facts(site, refresh=refresh)
    counts = facts.get("counts") or {}
    ups = facts.get("updates") or {}
    return {
        "id": site["id"], "url": site["url"], "label": site.get("label") or facts.get("name") or site["id"],
        "label_group": site.get("group") or "", "reachable": bool(facts.get("reachable")), "rest_ms": facts.get("rest_ms"),
        "wp_version": facts.get("wp_version"), "theme": facts.get("theme_name") or facts.get("theme"),
        "access": features_mod.access_label(facts, site, st.effective), "drafts": counts.get("drafts"),
        "pending": counts.get("pending"), "comments_hold": counts.get("comments_hold"),
        "published": counts.get("published"), "last_post": facts.get("last_post"),
        "updates": ups.get("count") if ups else None, "connector": (facts.get("connector") or {}).get("version"),
        "group": site.get("key_slot") or None, "pairing": st.pairing, **st.row(),
        "noindex": (facts.get("seo_public") or {}).get("noindex"), "fetched_at": facts.get("fetched_at"),
        "builders": builders_mod.site_hints(facts), "packs": packs_mod.status(site, facts),
    }


def refusal(rt: Runtime, args: Any) -> "dict | None":
    """A call this tool refuses outright, before any card: the error dict, or None."""
    if not isinstance(args, dict):
        return None
    action = args.get("action")
    if action == "add":
        fields = args.get("fields") or {}
        if isinstance(fields, dict):
            bad = _unknown_packs(fields)
            if bad:
                return bad
            if set(fields) & SYSTEM_FIELDS:
                return _refused(f"{', '.join(sorted(set(fields) & SYSTEM_FIELDS))} is written by WPBrücke itself "
                                "(access only by Confirm on the WPBrücke page)")
            if fields.get("key_slot") or fields.get("login"):
                return _refused("a site with a password is connected in a Site N group of the plugin settings "
                                "(the WPBrücke page, Connect a site); add a public site here with id and url only")
        return None
    if action == "update":
        changes = args.get("changes") or {}
        if not isinstance(changes, dict):
            return None
        bad = _unknown_packs(changes)
        if bad:
            return bad
        if set(changes) & SYSTEM_FIELDS:
            return _refused(f"{', '.join(sorted(set(changes) & SYSTEM_FIELDS))} cannot be changed by a tool: the "
                            "access level is confirmed only on the WPBrücke page")
        touched = set(changes) & SETTINGS_FIELDS
        if touched:
            try:
                site = rt.site(str(args.get("site") or ""))
            except NotFound:
                return None
            if site.get("key_slot") or "key_slot" in touched:
                return _refused(f"{', '.join(sorted(touched))} of a connected site come from its Site N group in "
                                "the plugin settings; change them there")
    if action == "remove":
        try:
            site = rt.site(str(args.get("site") or ""))
        except NotFound:
            return None
        if site.get("key_slot"):
            return _refused(f"site {site['id']} is connected in Site {site['key_slot']}; remove it on the WPBrücke "
                            "page (it empties the group and can revoke the password in WordPress)")
    return None


def _unknown_packs(fields: dict) -> "dict | None":
    packs = fields.get("packs")
    if packs is None:
        return None
    if not isinstance(packs, dict) or packs_mod.unknown(packs):
        names = ", ".join(packs_mod.unknown(packs)) if isinstance(packs, dict) else str(packs)[:40]
        return {"ok": False, "error": "unknown_pack", "message": (
            f"WPBrücke has no pack {names}; known packs: {', '.join(packs_mod.ids())}. A page built with a builder "
            "that has no pack stays read-only for its body. Nothing was saved.")}
    return None


def _refused(message: str) -> dict:
    return {"ok": False, "error": "refused", "message": message + ". Nothing was saved."}


def handle(rt: Runtime, args: dict, author: str = "hermes") -> dict:
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return {"ok": False, "error": "bad_args", "message": f"action must be one of {', '.join(ACTIONS)}"}
    refresh = bool(args.get("refresh", False))
    refused = refusal(rt, args)
    if refused:
        return refused
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
                    "next": "run wp_doctor for this site to read its public facts"}
        site_id = str(args.get("site") or "")
        if not site_id:
            return {"ok": False, "error": "bad_args", "message": f"{action} needs site"}
        if action == "get":
            site = rt.site(site_id)
            facts = rt.site_facts(site, refresh=refresh)
            ev = rt.features(site, facts)
            return {"ok": True, "site": site, "overview": overview_row(rt, site), "rights": features_mod.rights_words(facts),
                    "available": [f["feature"] for f in ev["available"]], "not_available": ev["unavailable"],
                    "design": packs_mod.design_tokens(rt, site, refresh=refresh),
                    "versions": rt.sites.versions(site_id)[-5:]}
        if action == "update":
            changes = args.get("changes") or {}
            if not isinstance(changes, dict) or not changes:
                return {"ok": False, "error": "bad_args", "message": "update needs changes: {field: value}"}
            rt.site(site_id)
            res = rt.sites.update(site_id, changes, author, preview=bool(args.get("preview")))
            res["guarded"] = _guarded_keys(changes)
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
    except UnknownPackError as e:
        return {"ok": False, "error": "unknown_pack", "message": str(e)}
    except SiteError as e:
        return {"ok": False, "error": "invalid", "message": str(e)}
    return {"ok": False, "error": "bad_args", "message": "unhandled action"}


def _guarded_keys(fields: dict) -> list:
    keys = set(fields) & GUARDED_FIELDS
    proof = fields.get("proof")
    if isinstance(proof, dict) and "extra_hosts" in proof:
        keys.add("proof.extra_hosts")
    return sorted(keys)


def touches_guarded(args: Any) -> list:
    """Which guarded fields an agent call would change (the M4 gate asks the user for these)."""
    if not isinstance(args, dict):
        return []
    action = args.get("action")
    if action == "add":
        f = args.get("fields") or {}
        return _guarded_keys(f) if isinstance(f, dict) else []
    if action == "update" and not args.get("preview"):
        c = args.get("changes") or {}
        return _guarded_keys(c) if isinstance(c, dict) else []
    if action in ("restore", "remove"):
        return ["(whole site file)"]
    return []


WRITES = ("add", "update", "restore", "remove")


@activity_mod.quiet
def log_change(rt: Runtime, args: dict, out: dict, who: str = "agent") -> None:
    """One write-log line for a call that writes (or was refused writing) a site file; a preview, a read or
    a call naming no site writes none."""
    action = str(args.get("action") or "")
    if action not in WRITES or (action == "update" and args.get("preview")):
        return
    fields = args.get("fields") if isinstance(args.get("fields"), dict) else {}
    site = str(args.get("site") or fields.get("id") or ((out.get("site") or {}).get("id") if isinstance(out.get("site"), dict) else out.get("site")) or "")
    ok = bool(out.get("ok"))
    changes = args.get("changes") if isinstance(args.get("changes"), dict) else {}
    if not ok:
        summary = f"{action} the site file refused: " + str(out.get("message") or out.get("error") or "").split(". ")[0]
    elif action == "add":
        summary = f"added the site file {site} (" + ", ".join(sorted(fields)) + ")"
    elif action == "update":
        summary = (f"changed the site file ({', '.join(sorted(changes))}), version {out.get('version')}"
                   if out.get("changed") else f"site file unchanged ({', '.join(sorted(changes))})")
    elif action == "restore":
        summary = f"restored the site file to version {args.get('version')}"
    else:
        summary = "removed the site file; nothing changed on WordPress"
    activity_mod.for_runtime(rt, site=site, tool="wp_sites", action=f"{action}_site", wp_type="site_file", label=site,
                         lane="chat", who=who, result="ok" if ok else "refused", code=out.get("error"),
                         summary=summary)
