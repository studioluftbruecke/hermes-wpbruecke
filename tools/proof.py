"""wp_proof: render a page, a draft or a desk item at three widths and check it (F6).

Targets: a desk item (its draft through the connector's signed preview link; a staged copy is
compared against its live post), a post or page by id, or a URL on a known site. The browser reaches
only that site (plus the site file's ``proof.extra_hosts``). Without the connector a draft cannot be
opened by a browser (P13), so its proof is ``not_proven`` with that reason and only the REST-rendered
HTML is checked (image alt text). The result carries one small image (the widest screenshot) for a
vision model and the same JSON as text for any other model.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from ..client import WPError
from ..desk import Desk, DeskError
from ..runtime import NotFound, Runtime
from ..store import Busy, new_id, now_iso

SCHEMA = {
    "name": "wp_proof",
    "description": (
        "Prove a WordPress page in a real browser: screenshots at the site's widths (default 1440, 768, 390 px) "
        "and checks (renders, console errors, horizontal overflow, broken same-site links, image alt text, text "
        "contrast, local load time; for a staged change also the pixel difference against the live page). Each "
        "check answers match, differs or not_proven with its reason; report them as they are, a not_proven is "
        "never a pass. Target one of: item (a review-desk item id, the usual case after wp_draft), site + id "
        "(+ type post|page), or site + url (a page on that site). Drafts need the WPBrücke connector's preview "
        "link; without it the result is not_proven with the reason. One proof runs at a time (busy answers "
        "retry_after_s). Read-only: it never changes WordPress."),
    "parameters": {"type": "object", "properties": {
        "item": {"type": "string", "description": "desk item id"},
        "site": {"type": "string"}, "id": {"type": "integer"},
        "type": {"type": "string", "enum": ["post", "page"]},
        "url": {"type": "string", "description": "a page on the site (same host)"},
    }, "required": []},
}


class LevelRefused(ValueError):
    def __init__(self, refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


def _preview_link(client, post_id: int) -> Optional[str]:
    try:
        return client.post("/wp-json/wpbruecke/v1/preview-link", {"post_id": int(post_id)}).get("url")
    except WPError:
        return None


def _has_connector(facts: dict) -> bool:
    return bool(facts.get("connector") or facts.get("connector_ns"))


def resolve(rt: Runtime, args: dict) -> dict:
    """{site, url, before_url, target, not_proven?: reason, rendered?: html}"""
    item = None
    if args.get("item"):
        item = Desk(rt.store).require(str(args["item"]))
        site = rt.site(item["site"])
        ptype, pid = item["wp_type"], int(item["wp_id"])
    else:
        site = rt.site(str(args.get("site") or ""))
        ptype, pid = str(args.get("type") or "post"), args.get("id")
    host = urlparse(site["url"]).hostname
    target = {"kind": "desk_item" if item else ("draft" if pid else "url"), "site": site["id"],
              "item": item["id"] if item else None, "wp_id": pid}
    if not item and not pid:
        url = str(args.get("url") or site["url"] + "/")
        if urlparse(url).hostname != host:
            raise ValueError(f"{url} is not on {host}; the proof browser reaches only the site itself")
        return {"site": site, "url": url, "before_url": None, "target": {**target, "url": url}}
    client = rt.client(site)
    try:
        post = client.get(f"/wp-json/wp/v2/{ptype}s/{int(pid)}", **({"context": "edit"} if client.authenticated else {}))
    except WPError:
        refusal = rt.require(site, "keyed_read") if site.get("key_slot") else None
        if refusal is not None:  # a draft needs a keyed read, which this site's level does not allow now
            raise LevelRefused(refusal) from None
        raise
    status = post.get("status")
    before = None
    if item and item.get("source_id"):
        src = client.get(f"/wp-json/wp/v2/{ptype}s/{int(item['source_id'])}", _fields="id,link,status")
        before = src.get("link") if src.get("status") == "publish" else None
    if status == "publish":
        url = post.get("link")
    else:
        facts = rt.site_facts(site)
        url = _preview_link(client, pid) if _has_connector(facts) else None
        if not url:
            reason = ("draft preview needs the WPBrücke connector (an application password cannot open a draft "
                      "in a browser)") if not _has_connector(facts) else "the connector did not hand out a preview link"
            return {"site": site, "url": None, "before_url": before, "target": {**target, "url": post.get("link")},
                    "not_proven": reason, "rendered": (post.get("content") or {}).get("rendered") or ""}
    return {"site": site, "url": url, "before_url": before, "target": {**target, "url": url}, "item_obj": item}


def _rendered_only(rt: Runtime, r: dict) -> dict:
    imgs = re.findall(r"<img\b[^>]*>", r.get("rendered") or "")
    missing = [i for i in imgs if not re.search(r'\balt="[^"]+"', i)]
    checks = [{"check": "render", "verdict": "not_proven", "detail": r["not_proven"]},
              {"check": "alt_text", "verdict": "differs" if missing else ("match" if imgs else "not_proven"),
               "detail": (f"{len(missing)} of {len(imgs)} images in the draft's HTML have no alt text" if missing else
                          f"{len(imgs)} of {len(imgs)} images have alt text" if imgs else "no images in the draft's HTML")}]
    run = {"id": new_id("proof"), "target": r["target"], "widths": r["site"]["proof"]["widths"], "started_at": now_iso(),
           "finished_at": now_iso(), "seconds": 0, "chromium": None, "verdict": "not_proven", "shots": [],
           "checks": checks, "diff": [], "blocked_hosts": [], "console_errors": [], "perf": {}}
    path = rt.store.path("proofs", f"{run['id']}/run.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    rt.store.write_json(path, run)
    return run


def prove(rt: Runtime, args: dict) -> dict:
    from ..proof import checks as proof_checks
    r = resolve(rt, args)
    site = r["site"]
    if r.get("not_proven"):
        run = _rendered_only(rt, r)
    else:
        run = proof_checks.run_proof(store=rt.store, url=r["url"], host=urlparse(site["url"]).hostname,
                                     connect_ip=site.get("connect_ip") or "", widths=site["proof"]["widths"],
                                     extra_hosts=site["proof"]["extra_hosts"], chromium_path=rt.settings.chromium_path,
                                     target=r["target"], before_url=r.get("before_url"))
    if r["target"].get("item"):
        try:
            Desk(rt.store).update(r["target"]["item"], proof_id=run["id"])
        except DeskError:
            pass
    return run


def summary(run: dict) -> dict:
    return {"ok": True, "proof_id": run["id"], "verdict": run.get("verdict"), "target": run.get("target"),
            "checks": [{k: c.get(k) for k in ("check", "verdict", "detail", "width") if c.get(k) is not None}
                       for c in run.get("checks") or []],
            "diff": run.get("diff") or [], "blocked_hosts": run.get("blocked_hosts") or [],
            "shots": [{k: s.get(k) for k in ("width", "file", "height", "load_ms", "status")} for s in run.get("shots") or []],
            "seconds": run.get("seconds"), "chromium": run.get("chromium")}


def handle(rt: Runtime, args: dict) -> Any:
    try:
        run = prove(rt, args)
    except LevelRefused as e:
        return e.refusal.to_dict()
    except Busy:
        return {"ok": False, "error": "busy", "message": "Another proof is running; try again in a few seconds.",
                "retry_after_s": 10}
    except (NotFound, DeskError, ValueError) as e:
        return {"ok": False, "error": "bad_target", "message": str(e)}
    except WPError as e:
        return {"ok": False, **e.to_dict()}
    out = summary(run)
    shots = run.get("shots") or []
    if not shots:
        return out
    widest = max(shots, key=lambda s: s.get("width") or 0)
    try:
        from ..proof import diff
        img = diff.thumbnail(rt.store.path("proofs", f"{run['id']}/{widest['file']}"), width=480)
    except Exception:
        return out
    mime = "image/jpeg" if img[:3] == b"\xff\xd8\xff" else "image/png"
    text = json.dumps(out, ensure_ascii=False)
    return {"_multimodal": True, "text_summary": text,
            "content": [{"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(img).decode()}}]}


def run_for_route(rt: Runtime, body: dict) -> dict:
    """The desk's 'Run proof' button (a job in the plugin host child)."""
    return summary(prove(rt, body or {}))
