"""The review desk: one queue of drafts, staged copies, SEO fixes and build pages across sites (F7).

Items live in ``desk/<id>.json`` through ``store.Store`` (atomic writes under the ``desk`` lock).
``created_by`` is immutable. States and who may move them:

    waiting ──review──▶ reviewed ──request_publish──▶ publish_requested
       │  ╲                │                               │
       │   changes──▶ changes_requested ──revise──▶ waiting │
       ├──discard──▶ discarded                              │
       └── live status read back from WordPress ──▶ published | scheduled (any open state)
       └── wp_draft apply at the Publish level ──▶ applied (staged copies)

The person moves review, changes, discard and request_publish (the page's buttons); Hermes may add
notes, revise (after changes were asked for) and request_publish ("ask for his publish click").
Only a status read back from WordPress sets published or scheduled: the desk never claims it.
"""
from __future__ import annotations

from typing import Any, Optional

from .store import Store, StoreError, check_id, new_id, now_iso

KINDS = ("new_post", "new_page", "staged_edit", "seo_fix", "build_page")
STATES = ("waiting", "changes_requested", "reviewed", "publish_requested", "published", "scheduled", "applied",
          "discarded")
OPEN = ("waiting", "changes_requested", "reviewed", "publish_requested")
ACTORS = ("hermes", "person")
TRANSITIONS = {
    ("waiting", "review"): ("reviewed", ("person",)),
    ("waiting", "changes"): ("changes_requested", ("person",)),
    ("reviewed", "changes"): ("changes_requested", ("person",)),
    ("publish_requested", "changes"): ("changes_requested", ("person",)),
    ("changes_requested", "revise"): ("waiting", ("hermes", "person")),
    ("waiting", "revise"): ("waiting", ("hermes", "person")),
    ("waiting", "request_publish"): ("publish_requested", ("hermes", "person")),
    ("reviewed", "request_publish"): ("publish_requested", ("hermes", "person")),
}
for _s in OPEN:
    TRANSITIONS[(_s, "discard")] = ("discarded", ("person",))


class DeskError(ValueError):
    pass


def edit_link(site_url: str, wp_id: Any) -> str:
    return f"{site_url.rstrip('/')}/wp-admin/post.php?post={int(wp_id)}&action=edit"


class Desk:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _path(self, item_id: str):
        try:
            return self.store.path("desk", f"{check_id(item_id, 'desk item')}.json")
        except StoreError as e:
            raise DeskError(str(e)) from None

    def get(self, item_id: str) -> Optional[dict]:
        return self.store.read_json(self._path(item_id), None)

    def require(self, item_id: str) -> dict:
        item = self.get(item_id)
        if item is None:
            raise DeskError(f"no desk item {item_id}")
        return item

    def list(self, site: str = "", kind: str = "", state: str = "", open_only: bool = False) -> list:
        items = []
        for p in self.store.dir("desk").glob("*.json"):
            item = self.store.read_json(p, None)
            if not isinstance(item, dict):
                continue
            if site and item.get("site") != site:
                continue
            if kind and item.get("kind") != kind:
                continue
            if state and item.get("state") != state:
                continue
            if open_only and item.get("state") not in OPEN:
                continue
            items.append(item)
        return sorted(items, key=lambda i: i.get("created_at") or "", reverse=True)

    def find(self, site: str, wp_id: Any) -> Optional[dict]:
        for item in self.list(site=site):
            if str(item.get("wp_id")) == str(wp_id) and item.get("state") != "discarded":
                return item
        return None

    def create(self, *, site: str, site_url: str, kind: str, wp_type: str, wp_id: int, title: str,
               created_by: str = "hermes", source_id: Optional[int] = None, link: str = "",
               live_status: str = "draft", note: str = "", automation_run: str = "") -> dict:
        if kind not in KINDS:
            raise DeskError(f"kind must be one of {', '.join(KINDS)}")
        if created_by not in ACTORS:
            raise DeskError("created_by is hermes or person")
        item = {"id": new_id("item"), "site": site, "kind": kind, "wp_type": wp_type, "wp_id": int(wp_id),
                "source_id": int(source_id) if source_id else None, "title": str(title)[:200],
                "created_by": created_by, "created_at": now_iso(), "updated_at": now_iso(), "state": "waiting",
                "proof_id": None, "seo_id": None, "notes": [], "live_status": live_status,
                "links": {"edit": edit_link(site_url, wp_id), "view": link}}
        if automation_run:  # the automation cron run that created it (lanes.automation_may_publish)
            item["automation_run"] = str(automation_run)
        if source_id:
            item["links"]["source_edit"] = edit_link(site_url, source_id)
        if note:
            item["notes"].append({"at": now_iso(), "by": created_by, "text": note[:2000]})
        with self.store.lock("desk"):
            self.store.write_json(self._path(item["id"]), item)
        return item

    def update(self, item_id: str, **changes: Any) -> dict:
        if "created_by" in changes or "id" in changes or "automation_run" in changes:
            raise DeskError("id, created_by and automation_run never change")
        with self.store.lock("desk"):
            item = self.require(item_id)
            item.update(changes)
            item["updated_at"] = now_iso()
            self.store.write_json(self._path(item_id), item)
        return item

    def move(self, item_id: str, verb: str, by: str, note: str = "") -> dict:
        if by not in ACTORS:
            raise DeskError("by is hermes or person")
        with self.store.lock("desk"):
            item = self.require(item_id)
            rule = TRANSITIONS.get((item["state"], verb))
            if rule is None:
                raise DeskError(f"cannot {verb} an item that is {item['state']}")
            target, allowed = rule
            if by not in allowed:
                raise DeskError(f"only the person can {verb} a desk item")
            item["state"] = target
            if note:
                item["notes"].append({"at": now_iso(), "by": by, "text": note[:2000]})
            item["updated_at"] = now_iso()
            self.store.write_json(self._path(item_id), item)
        return item

    def note(self, item_id: str, by: str, text: str) -> dict:
        if not str(text or "").strip():
            raise DeskError("a note needs text")
        with self.store.lock("desk"):
            item = self.require(item_id)
            item["notes"].append({"at": now_iso(), "by": by, "text": str(text)[:2000]})
            item["updated_at"] = now_iso()
            self.store.write_json(self._path(item_id), item)
        return item

    def sync_live(self, item_id: str, wp_status: Optional[str], exists: bool = True, applied: bool = False) -> dict:
        """Record what WordPress says now. Only this sets published or scheduled. ``applied``: the
        connector's review screen copied this staged copy onto its live post (then trashed the copy)."""
        with self.store.lock("desk"):
            item = self.require(item_id)
            item["live_status"] = wp_status if exists else "gone"
            if item["state"] in OPEN:
                if applied and item["kind"] in ("staged_edit", "seo_fix"):
                    item["state"] = "published"
                    item["live_status"] = "applied"
                elif wp_status == "publish":
                    item["state"] = "published"
                elif wp_status == "future":
                    item["state"] = "scheduled"
                elif not exists and item["kind"] == "staged_edit":
                    item["state"] = "published"  # the connector's Apply trashes the copy after applying it
                    item["live_status"] = "applied"
            item["updated_at"] = now_iso()
            self.store.write_json(self._path(item_id), item)
        return item

    def mark_applied(self, item_id: str) -> dict:
        """A staged copy that wp_draft apply copied onto its live post (and deleted, read back)."""
        with self.store.lock("desk"):
            item = self.require(item_id)
            if item["kind"] not in ("staged_edit", "seo_fix"):
                raise DeskError("only a staged copy can be applied")
            item["state"], item["live_status"], item["updated_at"] = "applied", "applied", now_iso()
            self.store.write_json(self._path(item_id), item)
        return item

    def read_back(self, client: Any, item: dict) -> dict:
        """Ask WordPress for the item's post now and record it (sync_live). Raises WPError except 404/410."""
        from .client import WPError
        route = f"/wp-json/wp/v2/{item['wp_type']}s/{int(item['wp_id'])}"
        kw = {"context": "edit"} if client.authenticated else {}
        try:
            try:
                post = client.get(route, _fields="id,status,meta", **kw)
            except WPError as e:
                # A drafts-only key may not open a post in edit context once someone published it:
                # the public view of the same route still answers with its status.
                if not kw or e.kind not in ("forbidden", "auth"):
                    raise
                post = client.get(route, _fields="id,status,meta")
        except WPError as e:
            if e.kind == "not_found" or (e.kind == "http" and e.status == 410):
                return self.sync_live(item["id"], None, exists=False)
            raise
        meta = post.get("meta") if isinstance(post.get("meta"), dict) else {}
        return self.sync_live(item["id"], post.get("status"), applied=bool(meta.get("_wpbruecke_applied")))

    def counts(self, site: str = "") -> dict:
        items = self.list(site=site, open_only=True)
        kinds = {"drafts": ("new_post", "new_page"), "staged": ("staged_edit",), "seo": ("seo_fix",),
                 "build": ("build_page",)}
        out = {"all": len(items)}
        for label, ks in kinds.items():
            out[label] = sum(1 for i in items if i.get("kind") in ks)
        out["waiting"] = sum(1 for i in items if i.get("state") in ("waiting", "publish_requested"))
        return out
