"""wp_draft: drafts, media, terms, SEO fields, staged copies, and at the Publish level publish,
schedule and apply (contract § 8, plan D6).

Every write is planned first (``plan``): the site's access level is checked there through
``Runtime.require`` (enforce.py) before any write request, and the ``pre_tool_call`` gate (hooks.py)
and the handler build the same plan. Within the level no card is asked. ``status`` on create and
update stays ``draft`` or ``pending``; publishing is its own action. A change to a published post is
staged as a new draft copy carrying ``_wpbruecke_source`` when the connector registers that meta;
``apply`` copies it onto the live post. Every publishing write is read back and the reading returned.
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
from ..access import DRAFT_ACTIONS
from ..desk import Desk, DeskError
from ..runtime import NotFound, Runtime

ACTIONS = ("create", "update", "upload_media", "stage", "set_seo", "publish", "schedule", "apply")
PUBLISHING = ("publish", "schedule", "apply")
SEO_META = ("_wpbruecke_title", "_wpbruecke_description", "_yoast_wpseo_metadesc", "_yoast_wpseo_title",
            "rank_math_description", "rank_math_title", "_seopress_titles_desc", "_seopress_titles_title")
ALLOWED_STATUS = ("draft", "pending")
IMAGE_TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp"}
MAX_IMAGE = 10 * 1024 * 1024

SCHEMA = {
    "name": "wp_draft",
    "description": (
        "Write to a WordPress site within the access level the person confirmed for it (the role of the site's "
        "WordPress user: read, drafts or publish). Anything above the level is refused before any request, with "
        "the next step. action=create: a new post or page draft (type post|page) with title, content (block "
        "markup, or plain text where blank lines separate paragraphs and lines starting with ## become headings), "
        "excerpt, slug, categories and tags by existing name, featured_image {path, alt} (a local PNG/JPEG/GIF/WebP "
        "file) or featured_media (an id), seo {title, description} (needs the WPBrücke connector); status draft or "
        "pending only. action=update: change a draft (id); published posts are refused, use stage. action=stage: "
        "change a published post or page (id) as a new draft copy; the live post stays untouched. "
        "action=upload_media: an image file with alt text. action=set_seo: title/description on a draft, or as a "
        "staged copy for a published post. Publish level only: action=publish (id) publishes a draft; "
        "action=schedule (id, date as local ISO time like 2026-12-01T09:00) schedules it; action=apply (item = the "
        "desk item of a staged copy, or id = the copy) copies title, content, excerpt and SEO fields onto the live "
        "post and deletes the copy. These act without asking; each answer carries WordPress' read-back (status, "
        "date, content hash). Every write lands on the review desk; run wp_proof on the desk item next. Scheduled "
        "jobs and one-shot runs are refused except the person's own recurring-draft automation for that site, "
        "which writes drafts and publishes only when the automation says publish: true, and then only the post "
        "that same run created."),
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
        "date": {"type": "string", "description": "schedule: local date and time, ISO (2026-12-01T09:00)"},
        "item": {"type": "string", "description": "apply (also publish, schedule): the desk item id"},
    }, "required": ["site", "action"]},
}


class Refused(Exception):
    def __init__(self, code: str, message: str, setting: str = "", next: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code, self.message, self.setting, self.next = code, message, setting, next

    def to_dict(self) -> dict:
        out = {"ok": False, "error": self.code, "message": self.message, "sent": False}
        if self.setting:
            out["setting"] = self.setting
        if self.next:
            out["next"] = dict(self.next)
        return out


def local_date(value: Any) -> str:
    """A local ISO date and time (``2026-12-01T09:00`` or with seconds), as WordPress' ``date`` takes it."""
    text = str(value or "").strip().replace(" ", "T")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$", text)
    if not m:
        raise Refused("bad_args", "schedule needs date as local ISO time, like 2026-12-01T09:00")
    from datetime import datetime
    try:
        when = datetime(*(int(g or 0) for g in m.groups()))
    except ValueError as e:
        raise Refused("bad_args", f"date {text}: {e}") from None
    return when.strftime("%Y-%m-%dT%H:%M:%S")


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
    item = None
    if args.get("item"):
        try:
            item = Desk(rt.store).require(str(args["item"]))
        except DeskError as e:
            raise Refused("not_found", str(e)) from None
        if args.get("site") and args["site"] != item["site"]:
            raise Refused("bad_args", f"desk item {item['id']} belongs to site {item['site']}, not {args['site']}")
        args = {**args, "site": item["site"], "type": item["wp_type"], "id": args.get("id") or item["wp_id"]}
    try:
        site = rt.site(str(args.get("site") or ""))
    except NotFound as e:
        raise Refused("not_found", str(e)) from None
    if not site.get("key_slot"):
        raise Refused("no_key", f"site {site['id']} is a public site without a password; connect it in a Site N "
                      "group of the plugin settings (the WPBrücke page, Connect a site, walks you through it)", "key_slot")
    if not rt.key_present(site):
        raise Refused("no_key", f"site {site['id']} (Site {site['key_slot']}) is not connected ({rt.pairing(site)}); "
                      "check it on the WPBrücke page", "key_slot")
    refusal = rt.require(site, DRAFT_ACTIONS[action])
    if refusal is not None:
        raise Refused(refusal.code, refusal.message, next=refusal.next)
    facts = rt.site_facts(site)
    needs, why = False, "within the site's access level"  # plan D6: no card inside the level
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
    elif action in PUBLISHING:
        if args.get("id") is None:
            raise Refused("bad_args", f"{action} needs id or item")
        if action == "schedule":
            args = {**args, "date": local_date(args.get("date"))}
            lines.append(f"schedule {ptype} {int(args['id'])} for {args['date']}")
        elif action == "apply":
            lines.append(f"apply the staged copy {int(args['id'])} to its live {ptype}")
        else:
            lines.append(f"publish {ptype} {int(args['id'])}")
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


def execute(rt: Runtime, p: Plan, created_by: str = "hermes", automation_run: str = "") -> dict:
    site, args = p.site, p.args
    client = rt.client(site)
    desk = Desk(rt.store)
    ptype = str(args.get("type") or "post")
    route = f"/wp-json/wp/v2/{ptype}s"
    notes, out = [], {"ok": True, "site": site["id"], "action": p.action, "sent": True}
    meta, seo_problem = _seo_meta(p.facts, args.get("seo") or {})
    if seo_problem:
        notes.append(seo_problem)
    if p.action in PUBLISHING:
        return {**out, **publishing(client, desk, p)}
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
                           link=post.get("link") or "", live_status=post.get("status"), note=str(args.get("note") or ""),
                           automation_run=automation_run)
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


def _raw(post: dict, key: str) -> str:
    value = post.get(key)
    return str((value.get("raw") if value.get("raw") is not None else value.get("rendered")) or "") \
        if isinstance(value, dict) else str(value or "")


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


def reading(post: dict) -> dict:
    """What WordPress says about a post after a write (the read-back every publishing action returns)."""
    return {"id": post.get("id"), "status": post.get("status"), "date": post.get("date"),
            "date_gmt": post.get("date_gmt"), "link": post.get("link"), "title": _raw(post, "title")[:200],
            "content_hash": content_hash(_raw(post, "content"))}


def _desk_item(desk: Desk, p: Plan, pid: int) -> Optional[dict]:
    if p.args.get("item"):
        return desk.get(str(p.args["item"]))
    return desk.find(p.site["id"], pid)


def publishing(client, desk: Desk, p: Plan) -> dict:
    """publish, schedule or apply (contract § 8), each read back from WordPress."""
    from .content import do_not_touch
    ptype = str(p.args.get("type") or "post")
    route = f"/wp-json/wp/v2/{ptype}s"
    pid = int(p.args["id"])
    post = client.get(f"{route}/{pid}", context="edit")
    item = _desk_item(desk, p, pid)
    if p.action == "apply":
        return _apply(client, desk, p, route, post, item)
    if do_not_touch(p.site, post):
        raise Refused("do_not_touch", f"{ptype} {pid} is on the site file's do-not-touch list; nothing was sent",
                      "memory.do_not_touch")
    if post.get("status") == "publish":
        raise Refused("already_published", f"{ptype} {pid} is already published; nothing was sent")
    if item is not None and item.get("kind") in ("staged_edit", "seo_fix"):
        raise Refused("staged_copy", f"{ptype} {pid} is a staged copy of a live post; use action=apply, which copies "
                      "it onto the live post")
    body = {"status": "publish"} if p.action == "publish" else {"status": "future", "date": p.args["date"]}
    client.post(f"{route}/{pid}", body)
    after = reading(client.get(f"{route}/{pid}", context="edit"))
    want = "publish" if p.action == "publish" else "future"
    out = {"wp_id": pid, "read_back": after, "status": after["status"], "confirmed": after["status"] == want}
    if p.action == "schedule" and after["status"] == "publish":
        out["note"] = "WordPress published it at once: the date is not in the future in the site's time zone"
    if item is not None:
        item = desk.sync_live(item["id"], after["status"])
        out.update(desk_item=item["id"], desk_state=item["state"])
    return out


def _apply(client, desk: Desk, p: Plan, route: str, copy: dict, item: Optional[dict]) -> dict:
    from .content import do_not_touch
    meta = copy.get("meta") if isinstance(copy.get("meta"), dict) else {}
    source = (item or {}).get("source_id") or meta.get("_wpbruecke_source")
    if not source:
        raise Refused("not_staged", f"{p.args.get('type') or 'post'} {copy.get('id')} is not a staged copy (no desk item "
                      "with a live post and no _wpbruecke_source); nothing was sent")
    source = int(source)
    live = client.get(f"{route}/{source}", context="edit")
    if do_not_touch(p.site, live):
        raise Refused("do_not_touch", f"post {source} is on the site file's do-not-touch list; nothing was sent",
                      "memory.do_not_touch")
    body = {"title": _raw(copy, "title"), "content": _raw(copy, "content"), "excerpt": _raw(copy, "excerpt")}
    seo = {k: v for k, v in meta.items() if k in SEO_META and v not in (None, "")}
    if seo:
        body["meta"] = seo
    client.post(f"{route}/{source}", body)
    after_post = client.get(f"{route}/{source}", context="edit")
    after = reading(after_post)
    expected = content_hash(body["content"])
    out = {"wp_id": source, "copy_id": copy.get("id"), "read_back": after, "status": after["status"],
           "applied_fields": sorted(k for k in body if k != "meta") + [f"meta.{k}" for k in sorted(seo)],
           "content_matches": after["content_hash"] == expected, "expected_content_hash": expected}
    after_meta = after_post.get("meta") if isinstance(after_post.get("meta"), dict) else {}
    if seo:
        out["seo_matches"] = all(after_meta.get(k) == v for k, v in seo.items())
    client.delete(f"{route}/{int(copy['id'])}", force="true")
    from ..client import WPError as _WPError
    try:
        client.get(f"{route}/{int(copy['id'])}", context="edit", _fields="id,status")
        out["copy_deleted"] = False
    except _WPError as e:
        out["copy_deleted"] = e.kind == "not_found" or e.status == 410
    if item is not None:
        item = desk.mark_applied(item["id"])
        out.update(desk_item=item["id"], desk_state=item["state"])
    out["confirmed"] = bool(out["content_matches"] and out["copy_deleted"])
    return out


def _title(post: dict) -> str:
    t = post.get("title") or {}
    return html.unescape(str((t.get("raw") if isinstance(t, dict) else t) or (t.get("rendered") if isinstance(t, dict) else "") or ""))


def run(rt: Runtime, args: dict, approved: bool, created_by: str = "hermes", automation_run: str = "") -> dict:
    """The handler body after the lanes check: ``approved`` says Hermes' gate passed this exact call."""
    try:
        p = plan(rt, args)
        if p.needs_card and not approved:
            return Refused("not_approved", "This write needs the person's approval in Hermes and did not get it "
                           "(no card was answered for this exact call). Nothing was sent.").to_dict()
        return execute(rt, p, created_by, automation_run)
    except Refused as r:
        return r.to_dict()
    except WPError as e:
        return {"ok": False, **e.to_dict()}
