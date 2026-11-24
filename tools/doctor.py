"""wp_doctor: per site, who the key is, what it may do, what that lights and what stays dark (F1)."""
from __future__ import annotations

from typing import Optional

from .. import features as features_mod
from .. import settings as settings_mod
from ..runtime import NotFound, Runtime

SCHEMA = {
    "name": "wp_doctor",
    "description": (
        "Check WPBrücke's setup against each live WordPress site: whether it answers and how fast, its "
        "WordPress version and theme, who the site's application password belongs to (user, roles, key name), "
        "what that user may do in plain words (can draft, cannot publish, ...), whether the WPBrücke connector "
        "is installed, and which plugin features are available or not available with the reason and the fix. "
        "Read-only. Call it first when a WordPress feature fails, when the user asks what the plugin can do, or "
        "after adding a site. site: one site id; empty checks every site. refresh=false uses the 5-minute cache."),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string", "description": "site id (wp_sites action=list shows them); empty = all"},
        "refresh": {"type": "boolean", "description": "read the sites now (default true)"},
    }, "required": []},
}


def site_report(rt: Runtime, site: dict, refresh: bool = True) -> dict:
    key_env = settings_mod.slot_env(site.get("key_slot"))
    key_set = rt.key_present(site) if key_env else False
    facts = rt.site_facts(site, refresh=refresh)
    ev = features_mod.evaluate(site, facts)
    proposal = sorted(set((facts.get("seo_public") or {}).get("third_party_hosts") or [])
                      - set((site.get("proof") or {}).get("extra_hosts") or []))
    lines = []
    if not facts.get("reachable"):
        lines.append(f"{site['url']} did not answer" + (f" (connect_ip {site['connect_ip']})" if site.get("connect_ip") else ""))
    else:
        lines.append(f"{site['url']}: {facts_mod_summary(facts)}")
    if not key_env:
        lines.append("no key slot: public facts only")
    elif not key_set:
        lines.append(f"key slot {site['key_slot']} is empty: store the application password in Hermes' plugin "
                     f"settings as {key_env} (Capabilities, Plugins, wpbruecke, Settings)")
    elif facts.get("user"):
        u = facts["user"]
        lines.append(f"key {key_env} belongs to {u.get('username')} ({', '.join(u.get('roles') or []) or 'no role'})"
                     + (f", application password \"{facts.get('key_name')}\"" if facts.get("key_name") else ""))
    else:
        auth_err = next((e for e in facts.get("errors") or [] if e.get("read") == "users/me"), None)
        lines.append(f"key {key_env} did not authenticate as {site.get('login')}: "
                     f"{(auth_err or {}).get('message', 'no answer')}")
    conn = facts.get("connector")
    lines.append(f"connector: {conn.get('version')}" if conn else "connector: not installed (optional)")
    return {
        "site": site["id"], "url": site["url"], "reachable": bool(facts.get("reachable")),
        "rest_ms": facts.get("rest_ms"), "wp_version": facts.get("wp_version"), "theme": facts.get("theme"),
        "key_slot": site.get("key_slot") or 0, "key_env": key_env, "key_set": key_set,
        "user": facts.get("user"), "key_name": facts.get("key_name"),
        "rights": features_mod.rights_words(facts), "access": features_mod.access_label(facts, site),
        "warnings": features_mod.key_warnings(facts), "connector": conn,
        "hard_rule": bool(site.get("never_publish_never_send", True)), "publishing": site.get("publishing"),
        "available": [f["feature"] for f in ev["available"]], "not_available": ev["unavailable"],
        "proof_extra_hosts_proposal": proposal, "errors": facts.get("errors") or [], "lines": lines,
        "facts_cache": facts.get("cache"),
    }


def facts_mod_summary(facts: dict) -> str:
    from ..facts import summary_line
    return summary_line(facts)


def run(rt: Runtime, site_id: Optional[str] = None, refresh: bool = True) -> dict:
    out = {"ok": True, "settings_warnings": list(rt.settings.warnings), "sites": []}
    if site_id:
        try:
            sites = [rt.site(site_id)]
        except NotFound as e:
            return {"ok": False, "error": "not_found", "message": str(e)}
    else:
        sites = [s for s in rt.sites.all() if "error" not in s]
        out["broken_site_files"] = [s for s in rt.sites.all() if "error" in s]
    if not sites:
        out["next_steps"] = ("No site yet. Add one with wp_sites action=add (id, url; login and key_slot when you "
                             "have an application password), or on the WPBrücke page.")
        return out
    for site in sites:
        out["sites"].append(site_report(rt, site, refresh))
    out["mode"] = "one" if len(rt.sites.ids()) == 1 else "many"
    return out


def format_text(report: dict) -> str:
    if not report.get("ok"):
        return f"error: {report.get('message')}"
    lines = []
    for w in report.get("settings_warnings") or []:
        lines.append(f"! {w}")
    if report.get("next_steps"):
        lines.append(report["next_steps"])
    for s in report.get("sites") or []:
        lines.append(f"== {s['site']} ({s['access']})")
        lines.extend(f"   {l}" for l in s["lines"])
        if s["rights"]:
            lines.append("   rights: " + ", ".join(s["rights"]))
        for w in s["warnings"]:
            lines.append(f"   ! {w}")
        lines.append("   available: " + ", ".join(s["available"]))
        for u in s["not_available"]:
            lines.append(f"   dark: {u['feature']}: {u['because']}")
        if s["proof_extra_hosts_proposal"]:
            lines.append("   proof may need extra_hosts: " + ", ".join(s["proof_extra_hosts_proposal"]))
    return "\n".join(lines)
