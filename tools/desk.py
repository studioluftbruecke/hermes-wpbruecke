"""wp_desk: what waits on the review desk; notes; asking for the person's publish click (F7)."""
from __future__ import annotations

from ..client import WPError
from ..desk import KINDS, STATES, Desk, DeskError
from ..runtime import NotFound, Runtime

ACTIONS = ("list", "get", "note", "request_publish", "refresh")

SCHEMA = {
    "name": "wp_desk",
    "description": (
        "The WPBrücke review desk: every draft, staged copy, SEO fix and build page waiting for the person. "
        "action=list (site, kind new_post|new_page|staged_edit|seo_fix|build_page, state waiting|"
        "changes_requested|reviewed|publish_requested|published|scheduled|discarded; default: open items), "
        "action=get (item): the item with its notes, proof and SEO ids and links, action=note (item, text): add "
        "a note for the person, action=request_publish (item): mark it as waiting for the person's publish "
        "click (you cannot publish; this only asks), action=refresh (item): read the post's live status back "
        "from WordPress (only this marks an item published or scheduled). Marking reviewed, asking for "
        "changes and discarding are the person's clicks on the desk."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "site": {"type": "string"}, "item": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)}, "state": {"type": "string", "enum": list(STATES)},
        "text": {"type": "string"},
    }, "required": ["action"]},
}


def refresh(rt: Runtime, desk: Desk, item: dict) -> dict:
    return desk.read_back(rt.client(rt.site(item["site"])), item)


def handle(rt: Runtime, args: dict) -> dict:
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return {"ok": False, "error": "bad_args", "message": f"action must be one of {', '.join(ACTIONS)}"}
    desk = Desk(rt.store)
    try:
        if action == "list":
            state = str(args.get("state") or "")
            items = desk.list(site=str(args.get("site") or ""), kind=str(args.get("kind") or ""), state=state,
                              open_only=not state)
            return {"ok": True, "count": len(items), "counts": desk.counts(str(args.get("site") or "")),
                    "items": [{k: i.get(k) for k in ("id", "site", "kind", "title", "state", "wp_id", "source_id",
                                                     "created_by", "created_at", "proof_id", "seo_id", "live_status")}
                              for i in items]}
        item_id = str(args.get("item") or "")
        if not item_id:
            return {"ok": False, "error": "bad_args", "message": f"{action} needs item"}
        if action == "get":
            return {"ok": True, "item": desk.require(item_id)}
        if action == "note":
            return {"ok": True, "item": desk.note(item_id, "hermes", str(args.get("text") or ""))}
        if action == "request_publish":
            item = desk.move(item_id, "request_publish", "hermes", str(args.get("text") or ""))
            return {"ok": True, "item": item, "message": ("Marked as waiting for the person's publish click. "
                    "Tell them it is on the review desk; publishing happens on their own WordPress login.")}
        if action == "refresh":
            return {"ok": True, "item": refresh(rt, desk, desk.require(item_id))}
    except (DeskError, NotFound) as e:
        return {"ok": False, "error": "refused" if isinstance(e, DeskError) else "not_found", "message": str(e)}
    except WPError as e:
        return {"ok": False, **e.to_dict()}
    return {"ok": False, "error": "bad_args", "message": "unhandled"}
