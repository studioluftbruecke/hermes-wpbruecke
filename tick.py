"""The tick: read-only watchers and the digest, for a no-agent Hermes cron job (F13).

``hermes wpbruecke tick`` refreshes every site's facts (forced), compares them with the last tick and
prints only what changed since the last tick: a site went down or came back, its home page broke
(health.py: once, again after 6 h, then "back"; maintenance mode only when the next tick still sees
it), REST got slow, an update offer appeared whose version was not announced before (the first tick
of a site prints one summary of what is pending), more items wait on the desk, a site stopped
because its WordPress user's rights changed (the role is read again every tick; a stopped site reads
only what the public sees). A clean tick prints nothing, so the no-agent cron job delivers nothing.
An incomplete update read (api.wordpress.org failed) keeps what was announced and prints nothing.
It also reads back the live status of open desk items (a post the person published in WordPress
shows as published on the desk). It never writes to WordPress. Overlapping ticks (cron and a manual
run) are serialised by the ``tick`` lock; a busy tick returns at once.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Optional

from . import health as health_mod
from .client import WPError
from .desk import OPEN, Desk
from .facts import offer_name
from .store import Busy

SLOW_FLOOR_MS = 1500
SLOW_FACTOR = 3.0
STILL_DOWN_S = 6 * 3600
STILL_BROKEN_S = STILL_DOWN_S
NAMES_SHOWN = 6


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


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M UTC", time.gmtime(t))


def _names(offers: list) -> str:
    names = [offer_name(o) for o in offers]
    more = len(names) - NAMES_SHOWN
    return ", ".join(names[:NAMES_SHOWN]) + (f", {more} more" if more > 0 else "")


def _updates(cur: dict, prev: dict, ups: Optional[dict], label: str, digest: bool, lines: list) -> None:
    """Offers by key: announce a key whose offered version differs from the one announced before.
    A state without ``baseline_sent`` (a new site, or one watched by 0.2.x) gets one summary instead."""
    cur["offers"] = dict(prev.get("offers") or {})
    cur["reported"] = dict(prev.get("reported") or {})
    cur["baseline_sent"] = bool(prev.get("baseline_sent"))
    if not isinstance(ups, dict) or "offers" not in ups or not ups.get("complete", True):
        return
    offers = list(ups["offers"])
    cur["offers"] = {o["key"]: o["offered"] for o in offers}
    if not cur["baseline_sent"]:
        if digest and offers:
            lines.append(f"{label}: {len(offers)} update(s) pending ({_names(offers)}); you update in wp-admin")
        cur["reported"].update(cur["offers"])
        cur["baseline_sent"] = True
        return
    new = [o for o in offers if cur["reported"].get(o["key"]) != o["offered"]]
    if new and digest:
        lines.append(f"{label}: {len(new)} update(s) available ({_names(new)}); you update in wp-admin")
    cur["reported"].update({o["key"]: o["offered"] for o in new})


def _health(cur: dict, prev: dict, health: dict, label: str, now: float, lines: list) -> None:
    """Broken like down (once, again after 6 h, then back); maintenance mode counts as broken only when
    the next tick still sees it, so an update overlapping one tick stays silent."""
    verdict = health.get("verdict") or "unknown"
    keys = ("health", "updating_since", "broken_since", "last_broken_alert")
    if verdict == "unknown":  # REST answered but the home page did not: nothing new is known about it
        cur.update({k: prev.get(k) for k in keys})
        return
    was = prev.get("health") or "ok"
    cur["health"] = verdict
    cur["updating_since"] = (prev.get("updating_since") or now) if verdict == "updating" and was == "updating" else \
        (now if verdict == "updating" else None)
    broken = verdict == "broken" or (verdict == "updating" and was in ("updating", "broken"))
    if broken:
        cur["broken_since"] = prev.get("broken_since") or (cur["updating_since"] if verdict == "updating" else now)
        last = prev.get("last_broken_alert")
        cur["last_broken_alert"] = last
        if last is None or now - float(last) > STILL_BROKEN_S:
            lines.append(f"{label} is broken since {_hhmm(cur['broken_since'])}: {health_mod.reason(health)}")
            cur["last_broken_alert"] = now
        return
    cur["broken_since"], cur["last_broken_alert"] = None, None
    if verdict == "ok" and prev.get("last_broken_alert") is not None:
        lines.append(f"{label} is back: the home page answers HTTP {health_mod.signal(health, 'home_status')} again")


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
                         "on the WPBrücke page" if st.confirmable else
                         f"{label} is stopped: {st.stop_reason}. Nothing keyed runs there until then. {st.restart()}")
        facts = rt.site_facts(site, refresh=True)
        health = facts.get("health") or {"verdict": "unknown", "signals": []}
        # A home page that answers 5xx or the maintenance page is a broken site, not a down one, even
        # when REST fails with it (maintenance mode answers every URL with 503).
        answered = bool(facts.get("reachable")) or health.get("verdict") in ("broken", "updating")
        up = bool(facts.get("reachable"))
        hist = list(prev.get("rest_hist") or [])[-10:]
        rest = facts.get("rest_ms")
        cur = {"reachable": answered, "rest_hist": (hist + [rest])[-10:] if up and rest is not None else hist,
               "updates": (facts.get("updates") or {}).get("count", prev.get("updates")),
               "down_since": prev.get("down_since"), "last_down_alert": prev.get("last_down_alert"), "slow": False,
               "waiting": desk.counts(sid)["waiting"], "effective": st.effective}
        if not answered:
            err = next((e.get("message") for e in facts.get("errors") or [] if e.get("read") == "/wp-json/"), "no answer")
            cur["down_since"] = prev.get("down_since") or now
            # Health is not known while nothing answers: keep the last verdict, so a site flapping between
            # broken and down is not announced as newly broken on every return.
            cur.update({k: prev.get(k) for k in ("health", "updating_since", "broken_since", "last_broken_alert")})
            if prev.get("reachable", True) or now - float(prev.get("last_down_alert") or 0) > STILL_DOWN_S:
                lines.append(f"{label} is down since {_hhmm(cur['down_since'])}: {err}")
                cur["last_down_alert"] = now
        else:
            came_back = bool(prev) and prev.get("reachable") is False
            was_broken = prev.get("last_broken_alert") is not None
            if came_back and health.get("verdict") in ("ok", "unknown") and not (was_broken and health.get("verdict") == "ok"):
                lines.append(f"{label} is back" + (f" (REST {rest} ms)" if rest is not None else ""))
            cur["down_since"] = None
            _health(cur, prev, health, label, now, lines)  # a broken site that was down says "back" once, here
        _updates(cur, prev, facts.get("updates") if up else None, label, digest, lines)
        if up:
            base = _median(hist)
            slow = rest is not None and rest > max(SLOW_FLOOR_MS, SLOW_FACTOR * (base or 0))
            cur["slow"] = bool(slow)
            if digest and prev and slow and not prev.get("slow"):
                lines.append(f"{label} is slow: REST {rest} ms" + (f", usually {int(base)} ms" if base else ""))
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
