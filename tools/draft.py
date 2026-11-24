"""wp_draft: drafts, media, terms, SEO fields and staged copies. Never publishes (F4, F5, F11).

Every write is planned first (``plan``); the ``pre_tool_call`` gate (hooks.py) and the handler build
the same plan, so the approval card shows exactly what the handler then does. Status values other
than ``draft`` and ``pending`` are refused here before any request: there is no way to publish,
schedule or make private through this tool. A change to a published post is staged as a new draft
copy (D8 b, provisional) carrying ``_wpbruecke_source`` when the connector registers that meta; the
live post is never written.
"""
from __future__ import annotations

import hashlib
import html
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..client import WPError
from ..desk import Desk
from ..facts import can_change_live
from ..runtime import NotFound, Runtime

ACTIONS = ("create", "update", "upload_media", "stage", "set_seo")
ALLOWED_STATUS = ("draft", "pending")
IMAGE_TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp"}
MAX_IMAGE = 10 * 1024 * 1024

SCHEMA = {
    "name": "wp_draft",
    "description": (
        "Write to a WordPress site as drafts only; this tool never publishes, schedules or makes anything "
        "private (status may be draft or pending, anything else is refused before any request). "
        "action=create: a new post or page (type post|page) with title, content (block markup, or plain text "
        "where blank lines separate paragraphs and lines starting with ## become headings), excerpt, slug, "
        "categories and tags by existing name, featured_image {path, alt} (a local PNG/JPEG/GIF/WebP file) or "
        "featured_media (an id), seo {title, description} (needs the WPBrücke connector). action=update: change "
        "a draft you or the person wrote (id); published posts are refused, use stage. action=stage: change a "
        "published post or page (id) as a new draft copy; the live post stays untouched until the person applies "
        "it. action=upload_media: an image file with alt text. action=set_seo: title/description on a draft, or "
        "as a staged copy for a published post. Every write lands on the review desk; run wp_proof on the desk "
        "item next. When the site's key can publish (or the site says always ask), the person approves each "
        "call in a card; scheduled jobs and one-shot runs are refused except the person's own recurring-draft "
        "automation for that site."),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "type": {"type": "string", "enum": ["post", "page"]},
        "id": {"type": "integer", "description": "update, stage, set_seo: the post or page id"},
        "title": {"type": "string"}, "content": {"type": "string"}, "excerpt": {"type": "string"},
        "slug": {"type": "string"}, "status": {"type": "string", "enum": list(ALLOWED_STATUS)},
        "categories": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "featured_media": {"type": "integer"},
        "featured_image": {"type": "object", "properties": {"path": {"type": "string"}, "alt": {"type": "string"}}},
        "path": {"type": "string", "description": "upload_media: local image file"},
        "alt": {"type": "string", "description": "upload_media: alt text (required)"},
        "seo": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}}},
        "note": {"type": "string", "description": "a line for the desk item (what changed and why)"},
    }, "required": ["site", "action"]},
}


class Refused(Exception):
    def __init__(self, code: str, message: str, setting: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.setting = code, message, setting

    def to_dict(self) -> dict:
        out = {"ok": False, "error": self.code, "message": self.message, "sent": False}
        if self.setting:
            out["setting"] = self.setting
        return out


@dataclass
class Plan:
    site: dict
    action: str
    args: dict
    facts: dict
    needs_card: bool
    why_card: str
    lines: list = field(default_factory=list)

    def signature(self) -> str:
        import json
        return hashlib.sha256(json.dumps([self.site["id"], self.action, self.args], sort_keys=True,
                                         default=str).encode()).hexdigest()

    def approval_text(self) -> str:
        head = f"This writes to {self.site['url']} ({self.site['id']}) as {self.facts.get('user', {}).get('username', '?')}:"
        return "\n".join([head, *self.lines, f"Why you are asked: {self.why_card}."])


def blocks_from_text(text: str) -> str:
    """Plain text to core blocks: blank lines split paragraphs, '#'/'##'/'###' lines become headings."""
    if "<!-- wp:" in text:
        return text
    out = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(#{1,4})\s+(.+)$", chunk)
        if m and "\n" not in chunk:
            level = max(2, len(m.group(1)) + 1) if len(m.group(1)) == 1 else len(m.group(1))
            attr = "" if level == 2 else f' {{"level":{level}}}'
            out.append(f'<!-- wp:heading{attr} -->\n<h{level} class="wp-block-heading">{html.escape(m.group(2))}</h{level}>\n<!-- /wp:heading -->')
        else:
            body = "<br>".join(html.escape(l) for l in chunk.splitlines())
            out.append(f"<!-- wp:paragraph -->\n<p>{body}</p>\n<!-- /wp:paragraph -->")
    return "\n\n".join(out)


def _seo_meta(facts: dict, seo: dict) -> "tuple[dict, Optional[str]]":
    """Meta keys for an SEO title/description: the connector's field, or the active SEO plugin's key the
    connector opens to REST (seo/fields.py). Without the connector nothing is written and the note says why."""
    if not seo:
        return {}, None
    from ..seo.fields import description_target
    target = description_target(facts)
    if not target.get("meta_key"):
        return {}, f"SEO fields not written: {target.get('reason')}"
    meta = {}
    if seo.get("description"):
        meta[target["meta_key"]] = str(seo["description"])[:320]
    if seo.get("title"):
        meta["_wpbruecke_title"] = str(seo["title"])[:120]
    return meta, None


def plan(rt: Runtime, args: dict) -> Plan:
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        raise Refused("bad_args", f"action must be one of {', '.join(ACTIONS)}")
    status = args.get("status")
    if status is not None and status not in ALLOWED_STATUS:
        raise Refused("no_publish", f"status {status!r} is refused: WPBrücke writes drafts (draft or pending) only. "
                      "Publishing is the person's click in WordPress (the desk links to it).")
    try:
        site = rt.site(str(args.get("site") or ""))
    except NotFound as e:
        raise Refused("not_found", str(e)) from None
    if not site.get("key_slot") or not rt.key_present(site):
        raise Refused("no_key", f"site {site['id']} has no key; wp_doctor shows how to add one", "key_slot")
    facts = rt.site_facts(site)
    rights = facts.get("rights") or {}
    policy = rt.agent_writes(site)
    if policy == "always":
        needs, why = True, "the site file (or the plugin setting) says always ask"
    elif not rights:
        needs, why = True, "the key's rights could not be read, so WPBrücke cannot vouch that WordPress refuses a publish"
    elif can_change_live(rights):
        needs, why = True, "this site's key can publish or edit live posts, so WordPress would not stop a publish"
    else:
        needs, why = False, "drafts-only key"
    ptype = str(args.get("type") or "post")
    if ptype not in ("post", "page"):
        raise Refused("bad_args", "type must be post or page")
    lines = []
    if action == "create":
        if not args.get("title"):
            raise Refused("bad_args", "create needs a title")
        lines.append(f"create a {ptype} draft \"{str(args['title'])[:120]}\"")
        if args.get("content"):
            lines.append(f"content: {len(str(args['content']))} characters: {str(args['content'])[:300]}")
        if args.get("featured_image"):
            lines.append(f"upload image {Path(str(args['featured_image'].get('path', ''))).name} as featured image")
    elif action in ("update", "stage", "set_seo"):
        if args.get("id") is None:
            raise Refused("bad_args", f"{action} needs id")
        verb = {"update": "change draft", "stage": "stage a draft copy of live", "set_seo": "set SEO fields on"}[action]
        lines.append(f"{verb} {ptype} {int(args['id'])}")
        for k in ("title", "excerpt", "slug"):
            if args.get(k):
                lines.append(f"{k}: {str(args[k])[:200]}")
        if args.get("content"):
            lines.append(f"content: {len(str(args['content']))} characters: {str(args['content'])[:300]}")
    elif action == "upload_media":
        if not args.get("path") or not str(args.get("alt") or "").strip():
            raise Refused("bad_args", "upload_media needs path and alt (alt text is required)")
        lines.append(f"upload image {Path(str(args['path'])).name} with alt \"{str(args['alt'])[:120]}\"")
    if args.get("seo"):
        lines.append(f"SEO: {args['seo']}")
    return Plan(site, action, {k: v for k, v in args.items() if k != "site"}, facts, needs, why, lines)


def _terms(client, tax: str, names: list) -> "tuple[list, list]":
    ids, missing = [], []
    for name in names or []:
        name = str(name).strip()
        if not name:
            continue
        found = client.get(f"/wp-json/wp/v2/{tax}", search=name, per_page=20, _fields="id,name")
        hit = next((t for t in found if str(t.get("name", "")).lower() == name.lower()), None)
        (ids.append(hit["id"]) if hit else missing.append(name))
    return ids, missing


def _read_image(path: str) -> "tuple[bytes, str, str]":
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise Refused("bad_args", f"no image file at {path}")
    if p.stat().st_size > MAX_IMAGE:
        raise Refused("bad_args", "images up to 10 MB")
    data = p.read_bytes()
    mime = next((m for sig, m in IMAGE_TYPES.items() if data.startswith(sig)), None)
    if mime is None or (mime == "image/webp" and data[8:12] != b"WEBP"):
        raise Refused("bad_args", "only PNG, JPEG, GIF or WebP images can be uploaded")
    ext = mimetypes.guess_extension(mime) or ".img"
    name = re.sub(r"[^A-Za-z0-9._-]", "-", p.stem)[:60] + ext
    return data, mime, name


def upload(client, path: str, alt: str, title: str = "") -> dict:
    data, mime, name = _read_image(path)
    media = client.request("POST", "/wp-json/wp/v2/media", data=data,
                           headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{name}"'}).json()
    client.post(f"/wp-json/wp/v2/media/{media['id']}", {"alt_text": alt[:300], **({"title": title} if title else {})})
    return {"id": media["id"], "src": media.get("source_url"), "alt": alt[:300]}


def execute(rt: Runtime, p: Plan, created_by: str = "hermes") -> dict:
    site, args = p.site, p.args
    client = rt.client(site)
    desk = Desk(rt.store)
    ptype = str(args.get("type") or "post")
    route = f"/wp-json/wp/v2/{ptype}s"
    notes, out = [], {"ok": True, "site": site["id"], "action": p.action, "sent": True}
    meta, seo_problem = _seo_meta(p.facts, args.get("seo") or {})
    if seo_problem:
        notes.append(seo_problem)
    if p.action == "upload_media":
        media = upload(client, args["path"], str(args["alt"]))
        return {**out, "media": media, "next": "pass featured_media to wp_draft create/update"}

    body: dict = {}
    for k in ("title", "excerpt", "slug"):
        if args.get(k):
            body[k] = str(args[k])
    if args.get("content"):
        body["content"] = blocks_from_text(str(args["content"]))
    for tax in ("categories", "tags"):
        if args.get(tax):
            ids, missing = _terms(client, tax, args[tax])
            body[tax] = ids
            if missing:
                notes.append(f"{tax} not found (not created): {', '.join(missing)}")
    if args.get("featured_image"):
        fi = args["featured_image"]
        if not str(fi.get("alt") or "").strip():
            raise Refused("bad_args", "featured_image needs alt text")
        body["featured_media"] = upload(client, fi.get("path", ""), str(fi["alt"]))["id"]
    elif args.get("featured_media"):
        body["featured_media"] = int(args["featured_media"])
    if meta:
        body["meta"] = meta

    if p.action == "create":
        body["status"] = args.get("status") or "draft"
        post = client.post(route, body)
        item = desk.create(site=site["id"], site_url=site["url"], kind="new_page" if ptype == "page" else "new_post",
                           wp_type=ptype, wp_id=post["id"], title=_title(post), created_by=created_by,
                           link=post.get("link") or "", live_status=post.get("status"), note=str(args.get("note") or ""))
        out.update(wp_id=post["id"], status=post.get("status"), desk_item=item["id"], links=item["links"])
    else:
        pid = int(args["id"])
        live = client.get(f"{route}/{pid}", context="edit")
        from .content import do_not_touch
        if do_not_touch(site, live):
            raise Refused("do_not_touch", f"{ptype} {pid} is on the site file's do-not-touch list; nothing was sent",
                          "memory.do_not_touch")
        is_live = live.get("status") in ("publish", "future", "private")
        if p.action == "update" and is_live:
            raise Refused("live_post", f"{ptype} {pid} is {live.get('status')}; use action=stage for a draft copy, the "
                          "live post is never written")
        if p.action in ("stage",) or (p.action == "set_seo" and is_live):
            copy = {"title": body.get("title") or (live.get("title") or {}).get("raw") or "",
                    "content": body.get("content") or (live.get("content") or {}).get("raw") or "",
                    "excerpt": body.get("excerpt") or (live.get("excerpt") or {}).get("raw") or "",
                    "status": "draft"}
            for k in ("categories", "tags", "featured_media"):
                if k in body or live.get(k):
                    copy[k] = body.get(k, live.get(k))
            copy_meta = dict(meta)
            if p.facts.get("connector") or p.facts.get("connector_ns"):
                copy_meta["_wpbruecke_source"] = pid
            if copy_meta:
                copy["meta"] = copy_meta
            post = client.post(route, copy)
            kind = "seo_fix" if p.action == "set_seo" else "staged_edit"
            item = desk.create(site=site["id"], site_url=site["url"], kind=kind, wp_type=ptype, wp_id=post["id"],
                               title=_title(post), created_by=created_by, source_id=pid, link=post.get("link") or "",
                               live_status=post.get("status"), note=str(args.get("note") or ""))
            after = client.get(f"{route}/{pid}", context="edit")
            out.update(wp_id=post["id"], source_id=pid, desk_item=item["id"], links=item["links"],
                       live_unchanged=after.get("modified_gmt") == live.get("modified_gmt"))
            if "_wpbruecke_source" not in copy_meta:
                notes.append("without the connector the copy has no _wpbruecke_source meta; the desk item remembers "
                             "the live post, and applying it is a copy in the editor")
        else:
            post = client.post(f"{route}/{pid}", body) if body else live
            item = desk.find(site["id"], pid)
            if item is None:
                item = desk.create(site=site["id"], site_url=site["url"],
                                   kind="seo_fix" if p.action == "set_seo" else ("new_page" if ptype == "page" else "new_post"),
                                   wp_type=ptype, wp_id=pid, title=_title(post), created_by="person"
                                   if live.get("author") != (p.facts.get("user") or {}).get("id") else created_by,
                                   link=post.get("link") or "", live_status=post.get("status"))
            elif item["state"] in ("changes_requested",):
                item = desk.move(item["id"], "revise", "hermes", str(args.get("note") or "revised"))
            elif args.get("note"):
                item = desk.note(item["id"], "hermes", str(args["note"]))
            out.update(wp_id=pid, status=post.get("status"), desk_item=item["id"], links=item["links"])
    if notes:
        out["notes"] = notes
    out["next"] = f"run wp_proof with item={out.get('desk_item')} to render it at three widths"
    return out


def _title(post: dict) -> str:
    t = post.get("title") or {}
    return html.unescape(str((t.get("raw") if isinstance(t, dict) else t) or (t.get("rendered") if isinstance(t, dict) else "") or ""))


def run(rt: Runtime, args: dict, approved: bool, created_by: str = "hermes") -> dict:
    """The handler body after the lanes check: ``approved`` says Hermes' gate passed this exact call."""
    try:
        p = plan(rt, args)
        if p.needs_card and not approved:
            return Refused("not_approved", "This write needs the person's approval in Hermes and did not get it "
                           "(no card was answered for this exact call). Nothing was sent.").to_dict()
        return execute(rt, p, created_by)
    except Refused as r:
        return r.to_dict()
    except WPError as e:
        return {"ok": False, **e.to_dict()}
