"""The Elementor pack: read the tree, stage a draft copy, save edits on it, apply it, read tokens. Every
write goes through the WPBrücke connector's Elementor routes (``includes/elementor.php``), which save
through Elementor's own document save (D22) and hold the permission checks; this module refuses
everything outside the pack's scope before a request and checks each write by reading it back.
"""
from __future__ import annotations

import re
from typing import Optional

from ...client import WPError
from . import model as M
from . import widgets as W

ROUTE = "/wp-json/wpbruecke/v1/elementor"
NEEDS = "0.4.0"
DRAFTER_ROLE = "wpbruecke_drafter"


def detect(facts: dict, post: Optional[dict] = None) -> bool:
    """Elementor is active: its REST namespace, or its plugin among the site's active plugins or assets."""
    facts = facts or {}
    if "elementor/v1" in (facts.get("namespaces") or []):
        return True
    seo = facts.get("seo_public") or {}
    slugs = [str(p).lower() for p in seo.get("plugins_in_html") or []]
    slugs += [str(p.get("plugin") or "").split("/")[0].lower() for p in facts.get("plugins") or []
              if isinstance(p, dict) and p.get("status") == "active"]
    return "elementor" in slugs


def _refusal(e: WPError, what: str) -> M.PackRefused:
    """A connector refusal in the pack's shape (the route's code and message kept)."""
    code = e.code or ""
    if e.status == 404 and code == "rest_no_route":
        return M.PackRefused("needs_connector", f"the site's WPBrücke connector has no Elementor routes; install "
                             f"connector {NEEDS} or newer (wp_doctor shows the version). Nothing was sent.")
    if code == "kses_would_strip":
        return M.PackRefused("kses_would_strip", f"{what}: {e.message.split(': ', 1)[-1]}")
    if code == "live_changed":
        return M.PackRefused("live_changed", f"{what}: the live page changed since the copy was staged (someone "
                             "edited it); stage the change again so nobody's edit is lost. Nothing was applied.")
    if e.status in (401, 403):
        return M.PackRefused("forbidden", f"{what}: WordPress refused it for this site's user ({e.message}).")
    if code == "wpbruecke_unknown_dynamic":
        return M.PackRefused("elementor_unknown_dynamic", f"{what}: {e.message} Nothing was changed.")
    if code == "wpbruecke_elementor_error":
        return M.PackRefused("elementor_error", f"{what}: {e.message}")
    if code == "wpbruecke_dynamic":
        return M.PackRefused("elementor_dynamic", f"{what}: {e.message}")
    if code in ("wpbruecke_unknown_element", "wpbruecke_unknown_item", "wpbruecke_structure_changed"):
        return M.PackRefused("elementor_structure", f"{what}: {e.message} {W.SCOPE}")
    if code in ("wpbruecke_read_only", "wpbruecke_unknown_widget"):
        return M.PackRefused("elementor_read_only", f"{what}: {e.message} {W.SCOPE}")
    return M.PackRefused(code.replace("wpbruecke_", "elementor_") or "wp_error", f"{what}: {e.message}")


def read(client, post_id: int) -> M.ElementorDoc:
    try:
        return M.ElementorDoc.from_route(client.get(f"{ROUTE}/{int(post_id)}"))
    except WPError as e:
        raise _refusal(e, f"reading Elementor post {post_id}") from None


def untested(doc: M.ElementorDoc) -> Optional[str]:
    return W.untested_reason(doc.version)


def refuse_drafter_page(facts: dict, ptype: str) -> None:
    """D39: the drafter role holds no edit_pages, so it never edits an Elementor page."""
    roles = ((facts or {}).get("user") or {}).get("roles") or []
    if ptype == "page" and DRAFTER_ROLE in roles:
        raise M.PackRefused("drafter_page", "this site's WPBrücke user has the drafter role, which edits its own "
                            "posts only and never a page (no edit_pages); an Editor's key stages page changes. "
                            "Nothing was sent.")


def check(doc: M.ElementorDoc, edits: list) -> None:
    why = untested(doc)
    if why:
        raise M.PackRefused("elementor_untested", f"{why}. Nothing was sent.")
    M.check_edits(doc, edits)


def stage(client, live_id: int) -> dict:
    try:
        return client.post(f"{ROUTE}/{int(live_id)}/stage", {})
    except WPError as e:
        raise _refusal(e, f"staging Elementor post {live_id}") from None


def write(client, post_id: int, edits: list, before: Optional[M.ElementorDoc] = None) -> dict:
    """Save checked edits on a draft or staged copy and read them back. Returns
    {hash, stored, filtered}; refuses (``elementor_readback``) when the saved tree is not the old one with
    the reported values set."""
    before = before or read(client, post_id)
    check(before, edits)
    try:
        saved = client.post(f"{ROUTE}/{int(post_id)}", {"edits": edits})
    except WPError as e:
        raise _refusal(e, f"saving Elementor post {post_id}") from None
    stored = saved.get("stored") if isinstance(saved.get("stored"), dict) else {}
    after = read(client, post_id)
    problem = M.readback_problem(before.elements, stored, after.elements)
    if problem:
        raise M.PackRefused("elementor_readback", f"post {post_id} was saved, but reading it back shows {problem}; "
                            "it is a draft, so nothing visitors see changed. Look at it in Elementor before "
                            "anything is applied.")
    filtered = []
    for e in edits:
        got = stored.get(e["element_id"])
        got = got if isinstance(got, dict) else {}
        for path, sent in e["set"].items():
            if W.leaf(path) in W.FLAGS + ("id",):
                continue
            if path in got and str(got[path]) != str(sent):
                filtered.append(f"{e['element_id']} {path}")
    return {"hash": after.hash, "stored": stored, "filtered": filtered, "doc": after}


def apply(client, copy_id: int) -> dict:
    try:
        return client.post(f"{ROUTE}/{int(copy_id)}/apply", {})
    except WPError as e:
        raise _refusal(e, f"applying Elementor copy {copy_id}") from None


# --- what wp_content shows --------------------------------------------------------------------------------

_SUMMARY_KEYS = ("title", "editor", "text", "html", "description", "caption", "button")


def _summary(el: dict) -> Optional[str]:
    s = el.get("settings") if isinstance(el.get("settings"), dict) else {}
    for k in _SUMMARY_KEYS:
        v = s.get(k)
        if isinstance(v, str) and v.strip():
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", v)).strip()
            return text[:160]
    return None


def view(doc: M.ElementorDoc, locked: Optional[str]) -> dict:
    """The tree for wp_content get: per element its editable paths with their values, or why it is
    read-only. ``locked`` (pack off, untested release, kses) makes every element read-only with that reason."""

    def one(el: dict) -> dict:
        out: dict = {"id": el.get("id"), "elType": el.get("elType")}
        if el.get("widgetType"):
            out["widgetType"] = el.get("widgetType")
        why = M.read_only_reason(el)
        paths = [] if (why or locked) else M.editable_paths(el)
        out["editable"] = paths
        if paths:
            settings = el.get("settings") if isinstance(el.get("settings"), dict) else {}
            out["values"] = {p: M.get_path(settings, p) for p in paths}
        dyn = M.dynamic_paths(el)
        if dyn:
            out["dynamic"] = dyn
        if why or locked or not paths:
            out["read_only"] = why or locked or "every editable setting here comes from a dynamic tag"
            if el.get("elType") == "widget":
                text = _summary(el)
                if text:
                    out["text"] = text
        kids = el.get("elements") if isinstance(el.get("elements"), list) else []
        if kids:
            out["elements"] = [one(k) for k in kids if isinstance(k, dict)]
        return out

    return {"version": doc.version, "tested": W.tested_range(), "hash": doc.hash, "template": doc.template,
            "editable": locked is None, "read_only": locked, "scope": W.SCOPE,
            "elements": [one(el) for el in doc.elements if isinstance(el, dict)]}


def lock_reason(doc: M.ElementorDoc, pack_on: bool) -> Optional[str]:
    if not pack_on:
        return "the Elementor pack is off for this site (packs.elementor: true switches it on; the person approves that)"
    why = untested(doc)
    if why:
        return why
    if doc.kses_would_strip:
        return ("WordPress would strip HTML from this page for this site's user (no unfiltered_html), so a save "
                "would change parts an edit does not touch (kses_would_strip)")
    if doc.unknown_types:
        return f"Elementor here does not know {', '.join(doc.unknown_types)}; a save would drop those elements"
    if doc.unknown_dynamic:
        return M.unknown_dynamic_reason(doc)
    return None


# --- design tokens (P30, D34) -----------------------------------------------------------------------------

def tokens(client, facts: dict) -> dict:
    """Elementor's global colours and fonts, read live from ``elementor/v1/globals``."""
    data = client.get("/wp-json/elementor/v1/globals") or {}
    colors = []
    for cid, c in (data.get("colors") or {}).items():
        if isinstance(c, dict):
            colors.append({"slug": str(c.get("id") or cid), "name": str(c.get("title") or cid),
                           "value": str(c.get("value") or "")})
    typo = data.get("typography") or {}

    def family(key: str) -> str:
        v = (typo.get(key) or {}).get("value") if isinstance(typo.get(key), dict) else None
        return str((v or {}).get("typography_font_family") or "") if isinstance(v, dict) else ""
    body = family("text")
    return {"colors": colors, "fonts": {"body": body, "headings": family("primary") or body}, "logo": None,
            "source": "elementor"}
