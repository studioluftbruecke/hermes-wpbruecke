"""wp_seo: SEO post and site checks, and fixes staged as drafts for the person (F9, F10, F11).

Reports are stored as ``seo/<id>.json`` (SeoReport); a post report is attached to the post's desk
item. ``stage_fix`` turns a proposed description or title into a desk item through ``wp_draft``
(``set_seo`` on a draft, a staged copy for a published post), so it passes the same write gate and
is never applied without a person. "Discourage search engines" is never changed: it is not in core
REST (P10); the report links to Settings → Reading instead.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from ..client import WPError
from ..desk import Desk, DeskError
from ..runtime import NotFound, Runtime
from ..store import StoreError, new_id, now_iso

ACTIONS = ("check_post", "check_site", "stage_fix", "get")
STAGEABLE = ("description", "title")

SCHEMA = {
    "name": "wp_seo",
    "description": (
        "SEO for a WordPress site without an SEO plugin. action=check_post (site, id, type post|page): title "
        "length, meta description, headings, internal links, image alt text, slug and readability (Flesch for "
        "English, Flesch-Amstad for German, by the site file's first language), with proposed fixes. "
        "action=check_site (site): indexing (\"Discourage search engines\"), sitemap, robots.txt, home "
        "description and canonical, mobile layout and speed from the latest proof (PageSpeed when the user stored "
        "WPBRUECKE_PSI_KEY, else a local measurement labelled not Google). action=stage_fix (site, report, fix: "
        "description|title, optional value to override the proposal): stages the fix as a draft for the person's "
        "review (same approval rules as wp_draft). action=get (report). Every row is match, differs or "
        "not_proven with the reason; the score is a plain sum explained per row, not Google's number."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "site": {"type": "string"}, "id": {"type": "integer"},
        "type": {"type": "string", "enum": ["post", "page"]},
        "report": {"type": "string", "description": "stage_fix, get: the report id"},
        "fix": {"type": "string", "enum": list(STAGEABLE)},
        "value": {"type": "string", "description": "stage_fix: your own text instead of the proposal"},
    }, "required": ["action"]},
}


def _save(rt: Runtime, report: dict) -> dict:
    report = {**report, "id": new_id("seo"), "created_at": now_iso(), "fixes": report.get("fixes") or []}
    rt.store.write_json(rt.store.path("seo", f"{report['id']}.json"), report)
    return report


def _get(rt: Runtime, report_id: str) -> dict:
    try:
        r = rt.store.read_json(rt.store.path("seo", f"{report_id}.json"), None)
    except StoreError as e:
        raise ValueError(str(e)) from None
    if not r:
        raise ValueError(f"no SEO report {report_id}")
    return r


def check_post(rt: Runtime, site: dict, pid: int, ptype: str = "post") -> dict:
    from ..seo import fields, post as seo_post
    client = rt.client(site)
    p = client.get(f"/wp-json/wp/v2/{ptype}s/{pid}", **({"context": "edit"} if client.authenticated else {}))
    facts = rt.site_facts(site)
    target = fields.description_target(facts)
    meta = p.get("meta") or {}
    description = meta.get(target["meta_key"]) if target.get("meta_key") else None
    if not description and meta.get("_wpbruecke_description"):
        description = meta["_wpbruecke_description"]
    images = []
    if p.get("featured_media"):
        try:
            m = client.get(f"/wp-json/wp/v2/media/{p['featured_media']}", _fields="source_url,alt_text")
            images.append({"src": m.get("source_url"), "alt": m.get("alt_text") or ""})
        except WPError:
            pass
    internal = []
    try:
        for q in client.get("/wp-json/wp/v2/posts", per_page=20, _fields="id,title,link"):
            if q.get("id") != pid:
                internal.append({"id": q["id"], "title": html.unescape((q.get("title") or {}).get("rendered") or ""),
                                 "link": q.get("link")})
    except WPError:
        pass
    langs = (site.get("memory") or {}).get("languages") or []
    report = seo_post.check_post(
        {"title": html.unescape(((p.get("title") or {}).get("raw") or (p.get("title") or {}).get("rendered") or "")),
         "slug": p.get("slug") or "", "content_html": (p.get("content") or {}).get("rendered") or "",
         "excerpt": html.unescape(re.sub(r"<[^>]+>", "", (p.get("excerpt") or {}).get("rendered") or "")), "images": images},
        site_url=site["url"], lang=langs[0] if langs else "en", description=description,
        description_source=target.get("where"), internal_posts=internal)
    report.update(site=site["id"], wp_id=pid, wp_type=ptype, wp_status=p.get("status"),
                  description_target=target)
    report["checks"].append(_gsc_row(rt, site, p))
    saved = _save(rt, report)
    desk = Desk(rt.store)
    item = desk.find(site["id"], pid)
    if item:
        desk.update(item["id"], seo_id=saved["id"])
    return saved


def _gsc_fetch(method: str, url: str, headers: dict, body: bytes):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except ValueError:
            return e.code, None


def _gsc_row(rt: Runtime, site: dict, post: dict, fetch=None) -> dict:
    from ..seo import gsc
    from ..settings import GSC_KEY_ENV
    if post.get("status") != "publish":
        return {"check": "search_queries", "label": "Search Console queries", "weight": 0, "verdict": "not_proven",
                "detail": "a draft has no search data yet"}
    try:
        raw = rt.get_secret(GSC_KEY_ENV)
    except Exception:
        raw = None
    return gsc.row(raw, site.get("gsc_property") or "", post.get("link") or "", fetch or _gsc_fetch)


def _latest_home_proof(rt: Runtime, site: dict) -> Any:
    best = None
    for p in rt.store.dir("proofs").glob("*/run.json"):
        run = rt.store.read_json(p, None)
        if not isinstance(run, dict):
            continue
        t = run.get("target") or {}
        if t.get("site") == site["id"] and str(t.get("url") or "").rstrip("/") == site["url"].rstrip("/") \
                and run.get("shots") and (best is None or run.get("finished_at", "") > best.get("finished_at", "")):
            best = run
    return best


def check_site(rt: Runtime, site: dict, run_proof: bool = False) -> dict:
    from ..seo import site as seo_site
    from ..settings import PSI_KEY_ENV
    facts = rt.site_facts(site, refresh=True)
    client = rt.client(site, keyed=False)
    try:
        resp = client.raw("GET", "/", auth=False)
        home_html = resp.text() if resp.status < 400 else None
    except WPError:
        home_html = None
    proof = _latest_home_proof(rt, site)
    if proof is None and run_proof:
        from . import proof as proof_tool
        try:
            proof = proof_tool.prove(rt, {"site": site["id"], "url": site["url"] + "/"})
        except Exception:
            proof = None
    psi = None
    key = None
    try:
        key = rt.get_secret(PSI_KEY_ENV)
    except Exception:
        key = None
    if key:
        try:
            psi = seo_site.pagespeed(site["url"] + "/", key, _psi_fetch)
        except Exception as e:
            psi = {"error": str(e)[:200]}
    report = seo_site.check_site(facts, home_html=home_html, proof_run=proof, pagespeed=psi)
    report.update(site=site["id"], proof_id=(proof or {}).get("id"))
    return _save(rt, report)


def _psi_fetch(url: str) -> Any:
    from ..client import WordPressClient
    return WordPressClient("https://www.googleapis.com", timeout=90).request("GET", url, auth=False).json()


def fix_args(rt: Runtime, args: dict) -> dict:
    """The wp_draft call a stage_fix becomes (the gate approves exactly this)."""
    report = _get(rt, str(args.get("report") or ""))
    if report.get("scope") != "post":
        raise ValueError("only post reports have stageable fixes; site findings are your clicks in wp-admin")
    kind = str(args.get("fix") or "")
    if kind not in STAGEABLE:
        raise ValueError("fix must be description or title")
    value = str(args.get("value") or "").strip()
    if not value:
        for c in report.get("checks") or []:
            f = c.get("fix") or {}
            if f.get("kind") == kind and f.get("value"):
                value = str(f["value"])
                break
    if not value:
        raise ValueError(f"the report proposes no {kind}; pass value")
    out = {"site": report["site"], "action": "set_seo", "type": report.get("wp_type") or "post", "id": int(report["wp_id"]),
           "note": f"SEO fix from report {report['id']}: {kind}"}
    if kind == "description":
        out["seo"] = {"description": value}
    else:
        out["seo"] = {"title": value}
    return out


def handle(rt: Runtime, args: dict, run_draft=None) -> dict:
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return {"ok": False, "error": "bad_args", "message": f"action must be one of {', '.join(ACTIONS)}"}
    try:
        if action == "get":
            return {"ok": True, "report": _get(rt, str(args.get("report") or ""))}
        if action == "stage_fix":
            if run_draft is None:
                return {"ok": False, "error": "internal", "message": "stage_fix needs the draft lane"}
            draft_args = fix_args(rt, args)
            out = run_draft(draft_args)
            if out.get("ok") and out.get("desk_item"):
                try:
                    Desk(rt.store).update(out["desk_item"], seo_id=str(args.get("report")))
                    report = _get(rt, str(args["report"]))
                    report["fixes"] = list(report.get("fixes") or []) + [out["desk_item"]]
                    rt.store.write_json(rt.store.path("seo", f"{report['id']}.json"), report)
                except (DeskError, ValueError):
                    pass
            return out
        site = rt.site(str(args.get("site") or ""))
        if action == "check_post":
            if args.get("id") is None:
                return {"ok": False, "error": "bad_args", "message": "check_post needs id"}
            return {"ok": True, "report": check_post(rt, site, int(args["id"]), str(args.get("type") or "post"))}
        if action == "check_site":
            return {"ok": True, "report": check_site(rt, site, run_proof=True)}
    except NotFound as e:
        return {"ok": False, "error": "not_found", "message": str(e)}
    except ValueError as e:
        return {"ok": False, "error": "bad_args", "message": str(e)}
    except WPError as e:
        return {"ok": False, **e.to_dict()}
    return {"ok": False, "error": "bad_args", "message": "unhandled"}


def run_site_for_route(rt: Runtime, body: dict) -> dict:
    site = rt.site(str((body or {}).get("site") or ""))
    report = check_site(rt, site, run_proof=True)
    return {"ok": True, "report_id": report["id"], "score": report.get("score")}
