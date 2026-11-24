"""Route handlers for the WPBrücke page (``dashboard/plugin_api.py`` adapts them to FastAPI).

Every handler returns ``(status, body)``. Config and key problems answer 200 ``{ok: false, error,
message, setting}``; refused clicks 403 or 409. The Desktop half reads the same shapes.
The routes are the person's click lane: they mark items
reviewed and ask for changes, but publishing happens only on the connector's review screen in
wp-admin on the person's own WordPress login (D9 a); no route publishes and no route holds a key
that can.
"""
from __future__ import annotations

import base64
import calendar
import threading
import time
from typing import Any, Callable, Optional

from . import features as features_mod
from .client import WPError
from .desk import OPEN, Desk, DeskError
from .runtime import NotFound, Runtime
from .sites import SiteError
from .store import Busy, StoreError, now_iso

SETTINGS_URL = "/settings?tab=plugins&agent=wpbruecke"
JOB_STALE_S = 600
SLOW_MS = 1500


def _err(status: int, code: str, message: str, setting: str = "") -> "tuple[int, dict]":
    body = {"ok": False, "error": code, "message": message}
    if setting:
        body["setting"] = setting
    return status, body


def _mark(site: dict, label: str) -> str:
    words = [w for w in (label or site["id"]).replace("-", " ").replace(".", " ").split() if w]
    return ((words[0][0] + (words[1][0] if len(words) > 1 else words[0][1:2])) if words else "WP").upper()


def _site_row(rt: Runtime, site: dict, desk: Desk) -> dict:
    from .tools.sites import overview_row
    row = overview_row(rt, site)
    row["desk_waiting"] = desk.counts(site["id"])["waiting"]
    row["mark"] = _mark(site, row.get("label") or "")
    row["wp_admin_url"] = site["url"].rstrip("/") + "/wp-admin/"
    from .sites import cockpit_url
    row["cockpit_url"] = cockpit_url(site)
    return row


def overview(rt: Runtime) -> "tuple[int, dict]":
    desk = Desk(rt.store)
    sites = [s for s in rt.sites.all() if "error" not in s]
    rows = [_site_row(rt, s, desk) for s in sites]
    needs = []
    for row in rows:
        if not row["reachable"]:
            needs.append({"kind": "down", "site": row["id"], "title": f"{row['label']} does not answer",
                          "detail": "The site did not answer its REST index.", "when": "now", "urgent": True,
                          "go": {"view": "settings", "site": row["id"]}})
            continue
        if row["desk_waiting"]:
            needs.append({"kind": "desk", "site": row["id"], "title": f"{row['desk_waiting']} item(s) ready for review",
                          "detail": f"On {row['label']}: drafts and staged changes with their proof.", "when": "today",
                          "urgent": False, "go": {"view": "desk", "site": row["id"]}})
        if row.get("noindex"):
            needs.append({"kind": "seo", "site": row["id"], "title": "Search engines are told to stay away",
                          "detail": "Settings → Reading → \"Discourage search engines\" is on, so there is no sitemap.",
                          "when": "now", "urgent": True, "go": {"view": "seo", "site": row["id"]}})
        if row.get("updates"):
            needs.append({"kind": "updates", "site": row["id"], "title": f"{row['updates']} update(s) on {row['label']}",
                          "detail": "Versions compared with wordpress.org; you update in wp-admin.", "when": "since last check",
                          "urgent": False, "go": {"view": "site", "site": row["id"]}})
        if row.get("rest_ms") and row["rest_ms"] > SLOW_MS:
            needs.append({"kind": "slow", "site": row["id"], "title": f"{row['label']} is slow",
                          "detail": f"REST {row['rest_ms']} ms.", "when": "now", "urgent": False,
                          "go": {"view": "site", "site": row["id"]}})
        if row["access"] == "not set up":
            needs.append({"kind": "setup", "site": row["id"], "title": f"Add a key for {row['label']}",
                          "detail": "Public facts only until a key slot is set.", "when": "", "urgent": False,
                          "go": {"view": "settings", "site": row["id"]}})
    counts = desk.counts()
    strip = {"sites": len(rows), "keyed": sum(1 for r in rows if r["access"] not in ("not set up", "key not working")),
             "waiting": counts["waiting"], "updates": sum(int(r.get("updates") or 0) for r in rows),
             "slow": sum(1 for r in rows if (r.get("rest_ms") or 0) > SLOW_MS)}
    mode = "none" if not rows else ("one" if len(rows) == 1 else "many")
    return 200, {"ok": True, "mode": mode, "desk_waiting": counts["waiting"], "sites": rows, "needs_you": needs,
                 "strip": strip, "generated_at": now_iso()}


def site_detail(rt: Runtime, site_id: str) -> "tuple[int, dict]":
    from . import yamlish
    from .sites import HEADER
    try:
        site = rt.site(site_id)
    except NotFound as e:
        return _err(404, "not_found", str(e))
    facts = rt.site_facts(site)
    ev = features_mod.evaluate(site, facts)
    public = {k: v for k, v in facts.items() if not k.startswith("_")}
    return 200, {"ok": True, "site": site, "facts": public, "rights": features_mod.rights_words(facts),
                 "available": [f["feature"] for f in ev["available"]], "not_available": ev["unavailable"],
                 "versions": rt.sites.versions(site_id), "yaml": yamlish.dump(site, HEADER), "settings_url": SETTINGS_URL}


def site_create(rt: Runtime, body: dict) -> "tuple[int, dict]":
    try:
        res = rt.sites.create((body or {}).get("fields") or {}, "page")
    except SiteError as e:
        return _err(400, "invalid", str(e))
    return 200, {"ok": True, "site": res["site"], "diff": res["diff"], "version": res["version"], "changed": True}


def site_patch(rt: Runtime, site_id: str, body: dict) -> "tuple[int, dict]":
    changes = (body or {}).get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        return _err(400, "bad_args", "changes: {field: value} needed")
    try:
        rt.site(site_id)
        res = rt.sites.update(site_id, changes, "page", preview=bool((body or {}).get("preview")))
    except NotFound as e:
        return _err(404, "not_found", str(e))
    except SiteError as e:
        return _err(400, "invalid", str(e))
    return 200, {"ok": True, **res}


def site_history(rt: Runtime, site_id: str) -> "tuple[int, dict]":
    try:
        rt.site(site_id)
    except NotFound as e:
        return _err(404, "not_found", str(e))
    return 200, {"ok": True, "versions": rt.sites.versions(site_id)}


# --- desk ---------------------------------------------------------------------------------------

def _proof(rt: Runtime, proof_id: Optional[str]) -> Optional[dict]:
    if not proof_id:
        return None
    try:
        return rt.store.read_json(rt.store.path("proofs", f"{proof_id}/run.json"), None)
    except StoreError:
        return None


def _seo(rt: Runtime, seo_id: Optional[str]) -> Optional[dict]:
    if not seo_id:
        return None
    try:
        return rt.store.read_json(rt.store.path("seo", f"{seo_id}.json"), None)
    except StoreError:
        return None


def _data_url(rt: Runtime, proof_id: str, file: Optional[str], width: int = 360) -> Optional[str]:
    if not file:
        return None
    try:
        path = rt.store.path("proofs", f"{proof_id}/{file}")
        from .proof import diff
        data = diff.thumbnail(path, width=width)
    except Exception:
        return None
    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _chips(proof: Optional[dict]) -> list:
    if not proof:
        return [{"verdict": "not_proven", "text": "no proof run yet"}]
    return [{"verdict": c.get("verdict"), "text": c.get("detail") or c.get("check")} for c in (proof.get("checks") or [])][:6]


def desk_list(rt: Runtime, site: str = "", kind: str = "") -> "tuple[int, dict]":
    desk = Desk(rt.store)
    kinds = {"drafts": ("new_post", "new_page"), "staged": ("staged_edit",), "seo": ("seo_fix",), "build": ("build_page",)}
    items = [i for i in desk.list(site=site, open_only=False) if i.get("state") != "discarded"]
    if kind:
        items = [i for i in items if i.get("kind") in kinds.get(kind, (kind,))]
    out = []
    for i in items:
        proof = _proof(rt, i.get("proof_id"))
        shot = next((s for s in (proof or {}).get("shots") or [] if s.get("width") == max(
            [s2.get("width", 0) for s2 in (proof or {}).get("shots") or []] or [0])), None)
        out.append({**i, "thumb": _data_url(rt, i["proof_id"], shot.get("file")) if proof and shot else None,
                    "proof_verdict": (proof or {}).get("verdict") or "not_proven", "chips": _chips(proof)})
    return 200, {"ok": True, "items": out, "counts": desk.counts(site)}


def _publishing(rt: Runtime, item: dict) -> dict:
    try:
        site = rt.site(item["site"])
    except NotFound:
        return {"rule": True, "setting": "off", "can_link": False, "why": "the site file is gone"}
    rule = bool(site.get("never_publish_never_send", True))
    setting = site.get("publishing") or "off"
    if rule:
        return {"rule": True, "setting": setting, "can_link": False,
                "why": "\"Never publish, never send\" is on for this site, so only the WordPress link is offered."}
    if setting == "off":
        return {"rule": False, "setting": setting, "can_link": False,
                "why": "Publishing is off for this site: publish in WordPress."}
    facts = rt.site_facts(site)
    if not (facts.get("connector") or facts.get("connector_ns")):
        return {"rule": False, "setting": setting, "can_link": False,
                "why": "The review screen needs the WPBrücke connector; the WordPress editor link works without it."}
    return {"rule": False, "setting": setting, "can_link": True,
            "why": "Opens the connector's review screen in WordPress, where your own login publishes."}


def _read_back(rt: Runtime, item: dict) -> dict:
    """An open item's live status from WordPress (a publish on the review screen shows at once)."""
    if item.get("state") not in OPEN:
        return item
    try:
        return Desk(rt.store).read_back(rt.client(rt.site(item["site"])), item)
    except (WPError, NotFound, DeskError, StoreError):
        return item


def desk_item(rt: Runtime, item_id: str) -> "tuple[int, dict]":
    try:
        item = _read_back(rt, Desk(rt.store).require(item_id))
    except DeskError as e:
        return _err(404, "not_found", str(e))
    proof = _proof(rt, item.get("proof_id"))
    shots = {}
    for s in (proof or {}).get("shots") or []:
        shots[str(s.get("width"))] = {"after": _data_url(rt, item["proof_id"], s.get("file"), 720),
                                      "before": _data_url(rt, item["proof_id"], s.get("before_file"), 720)}
    return 200, {"ok": True, "item": item, "proof": proof, "seo": _seo(rt, item.get("seo_id")), "shots": shots,
                 "publishing": _publishing(rt, item)}


def desk_review(rt: Runtime, item_id: str) -> "tuple[int, dict]":
    try:
        return 200, {"ok": True, "item": Desk(rt.store).move(item_id, "review", "person")}
    except DeskError as e:
        return _err(409, "refused", str(e))


def desk_changes(rt: Runtime, item_id: str, body: dict) -> "tuple[int, dict]":
    note = str((body or {}).get("note") or "").strip()
    try:
        item = Desk(rt.store).move(item_id, "changes", "person", note)
    except DeskError as e:
        return _err(409, "refused", str(e))
    text = (f"Please revise @wp:{item['site']}/{item['wp_id']} (desk item {item['id']}, \"{item['title']}\"): "
            f"{note or 'see my note on the desk'}. Update the same draft with wp_draft and run wp_proof again.")
    return 200, {"ok": True, "item": item, "draft_text": text}


def review_link(rt: Runtime, item_id: str, action: str) -> "tuple[int, dict]":
    if action not in ("publish", "schedule", "apply"):
        return _err(400, "bad_args", "action is publish, schedule or apply")
    try:
        item = Desk(rt.store).require(item_id)
        site = rt.site(item["site"])
    except (DeskError, NotFound) as e:
        return _err(404, "not_found", str(e))
    if site.get("never_publish_never_send", True):
        return _err(403, "hard_rule", "\"Never publish, never send\" is on for this site; open the post in the "
                    "WordPress editor instead (item.links.edit).")
    if (site.get("publishing") or "off") == "off":
        return _err(403, "publishing_off", "Publishing is off for this site; open the post in the WordPress editor.")
    if action == "schedule" and site.get("publishing") != "schedule":
        return _err(403, "publishing_off", "Scheduling is not switched on for this site.")
    if action == "apply" and item.get("kind") not in ("staged_edit", "seo_fix"):
        return _err(400, "bad_args", "only a staged copy can be applied")
    facts = rt.site_facts(site)
    if not (facts.get("connector") or facts.get("connector_ns")):
        return 200, {"ok": True, "url": item["links"]["edit"], "kind": "editor",
                     "why": "the connector is not installed, so the WordPress editor opens instead"}
    url = (f"{site['url'].rstrip('/')}/wp-admin/admin.php?page=wpbruecke-review&post={int(item['wp_id'])}"
           f"&wpb_action={action}")
    return 200, {"ok": True, "url": url, "kind": "review"}


# --- jobs ---------------------------------------------------------------------------------------

def _start(rt: Runtime, kind: str, params: dict, work: Callable[[], dict]) -> "tuple[int, dict]":
    job = rt.store.job_create(kind, params)

    def run():
        rt.store.job_update(job["id"], state="running")
        try:
            result = work()
            rt.store.job_update(job["id"], state="done", result=result)
        except Busy as e:
            rt.store.job_update(job["id"], state="failed", error={"error": "busy", "message": str(e)})
        except Exception as e:  # recorded on the job, never raised into the server
            rt.store.job_update(job["id"], state="failed", error={"error": type(e).__name__, "message": str(e)[:300]})
    threading.Thread(target=run, name=f"wpbruecke-{kind}", daemon=True).start()
    return 202, {"ok": True, "job": job}


def job_proof(rt: Runtime, body: dict, runner: Callable[[Runtime, dict], dict]) -> "tuple[int, dict]":
    from . import store as store_mod
    try:
        with rt.store.chromium_slot():
            pass  # probe only: a held slot answers 409 at once instead of queueing behind it
    except store_mod.Busy:
        return 409, {"ok": False, "error": "busy", "message": "A proof is running; try again shortly.", "retry_after_s": 10}
    return _start(rt, "proof", body or {}, lambda: runner(rt, body or {}))


def job_seo_site(rt: Runtime, body: dict, runner: Callable[[Runtime, dict], dict]) -> "tuple[int, dict]":
    if not (body or {}).get("site"):
        return _err(400, "bad_args", "site needed")
    return _start(rt, "seo-site", body, lambda: runner(rt, body))


def job_get(rt: Runtime, job_id: str) -> "tuple[int, dict]":
    try:
        job = rt.store.job_get(job_id)
    except StoreError as e:
        return _err(400, "bad_args", str(e))
    if job is None:
        return _err(404, "not_found", f"no job {job_id}")
    if job["state"] in ("queued", "running"):
        stamp = job.get("updated_at") or job.get("created_at")
        try:
            age = time.time() - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            age = 0
        if age > JOB_STALE_S:
            job = rt.store.job_update(job_id, state="failed", error={"error": "stale", "message": (
                "The job stopped answering (the plugin host restarted); start it again.")})
    return 200, {"ok": True, "job": job}


# --- seo, build, doctor -------------------------------------------------------------------------

def seo_reports(rt: Runtime, site: str, post: str = "") -> "tuple[int, dict]":
    reports = []
    for p in rt.store.dir("seo").glob("*.json"):
        r = rt.store.read_json(p, None)
        if isinstance(r, dict) and r.get("site") == site:
            reports.append(r)
    reports.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    site_report = next((r for r in reports if r.get("scope") == "site"), None)
    posts = [r for r in reports if r.get("scope") == "post" and (not post or str(r.get("wp_id")) == str(post))]
    seen, latest = set(), []
    for r in posts:
        if r.get("wp_id") not in seen:
            seen.add(r.get("wp_id"))
            latest.append(r)
    return 200, {"ok": True, "site_report": site_report, "post_reports": latest}


def build_list(rt: Runtime, site: str = "") -> "tuple[int, dict]":
    runs = [rt.store.read_json(p, None) for p in rt.store.dir("build").glob("*.json")]
    runs = [r for r in runs if isinstance(r, dict) and (not site or r.get("site") == site)]
    return 200, {"ok": True, "runs": sorted(runs, key=lambda r: r.get("created_at") or "", reverse=True)}


def build_get(rt: Runtime, run_id: str) -> "tuple[int, dict]":
    try:
        run = rt.store.read_json(rt.store.path("build", f"{run_id}.json"), None)
    except StoreError as e:
        return _err(400, "bad_args", str(e))
    return (200, {"ok": True, "run": run}) if run else _err(404, "not_found", f"no build run {run_id}")


def build_gate(rt: Runtime, run_id: str, body: dict) -> "tuple[int, dict]":
    from .gp import build as gp_build
    path = rt.store.path("build", f"{run_id}.json")
    with rt.store.lock("build"):
        run = rt.store.read_json(path, None)
        if not run:
            return _err(404, "not_found", f"no build run {run_id}")
        try:
            run = gp_build.gate(run, str((body or {}).get("step") or ""), str((body or {}).get("decision") or ""),
                                by="you", note=str((body or {}).get("note") or ""))
        except ValueError as e:
            return _err(409, "refused", str(e))
        rt.store.write_json(path, run)
    return 200, {"ok": True, "run": run}


def doctor(rt: Runtime, site: str = "") -> "tuple[int, dict]":
    from .tools import doctor as doctor_tool
    return 200, doctor_tool.run(rt, site or None, refresh=False)
