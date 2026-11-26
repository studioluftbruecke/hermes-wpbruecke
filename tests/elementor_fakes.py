"""A fake WPBrücke connector with the four Elementor routes, on top of wpb_fakes.FakeWP. It answers the way
includes/elementor.php does (checks, codes, fingerprint at stage, trash at apply), so the pack's Python is
tested against the routes' contract; the PHP itself is proven in the harness (live/v04_ae2_elementor.py)."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import re
from typing import Callable, Optional

from wpb_fakes import ROLE_CAPS, FakeWP

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "elementor" / "harness-v3-page.json"
EDITABLE_TYPES = ("heading", "text-editor", "button", "image", "icon-list", "call-to-action")


def harness_tree() -> list:
    return copy.deepcopy(json.loads(FIX.read_text())["elements"])


class ElementorSite(FakeWP):
    """FakeWP plus Elementor pages and the connector's Elementor routes."""

    def __init__(self, role: str = "editor", version: str = "4.4.2", **kw) -> None:
        super().__init__(user="hermes", caps=ROLE_CAPS[role], roles=[role],
                         namespaces=["wp/v2", "wpbruecke/v1", "elementor/v1"], **kw)
        self.role, self.version = role, version
        self.unfiltered_html = role in ("editor", "administrator")
        self.tamper: Optional[Callable[[list], list]] = None   # run on every save: a broken save
        self.dynamic_tags: tuple = ()   # tag names this Elementor resolves (4.4.2 free: none)
        self.calls: list = []
        self.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.4.0"})
        self.routes[("GET", "/wp-json/elementor/v1/globals")] = FakeWP.j(200, {
            "colors": {"primary": {"id": "primary", "title": "Primary", "value": "#6EC1E4"}},
            "typography": {"primary": {"id": "primary", "title": "Primary", "value": {"typography_font_family": "Roboto"}},
                           "text": {"id": "text", "title": "Text", "value": {"typography_font_family": "Inter"}}}})

    # --- posts ---------------------------------------------------------------------------------------
    def elementor_page(self, ptype="page", status="publish", tree=None, title="Harness Elementor page", author=1,
                       template="") -> dict:
        meta = {"_elementor_edit_mode": "builder", "_elementor_template_type": "wp-page",
                "_elementor_data": json.dumps(tree if tree is not None else harness_tree())}
        if template:
            meta["_wp_page_template"] = template
        return self.add_post(ptype=ptype, status=status, title=title, content="<p>fallback copy</p>", author=author,
                             meta=meta, wpbruecke_builder={"id": "elementor", "evidence": ["meta _elementor_edit_mode=builder"]})

    def tree(self, pid: int) -> list:
        return json.loads(self.posts[pid]["meta"]["_elementor_data"])

    def fingerprint(self, pid: int) -> str:
        p = self.posts[pid]
        caches = ("_elementor_css", "_elementor_element_cache", "_elementor_page_assets")
        meta = {k: v for k, v in sorted(p["meta"].items()) if (k.startswith("_elementor") and k not in caches)
                or k in ("_wpbruecke_title", "_wpbruecke_description")}
        parts = [p.get("modified_gmt"), p.get("status"), p["title"]["raw"], p["excerpt"]["raw"], p["content"]["raw"],
                 p["meta"].get("_elementor_data", ""), p["meta"].get("_wp_page_template", ""), json.dumps(meta)]
        return hashlib.sha256("\x1f".join(str(x) for x in parts).encode()).hexdigest()

    def person_edits_live(self, pid: int, title: str) -> None:
        tree = self.tree(pid)
        tree[0]["elements"][0]["settings"]["title"] = title
        self.posts[pid]["meta"]["_elementor_data"] = json.dumps(tree)
        self.posts[pid]["modified_gmt"] = "2026-11-25T12:00:00"

    # --- the routes ----------------------------------------------------------------------------------
    def _can_edit(self, post: dict) -> bool:
        if post["type"] == "page" and not self.caps.get("edit_pages"):
            return False
        mine = post.get("author") == 1
        if post["status"] == "publish" and not (self.caps.get("edit_published_posts") or self.caps.get("edit_others_pages")):
            return False
        return mine or bool(self.caps.get("edit_others_posts"))

    def default(self, method, parsed, headers, body):
        m = re.match(r"^/wp-json/wpbruecke/v1/elementor/(\d+)(/stage|/apply)?$", parsed.path)
        if not m:
            return super().default(method, parsed, headers, body)
        if not self.authed(headers):
            return self.j(401, {"code": "rest_not_logged_in", "message": "not logged in"})
        pid, verb = int(m.group(1)), (m.group(2) or "").lstrip("/")
        self.calls.append((method, verb or ("read" if method == "GET" else "save"), pid))
        post = self.posts.get(pid)
        if post is None:
            return self.j(404, {"code": "rest_post_invalid_id", "message": "Invalid post ID."})
        if not self._can_edit(post) and verb != "apply":
            return self.j(403, {"code": "rest_forbidden", "message": "Sorry, you are not allowed to do that."})
        data = json.loads(body.decode()) if body else {}
        if method == "GET":
            return self.j(200, self.read(pid))
        if verb == "stage":
            return self.stage(pid)
        if verb == "apply":
            return self.apply(pid)
        return self.save(pid, data.get("edits") or [])

    def read(self, pid: int) -> dict:
        p = self.posts[pid]
        return {"post_id": pid, "type": p["type"], "status": p["status"], "title": p["title"]["raw"],
                "modified_gmt": p["modified_gmt"], "source": int(p["meta"].get("_wpbruecke_source") or 0),
                "staged": "_wpbruecke_elementor_base" in p["meta"], "version": self.version, "edit_mode": "builder",
                "template": p["meta"].get("_wp_page_template", ""), "elements": self.tree(pid), "page_settings": {},
                "unknown_types": [], "unknown_dynamic": self.unknown_dynamic(self.tree(pid)),
                "unfiltered_html": self.unfiltered_html, "kses_would_strip": False}

    def unknown_dynamic(self, tree: list) -> list:
        """'<element id> <setting>' per dynamic tag this Elementor cannot resolve (repeater items too)."""
        out = []

        def known(text) -> bool:
            m = re.search(r'name="([^"]+)"', str(text))
            return bool(m) and m.group(1) in self.dynamic_tags

        def walk(els):
            for el in els:
                s = el.get("settings") if isinstance(el.get("settings"), dict) else {}
                sets = {"": s.get("__dynamic__") or {}}
                for k, v in s.items():
                    for item in v if isinstance(v, list) else []:
                        if isinstance(item, dict) and item.get("__dynamic__"):
                            sets[f"{k}.{item.get('_id')}."] = item["__dynamic__"]
                out.extend(f"{el['id']} {where}{name}" for where, d in sets.items() for name, text in d.items()
                           if not known(text))
                walk(el.get("elements") or [])
        walk(tree)
        return out

    def _refuse_unknown_dynamic(self, pid: int):
        unknown = self.unknown_dynamic(self.tree(pid))
        if unknown:
            return self.j(409, {"code": "wpbruecke_unknown_dynamic", "message": f"Elementor here cannot resolve the "
                                f"dynamic tags of {', '.join(unknown)}.", "data": {"elements": unknown}})
        return None

    def stage(self, pid: int):
        refused = self._refuse_unknown_dynamic(pid)
        if refused:
            return refused
        live = self.posts[pid]
        self.next_id += 1
        cid = self.next_id
        meta = {k: v for k, v in live["meta"].items() if k.startswith("_elementor") or k == "_wp_page_template"}
        meta.update(_wpbruecke_source=pid, _wpbruecke_elementor_base=self.fingerprint(pid))
        self.posts[cid] = {**copy.deepcopy(live), "id": cid, "status": "draft", "meta": meta,
                           "link": f"https://{self.host}/?p={cid}", "modified_gmt": "2026-11-25T10:00:00"}
        return self.j(200, {"copy": cid, "source": pid, "status": "draft", "template": meta.get("_wp_page_template", "")})

    def save(self, pid: int, edits: list):
        post = self.posts[pid]
        if post["status"] not in ("draft", "pending"):
            return self.j(409, {"code": "wpbruecke_live_post", "message": "Only a draft or a staged copy is saved here."})
        refused = self._refuse_unknown_dynamic(pid)
        if refused:
            return refused
        tree, stored = self.tree(pid), {}
        index = {}

        def walk(els):
            for el in els:
                index[el["id"]] = el
                walk(el.get("elements") or [])
        walk(tree)
        for e in edits:
            el = index.get(e["element_id"])
            if el is None:
                return self.j(409, {"code": "wpbruecke_unknown_element", "message": f"No element {e['element_id']}."})
            if el.get("widgetType") not in EDITABLE_TYPES:
                return self.j(409, {"code": "wpbruecke_read_only", "message": "read-only"})
            stored[e["element_id"]] = {}
            for path, value in e["set"].items():
                if path.endswith(("is_external", "nofollow")):
                    value = "on" if value in (True, "on", 1, "1") else ""
                parts, cur = path.split("."), el.setdefault("settings", {})
                for seg in parts[:-1]:
                    cur = cur.setdefault(seg, {})
                cur[parts[-1]] = value
                stored[e["element_id"]][path] = value
                if path.endswith("image.id"):
                    url = f"https://{self.host}/wp-content/uploads/{value}.png"
                    cur["url"] = url
                    stored[e["element_id"]][path[:-2] + "url"] = url
        if self.tamper:
            tree = self.tamper(tree)
        post["meta"]["_elementor_data"] = json.dumps(tree)
        post["modified_gmt"] = "2026-11-25T11:00:00"
        return self.j(200, {"saved": True, "post_id": pid, "stored": stored, "elements": tree})

    def apply(self, cid: int):
        copy_post = self.posts[cid]
        live_id = int(copy_post["meta"].get("_wpbruecke_source") or 0)
        live = self.posts.get(live_id)
        if not live or not (self.caps.get("publish_pages" if live["type"] == "page" else "publish_posts")):
            return self.j(403, {"code": "rest_forbidden", "message": "Sorry, you are not allowed to do that."})
        if copy_post["meta"].get("_wpbruecke_elementor_base") != self.fingerprint(live_id):
            return self.j(409, {"code": "live_changed", "message": "The live post changed since this copy was staged."})
        for k, v in copy_post["meta"].items():
            if k.startswith("_elementor") or k == "_wp_page_template":
                live["meta"][k] = v
        for k in ("title", "excerpt", "content"):
            live[k] = copy.deepcopy(copy_post[k])
        live["modified_gmt"] = "2026-11-25T13:00:00"
        copy_post["status"] = "trash"
        return self.j(200, {"applied": True, "live": live_id, "copy": cid, "copy_trashed": True, "live_keys_removed": []})
