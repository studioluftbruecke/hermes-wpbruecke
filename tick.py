"""The tick: read-only watchers and the digest, for a no-agent Hermes cron job (F13).

``hermes wpbruecke tick`` refreshes every site's facts (forced), compares them with the last tick and
prints only what changed since the last tick (the first tick of a site sets the baseline): a site went
down or came back, REST got slow, new updates appeared, more items wait on the desk, a site stopped
because its WordPress user's rights changed (the role is read again every tick; a stopped site reads
only what the public sees). A clean tick prints nothing, so the no-agent cron job delivers nothing.
It also reads back the live status of open desk items (a post the person published in WordPress
shows as published on the desk). It never writes to WordPress. Overlapping ticks (cron and a manual
run) are serialised by the ``tick`` lock; a busy tick returns at once.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Optional

from .client import WPError
from .desk import OPEN, Desk
from .store import Busy

SLOW_FLOOR_MS = 1500
SLOW_FACTOR = 3.0
STILL_DOWN_S = 6 * 3600


def _median(values: list) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(vals) if vals else None


def run_tick(rt: Any, now: Optional[float] = None, digest: Optional[bool] = None) -> dict:
    now = time.time() if now is None else now
    digest = rt.settings.digest if digest is None else digest
    try:
        with rt.store.lock("tick", blocking=False):
            return _tick(rt, now, digest)
    except Busy:
        return {"ok": True, "busy": True, "lines": [], "message": "another tick is running"}


def _tick(rt: Any, now: float, digest: bool) -> dict:
    path = rt.store.path("tick", "state.json")
    state = rt.store.read_json(path, {}) or {}
    desk = Desk(rt.store)
    lines, sites_seen = [], []
    for site in rt.sites.all():
        if "error" in site:
            lines.append(f"{site['id']}: site file does not validate ({site['error']})")
            continue
        sid = site["id"]
        sites_seen.append(sid)
        prev = state.get(sid) or {}
        st = rt.standing(site, refresh=True)  # the role read again every tick; a stopped site reads only public
        label = site.get("label") or sid
        if st.effective == "stopped" and prev.get("effective") != "stopped":
            lines.append(f"{label} is stopped: {st.stop_reason}. Nothing keyed runs there until you confirm again "
                         "on the WPBrücke page")
        facts = rt.site_facts(site, refresh=True)
        up = bool(facts.get("reachable"))
        hist = list(prev.get("rest_hist") or [])[-10:]
        rest = facts.get("rest_ms")
        cur = {"reachable": up, "rest_hist": (hist + [rest])[-10:] if up and rest is not None else hist,
               "updates": (facts.get("updates") or {}).get("count"), "down_since": prev.get("down_since"),
               "last_down_alert": prev.get("last_down_alert"), "slow": False,
               "waiting": desk.counts(sid)["waiting"], "effective": st.effective}
        if not up:
            err = next((e.get("message") for e in facts.get("errors") or [] if e.get("read") == "/wp-json/"), "no answer")
            cur["down_since"] = prev.get("down_since") or now
            if prev.get("reachable", True) or now - float(prev.get("last_down_alert") or 0) > STILL_DOWN_S:
                since = time.strftime("%H:%M UTC", time.gmtime(cur["down_since"]))
                lines.append(f"{label} is down since {since}: {err}")
                cur["last_down_alert"] = now
        else:
            if prev and prev.get("reachable") is False:
                lines.append(f"{label} is back (REST {rest} ms)")
            cur["down_since"] = None
            base = _median(hist)
            slow = rest is not None and rest > max(SLOW_FLOOR_MS, SLOW_FACTOR * (base or 0))
            cur["slow"] = bool(slow)
            if digest and prev and slow and not prev.get("slow"):
                lines.append(f"{label} is slow: REST {rest} ms" + (f", usually {int(base)} ms" if base else ""))
            if digest and prev and cur["updates"] and cur["updates"] > int(prev.get("updates") or 0):
                ups = facts.get("updates") or {}
                names = [p["slug"] for p in ups.get("plugins") or []] + [t["slug"] for t in ups.get("themes") or []]
                if ups.get("core"):
                    names.insert(0, f"WordPress {ups['core']['latest']}")
                lines.append(f"{label}: {cur['updates']} update(s) available ({', '.join(names[:6])}); you update in wp-admin")
            for item in desk.list(site=sid, open_only=True):
                try:
                    desk.read_back(rt.client(site), item)
                except WPError:
                    pass
            cur["waiting"] = desk.counts(sid)["waiting"]
            if digest and prev and cur["waiting"] > int(prev.get("waiting") or 0):
                lines.append(f"{label}: {cur['waiting']} item(s) wait for you on the review desk")
        state[sid] = cur
    for gone in set(state) - set(sites_seen):
        state.pop(gone, None)
    rt.store.write_json(path, state)
    return {"ok": True, "busy": False, "lines": lines, "sites": len(sites_seen), "clean": not lines}


def format_text(report: dict) -> str:
    if report.get("busy") or not report.get("lines"):
        return ""
    return "WPBrücke:\n" + "\n".join(f"- {l}" for l in report["lines"])
