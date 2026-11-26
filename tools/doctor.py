"""wp_doctor: per site, who the key is, what it may do, what that lights and what stays dark (F1)."""
from __future__ import annotations

from typing import Optional

from .. import abilities as abilities_mod
from .. import builders as builders_mod
from .. import features as features_mod
from .. import packs as packs_mod
from ..packs.fluentcrm import pack as fluentcrm_pack
from .. import settings as settings_mod
from ..runtime import NotFound, Runtime
from ..store import StoreError

SCHEMA = {
    "name": "wp_doctor",
    "description": (
        "Two jobs. action=log reads WPBrücke's write log: call it to answer what happened with a post or page, "
        "did Hermes change it, who did what when. action=check (default) checks WPBrücke's setup against each "
        "live WordPress site: whether it answers and how fast, its "
        "WordPress version and theme, who the site's application password belongs to (user, roles, key name), "
        "what that user may do in plain words (can draft, cannot publish, ...), whether the WPBrücke connector "
        "is installed, which plugin features are available or not available with the reason and the fix, and "
        "per pack (packs: {id: {label, detected, on, needs}}) whether the site has what it edits, whether the site "
        "file switches it on, and what it still needs; builders lists the page builders the site runs; abilities lists "
        "the plugin abilities the site offers (WordPress 6.9+): enabled (declared by a pack that is on, runnable "
        "through wp_pack) and seen_not_enabled (WPBrücke never runs those); fluentcrm shows, on a site with FluentCRM, what "
        "the site's admin lets Hermes do there (view, draft, test_email, send; saved false: the FluentCRM pack runs "
        "nothing until the admin saves the screen named in the line). "
        "Read-only. Call it first when a WordPress feature fails, when the user asks what the plugin can do, or "
        "after adding a site. site: one site id; empty checks every site. refresh=false uses the 5-minute cache. "
        "action=log reads WPBrücke's write log instead: one line per write it did or refused (drafts, staged "
        "changes, publishes, SEO fixes, brand, site-file changes, the person's clicks on the page), newest first, "
        "each with time, tool, action, the post's id and its title at write time (label), result (ok, refused, "
        "error) and refusal code. Call it before answering \"what happened with X\" or \"did you change X\"; "
        "filters: site, wp_id, label (part of the title, any case), q (any word in label, summary, tool, action, "
        "code), tool, log_action, result, since/until (2026-11-25 or an ISO time), limit (default 50)."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["check", "log"], "description": "check (default) or log"},
        "site": {"type": "string", "description": "site id (wp_sites action=list shows them); empty = all"},
        "refresh": {"type": "boolean", "description": "read the sites now (default true)"},
        "wp_id": {"type": "integer", "description": "log: the post, page or media id"},
        "label": {"type": "string", "description": "log: part of the post's title or slug as it was at the write"},
        "q": {"type": "string", "description": "log: a word found in label, summary, tool, action or code"},
        "tool": {"type": "string", "description": "log: wp_draft, wp_seo, wp_build, wp_sites, wp_desk, page, check"},
        "log_action": {"type": "string", "description": "log: the logged action (create, update, stage, publish, "
                                                         "schedule, apply, set_seo, upload_media, set_brand, ...)"},
        "result": {"type": "string", "enum": ["ok", "refused", "error"], "description": "log: only these results"},
        "since": {"type": "string", "description": "log: from this date or time (UTC when no zone)"},
        "until": {"type": "string", "description": "log: up to this date (inclusive) or time"},
        "limit": {"type": "integer", "description": "log: how many lines (default 50, at most 500)"},
    }, "required": []},
}


def log(rt: Runtime, args: dict) -> dict:
    """action=log: the LogQuery over the write log, newest first (A35)."""
    from .. import activity, redact
    query = {k: args.get(k) for k in activity.QUERY_KEYS if k != "action"}
    query["action"] = args.get("log_action")
    if query.get("site"):
        try:
            rt.site(str(query["site"]))
        except NotFound as e:
            return {"ok": False, "error": "not_found", "message": str(e)}
    try:
        found = activity.ActivityLog(rt.store, redact.known_secrets(rt.get_secret)).query(query)
    except ValueError as e:
        return {"ok": False, "error": "bad_args", "message": str(e)}
    except StoreError as e:
        return {"ok": False, "error": "bad_args", "message": str(e)}
    out = {"ok": True, "lines": found["lines"], "count": len(found["lines"]), "matched": found["matched"],
           "query": {k: v for k, v in query.items() if v not in (None, "")}}
    if not found["lines"]:
        out["note"] = ("The write log has no line for this. WPBrücke logs every write it does or refuses since "
                       "v0.4; say that nothing is logged rather than guessing.")
    return out


def site_report(rt: Runtime, site: dict, refresh: bool = True) -> dict:
    key_env = settings_mod.slot_env(site.get("key_slot"))
    key_set = bool(rt.group_password(site["key_slot"])) if key_env else False
    st = rt.standing(site, refresh=refresh)
    pairing = st.pairing
    facts = rt.site_facts(site, refresh=refresh)
    ev = rt.features(site, facts)
    proposal = sorted(set((facts.get("seo_public") or {}).get("third_party_hosts") or [])
                      - set((site.get("proof") or {}).get("extra_hosts") or []))
    lines = []
    if not facts.get("reachable"):
        lines.append(f"{site['url']} did not answer" + (f" (connect_ip {site['connect_ip']})" if site.get("connect_ip") else ""))
    else:
        lines.append(f"{site['url']}: {facts_mod_summary(facts)}")
    health = facts.get("health") or {"verdict": "unknown", "signals": []}
    lines.append(health_line(health))
    if not key_env:
        lines.append("public site: public facts only")
    elif not key_set:
        lines.append(f"Site {site['key_slot']} has no application password: paste it into Site {site['key_slot']} · "
                     "application password in the plugin settings (the WPBrücke page, Connect a site)")
    elif pairing != "paired":
        lines.append(f"Site {site['key_slot']} is not connected yet ({pairing}): check it on the WPBrücke page; "
                     "until then no password is sent")
    elif st.effective == "unconfirmed":
        lines.append("access level not confirmed yet: confirm it on the WPBrücke page; until then Hermes reads only "
                     "what the public sees")
    elif st.effective == "stopped":
        lines.append(f"stopped: {st.stop_reason}; nothing keyed runs until the person confirms again on the "
                     "WPBrücke page" if st.confirmable else
                     f"stopped: {st.stop_reason}; nothing keyed runs until then. {st.restart()}")
    elif facts.get("user"):
        u = facts["user"]
        lines.append(f"key {key_env} belongs to {u.get('username')} ({', '.join(u.get('roles') or []) or 'no role'})"
                     + (f", application password \"{facts.get('key_name')}\"" if facts.get("key_name") else ""))
    else:
        auth_err = next((e for e in facts.get("errors") or [] if e.get("read") == "users/me"), None)
        lines.append(f"key {key_env} did not authenticate as {site.get('login')}: "
                     f"{(auth_err or {}).get('message', 'no answer')}")
    conn = facts.get("connector")
    lines.append(f"connector: {conn.get('version')}" if conn else
                 "connector: installed (its version shows once the password is sent)" if facts.get("connector_ns") else
                 "connector: not installed (optional)")
    packs = packs_mod.status(site, facts)
    for pid, st_ in packs.items():
        lines.append(f"pack {st_['label']}: {'detected' if st_['detected'] else 'not detected'}, "
                     f"{'on' if st_['on'] else 'off'}" + (f"; {st_['needs']}" if st_["needs"] else ""))
    abilities = abilities_mod.doctor_view(site, facts)
    lines.append(abilities_mod.doctor_line(abilities))
    fluentcrm = fluentcrm_pack.doctor_view(rt, site, facts, refresh) if st.effective not in ("unconfirmed", "stopped") \
        else None
    if fluentcrm is not None:
        lines.append(fluentcrm_pack.doctor_line(fluentcrm))
    hints = builders_mod.site_hints(facts)
    if hints:
        lines.append("page builders on the site: " + ", ".join(builders_mod.LABELS[h] for h in hints)
                     + "; pages made with one have a read-only body unless its pack is on")
    return {
        "site": site["id"], "url": site["url"], "reachable": bool(facts.get("reachable")),
        "rest_ms": facts.get("rest_ms"), "wp_version": facts.get("wp_version"), "theme": facts.get("theme"),
        "key_slot": site.get("key_slot") or 0, "key_env": key_env, "key_set": key_set,
        "user": facts.get("user"), "key_name": facts.get("key_name"),
        "rights": features_mod.rights_words(facts), "access": features_mod.access_label(facts, site, st.effective),
        "warnings": features_mod.key_warnings(facts), "connector": conn,
        "group": site.get("key_slot") or None, "pairing": pairing, **st.row(),
        "available": [f["feature"] for f in ev["available"]], "not_available": ev["unavailable"],
        "proof_extra_hosts_proposal": proposal, "errors": facts.get("errors") or [], "lines": lines,
        "packs": packs, "builders": hints, "abilities": abilities, "fluentcrm": fluentcrm,
        "facts_cache": facts.get("cache"), "health": health,
    }


def health_line(health: dict) -> str:
    from .. import health as health_mod
    verdict = health.get("verdict")
    status = health_mod.signal(health, "home_status")
    if verdict == "unknown":
        return "health: unknown (the home page did not answer)"
    if verdict == "updating":
        return f"health: updating (the home page answers HTTP {status} in maintenance mode, Retry-After " \
               f"{health_mod.signal(health, 'retry_after')}); the watcher reports it when the next tick still sees it"
    if verdict == "broken":
        return f"health: broken ({health_mod.reason(health)})"
    return f"health: ok (the home page answers HTTP {status})"


def facts_mod_summary(facts: dict) -> str:
    from ..facts import summary_line
    return summary_line(facts)


def run(rt: Runtime, site_id: Optional[str] = None, refresh: bool = True) -> dict:
    out = {"ok": True, "settings_warnings": list(rt.settings.warnings), "sites": []}
    if rt.sync_problems:
        out["sync_problems"] = rt.sync_problems
    if site_id:
        try:
            sites = [rt.site(site_id)]
        except NotFound as e:
            return {"ok": False, "error": "not_found", "message": str(e)}
    else:
        sites = [s for s in rt.sites.all() if "error" not in s]
        out["broken_site_files"] = [s for s in rt.sites.all() if "error" in s]
    if not sites:
        out["next_steps"] = ("No site yet. The person connects one in a Site N group of the plugin settings (the "
                             "WPBrücke page, Connect a site, walks them through it); a public site without a "
                             "password can be added with wp_sites action=add (id, url).")
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
