"""wp_build: the GeneratePress pack's build run, from brief to client handoff (F18).

Only for sites whose file switches the pack on (``packs.generatepress: true``). Pages are written as
drafts through ``wp_draft`` (same gate, same desk), as GenerateBlocks when the site has the
GenerateBlocks plugin and core blocks otherwise (Q1/P19 proved both valid in the editor). Brand colours
are staged through the connector and applied only by the person's click on its review screen. Gates
are the person's (the page's buttons); this tool can only advance Hermes' own steps.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..client import WPError
from ..desk import Desk, DeskError
from ..runtime import NotFound, Runtime
from ..store import StoreError

ACTIONS = ("start", "get", "list", "set_brand", "set_sitemap", "build_page", "advance", "handoff")
WRITES = ("set_brand", "build_page")

SCHEMA = {
    "name": "wp_build",
    "description": (
        "GeneratePress pack only (the site file needs packs.generatepress: true): a build run from brief to "
        "client handoff. Steps: brief, brand, sitemap, pages, header_footer, proof, handoff; each names who acts "
        "(Hermes, you, client) and every gate is the person's click on the WPBrücke page. action=start (site, "
        "brief {name, audience, must_have[], tone}); get/list (run, site); set_brand (run, colors [{slug, name, "
        "color #hex}], fonts {body, headings}): staged through the connector, applied only by the person; "
        "set_sitemap (run, pages [{slug, title}]); build_page (run, slug, title, sections [{type: hero|features|"
        "text|cta|contact, heading, body, items[], button {label, href}}]): writes the page as a draft "
        "(GenerateBlocks when the site has it, else core blocks) onto the desk; advance (run, step) for Hermes' "
        "own steps; handoff (run): the client's guide. Writes follow wp_draft's approval rules."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "site": {"type": "string"}, "run": {"type": "string"}, "brief": {"type": "object"},
        "colors": {"type": "array", "items": {"type": "object"}}, "fonts": {"type": "object"},
        "pages": {"type": "array", "items": {"type": "object"}},
        "slug": {"type": "string"}, "title": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object"}}, "step": {"type": "string"},
    }, "required": ["action"]},
}


def _load(rt: Runtime, run_id: str) -> dict:
    try:
        run = rt.store.read_json(rt.store.path("build", f"{run_id}.json"), None)
    except StoreError as e:
        raise ValueError(str(e)) from None
    if not run:
        raise ValueError(f"no build run {run_id}")
    return run


def _save(rt: Runtime, run: dict) -> dict:
    with rt.store.lock("build"):
        rt.store.write_json(rt.store.path("build", f"{run['id']}.json"), run)
    return run


def _pack_site(rt: Runtime, site_id: str) -> dict:
    site = rt.site(site_id)
    if not (site.get("packs") or {}).get("generatepress"):
        raise ValueError(f"the GeneratePress pack is off for {site_id}; the person switches it on in the site's "
                         "settings (packs.generatepress: true)")
    return site


def _gb_active(facts: dict) -> bool:
    if "generateblocks/v1" in (facts.get("namespaces") or []):
        return True
    return any(str(p.get("plugin", "")).startswith("generateblocks/") and p.get("status") == "active"
               for p in facts.get("plugins") or [])


def draft_args(rt: Runtime, args: dict) -> dict:
    """The wp_draft call a build_page becomes (the gate approves exactly this)."""
    from ..gp import blocks
    from ..gp.build import BuildError
    run = _load(rt, str(args.get("run") or ""))
    site = _pack_site(rt, run["site"])
    pages = next(s for s in run["steps"] if s["key"] == "pages")
    if pages["state"] != "running":
        raise BuildError("state", f"pages are written while the pages step is running (it is {pages['state']}; "
                         "the earlier gates are the person's clicks on the WPBrücke page)")
    sections = args.get("sections") or []
    if not isinstance(sections, list) or not sections:
        raise ValueError("build_page needs sections")
    title = str(args.get("title") or args.get("slug") or "Page")
    markup = blocks.page_from_section_plan(sections, run.get("brand") or {}, run.get("mode") or "core",
                                           seed=f"{run['id']}-{args.get('slug') or title}")
    return {"site": site["id"], "action": "create", "type": "page", "title": title,
            "slug": str(args.get("slug") or ""), "content": markup, "note": f"build run {run['id']}: page {title}"}


def handle(rt: Runtime, args: dict, run_draft: Optional[Callable[[dict], dict]] = None,
           approved_brand: bool = False) -> dict:
    from ..gp import build as gp_build
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return {"ok": False, "error": "bad_args", "message": f"action must be one of {', '.join(ACTIONS)}"}
    try:
        if action == "list":
            runs = [rt.store.read_json(p, None) for p in rt.store.dir("build").glob("*.json")]
            runs = [r for r in runs if isinstance(r, dict) and (not args.get("site") or r.get("site") == args["site"])]
            return {"ok": True, "runs": [{k: r.get(k) for k in ("id", "site", "mode", "created_at", "updated_at")} for r in runs]}
        if action == "start":
            site = _pack_site(rt, str(args.get("site") or ""))
            mode = gp_build.mode_for(_gb_active(rt.site_facts(site)))
            run = gp_build.new_run(site["id"], args.get("brief") or {}, mode=mode)
            return {"ok": True, "run": _save(rt, run), "next": "the brief gate is the person's click on the WPBrücke page"}
        run = _load(rt, str(args.get("run") or ""))
        if action == "get":
            return {"ok": True, "run": run, "current": gp_build.current(run)}
        if action == "set_sitemap":
            pages = [{"slug": str(p.get("slug") or ""), "title": str(p.get("title") or "")} for p in args.get("pages") or []
                     if isinstance(p, dict)]
            if not pages:
                return {"ok": False, "error": "bad_args", "message": "set_sitemap needs pages [{slug, title}]"}
            run["sitemap"] = pages
            return {"ok": True, "run": _save(rt, run)}
        if action == "advance":
            run = gp_build.advance(run, str(args.get("step") or ""), by="hermes")
            return {"ok": True, "run": _save(rt, run)}
        if action == "handoff":
            items = [i for i in (Desk(rt.store).get(x) for x in run.get("pages") or []) if i]
            site = rt.site(run["site"])
            return {"ok": True, "guide": gp_build.handoff_guide(run, items, site_url=site["url"])}
        if action == "set_brand":
            colors = [c for c in args.get("colors") or [] if isinstance(c, dict)]
            run["brand"] = {"colors": colors, "fonts": args.get("fonts") or run.get("brand", {}).get("fonts") or {}}
            site = rt.site(run["site"])
            refusal = rt.require(site, "build_write")
            if refusal is not None:
                return refusal.to_dict()
            facts = rt.site_facts(site)
            note = "brand saved in the build run"
            if facts.get("connector") or facts.get("connector_ns"):
                if not approved_brand:
                    return {"ok": False, "error": "not_approved", "message": "Staging the brand needs the person's "
                            "approval in Hermes (no card was answered for this exact call). Nothing was sent."}
                staged = rt.client(site).post("/wp-json/wpbruecke/v1/gp/brand", {"colors": colors, "fonts": run["brand"]["fonts"]})
                note = f"brand staged on the site; the person applies it on the review screen: {staged.get('review_url')}"
            else:
                note += "; without the connector the person sets the colours in Customizer (Colors)"
            _save(rt, run)
            return {"ok": True, "run": run, "note": note}
        if action == "build_page":
            if run_draft is None:
                return {"ok": False, "error": "internal", "message": "build_page needs the draft lane"}
            out = run_draft(draft_args(rt, args))
            if out.get("ok") and out.get("desk_item"):
                Desk(rt.store).update(out["desk_item"], kind="build_page")
                run = gp_build.add_page(_load(rt, run["id"]), out["desk_item"])
                _save(rt, run)
                out["run"] = run["id"]
            return out
    except (NotFound, ValueError, DeskError) as e:
        code = getattr(e, "code", None) or ("not_found" if isinstance(e, NotFound) else "refused")
        return {"ok": False, "error": code, "message": str(e)}
    except WPError as e:
        return {"ok": False, **e.to_dict()}
    return {"ok": False, "error": "bad_args", "message": "unhandled"}
