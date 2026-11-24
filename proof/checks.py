"""Proof checks: turn a capture into verdicts, and one proof run into a stored ProofRun.

Every row's verdict is ``match``, ``differs`` or ``not_proven``; a ``not_proven`` names its reason in
``detail``. ``overall()`` uses only the checks marked ``counts``: the pixel diff (a staged change is
meant to differ) and the local speed measurement (one machine, one load) are shown, not judged.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..store import Store, check_id, new_id, now_iso
from . import runner

CHECKS: dict[str, dict] = {
    "render": {
        "label": "Page renders",
        "method": "HTTP status of the page and a full-page screenshot at every width in Hermes' Chromium",
        "not_proven_reason": "the browser could not load the page",
        "weight": 25, "counts": True,
    },
    "console_errors": {
        "label": "No console errors or failed requests",
        "method": "console.error, uncaught exceptions and browser log errors, plus requests to the site's "
                  "own host that failed or answered 400 and above",
        "not_proven_reason": "no width was captured",
        "weight": 15, "counts": True,
    },
    "overflow": {
        "label": "No horizontal scrolling",
        "method": "document width minus viewport width at every width",
        "not_proven_reason": "the page width could not be measured",
        "weight": 15, "counts": True,
    },
    "broken_links": {
        "label": "Links on the site answer",
        "method": "HEAD request from inside the page for up to 30 links to the site's own host",
        "not_proven_reason": "the links were not checked",
        "weight": 15, "counts": True,
    },
    "alt_text": {
        "label": "Images have alt text",
        "method": "alt attribute of every image on the page (alt=\"\" marks a decorative image)",
        "not_proven_reason": "the images could not be read",
        "weight": 10, "counts": True,
    },
    "contrast": {
        "label": "Text contrast (WCAG AA)",
        "method": "text colour against its effective background colour, 4.5:1 (3:1 for large text); "
                  "text on background images is not measured",
        "not_proven_reason": "no text whose colours could be read",
        "weight": 10, "counts": True,
    },
    "diff": {
        "label": "Change against before",
        "method": "share of pixels that changed between the before and after screenshots (Pillow)",
        "not_proven_reason": "no before screenshot to compare against",
        "weight": 5, "counts": False,
    },
    "speed_local": {
        "label": "Speed (local measurement)",
        "method": "LCP and CLS from the page's own performance entries in Hermes' Chromium; one load "
                  "on this machine, not field data (poor: LCP over 4 s or CLS over 0.25)",
        "not_proven_reason": "the browser reported no largest contentful paint",
        "weight": 5, "counts": False,
    },
}

DIFF_SAME_PCT = 0.1
LCP_POOR_MS = 4000
CLS_POOR = 0.25


def _row(check: str, verdict: str, detail: str, width: int | None = None) -> dict:
    if verdict not in ("match", "differs", "not_proven"):
        raise ValueError(verdict)
    if not detail:
        detail = CHECKS[check]["not_proven_reason"]
    row = {"check": check, "verdict": verdict, "detail": detail}
    if width is not None:
        row["width"] = width
    return row


def _widths(capture: dict | None) -> dict[int, dict]:
    out = {}
    for key, row in ((capture or {}).get("widths") or {}).items():
        try:
            out[int(key)] = row or {}
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items(), key=lambda kv: -kv[0]))


def _short(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _list(items: list[str], n: int = 3) -> str:
    shown = ", ".join(items[:n])
    return shown + (f" and {len(items) - n} more" if len(items) > n else "")


def evaluate(capture: dict, before: dict | None = None, diffs: dict | None = None) -> list[dict]:
    """Rows for every check in CHECKS, from a capture() result (also one read back from JSON)."""
    widths = _widths(capture)
    error = (capture or {}).get("error")
    blocked = list((capture or {}).get("blocked_hosts") or [])
    rows: list[dict] = []

    # render
    for w, r in widths.items():
        status, file = r.get("status"), r.get("file")
        if r.get("error"):
            rows.append(_row("render", "not_proven", r["error"], w))
        elif isinstance(status, int) and status >= 400:
            rows.append(_row("render", "differs", f"the page answered HTTP {status}", w))
        elif not file or not status:
            rows.append(_row("render", "not_proven",
                             "no screenshot or no HTTP status was recorded for this width", w))
        else:
            detail = f"HTTP {status}, {w}×{r.get('height')} px screenshot"
            if r.get("load_ms") is not None:
                detail += f", loaded in {r['load_ms']} ms"
            page_h = r.get("page_height") or 0
            if page_h > (r.get("height") or 0):
                detail += f" (cut at {r.get('height')} of {page_h} px)"
            if blocked:
                detail += (f"; requests to {_list(blocked)} were blocked (only the site and its "
                           f"extra hosts are reachable; add a host to proof.extra_hosts if the page "
                           f"needs it)")
            rows.append(_row("render", "match", detail, w))
    if error or not widths:
        rows.append(_row("render", "not_proven", error or CHECKS["render"]["not_proven_reason"]))

    measured = {w: r for w, r in widths.items() if not r.get("error") and r.get("file")}
    if not measured:
        why = error or "no width was captured"
        for key in ("console_errors", "overflow", "broken_links", "alt_text", "contrast"):
            rows.append(_row(key, "not_proven", f"{CHECKS[key]['not_proven_reason']}: {why}"))
    else:
        rows += _console_rows(measured, blocked)
        for w, r in measured.items():
            px = r.get("overflow_px")
            if px is None:
                rows.append(_row("overflow", "not_proven", CHECKS["overflow"]["not_proven_reason"], w))
            elif px > 1:
                rows.append(_row("overflow", "differs",
                                 f"the page is {px} px wider than the {w} px viewport, so it scrolls "
                                 f"sideways", w))
            else:
                rows.append(_row("overflow", "match", f"no horizontal scrolling at {w} px", w))
        rows.append(_links_row(measured))
        rows.append(_alt_row(measured))
        rows.append(_contrast_row(measured))

    rows += _diff_rows(widths, before, diffs)
    rows.append(_speed_row(measured))
    return rows


def _console_rows(measured: dict[int, dict], blocked: list[str]) -> list[dict]:
    errors: list[str] = []
    failed: list[str] = []
    for r in measured.values():
        for e in r.get("console_errors") or []:
            if e not in errors:
                errors.append(e)
        for f in r.get("failed_requests") or []:
            if f.get("host") in blocked:
                continue
            text = f"{f.get('url')} ({f.get('error')})"
            if text not in failed:
                failed.append(text)
    widths = ", ".join(str(w) for w in measured)
    if not errors and not failed:
        return [_row("console_errors", "match",
                     f"no console errors and no failed requests at {widths} px")]
    parts = []
    if errors:
        parts.append(f"{len(errors)} console error{'s' if len(errors) != 1 else ''} "
                     f"(first: {_short(errors[0])})")
    if failed:
        parts.append(f"{len(failed)} failed request{'s' if len(failed) != 1 else ''} "
                     f"({_list([_short(f, 80) for f in failed], 2)})")
    return [_row("console_errors", "differs", "; ".join(parts))]


def _links_row(measured: dict[int, dict]) -> dict:
    checked_any = any(r.get("links_checked", bool(r.get("links"))) for r in measured.values())
    links: dict[str, dict] = {}
    for r in measured.values():
        for link in r.get("links") or []:
            links.setdefault(link.get("href"), link)
    if not checked_any:
        return _row("broken_links", "not_proven", CHECKS["broken_links"]["not_proven_reason"])
    same = [l for l in links.values() if l.get("same", True) and (l.get("status") is not None or l.get("error"))]
    other = [l for l in links.values() if l.get("same") is False]
    broken = [l for l in same if l.get("status") is None or int(l["status"]) >= 400]
    note = (f"; {len(other)} link{'s' if len(other) != 1 else ''} to other hosts not checked (the proof "
            f"browser reaches only the site)") if other else ""
    if broken:
        shown = [f"{l['href']} ({l.get('status') or l.get('error') or 'no answer'})" for l in broken]
        return _row("broken_links", "differs",
                    f"{len(broken)} of {len(same)} links on the site do not answer: {_list(shown)}{note}")
    if not same:
        return _row("broken_links", "match", f"no links to the site's own pages{note}")
    return _row("broken_links", "match",
                f"{len(same)} link{'s' if len(same) != 1 else ''} on the site answered{note}")


def _alt_row(measured: dict[int, dict]) -> dict:
    images: dict[str, dict] = {}
    for r in measured.values():
        for img in r.get("images") or []:
            images.setdefault(img.get("src") or f"#{len(images)}", img)
    if not images:
        return _row("alt_text", "match", "no images on the page")
    missing = [src for src, img in images.items() if img.get("alt") is None]
    decorative = sum(1 for img in images.values() if img.get("alt") == "")
    if missing:
        return _row("alt_text", "differs",
                    f"{len(missing)} of {len(images)} images have no alt attribute: "
                    f"{_list([_short(m, 80) for m in missing])}")
    extra = f" ({decorative} marked decorative with alt=\"\")" if decorative else ""
    return _row("alt_text", "match", f"all {len(images)} images have alt text{extra}")


def _contrast_row(measured: dict[int, dict]) -> dict:
    failing: dict[str, dict] = {}
    checked = unknown = 0
    for r in measured.values():
        checked = max(checked, int(r.get("contrast_checked") or 0))
        unknown = max(unknown, int(r.get("contrast_unknown") or 0))
        for c in r.get("contrast") or []:
            key = c.get("text", "")
            if key not in failing or c.get("ratio", 99) < failing[key].get("ratio", 99):
                failing[key] = c
    skipped = f"; {unknown} on background images not measured" if unknown else ""
    if failing:
        worst = sorted(failing.values(), key=lambda c: c.get("ratio", 99))
        shown = [f"\"{_short(c.get('text', ''), 40)}\" {c.get('ratio')}:1" for c in worst]
        return _row("contrast", "differs",
                    f"{len(worst)} text element{'s' if len(worst) != 1 else ''} below WCAG AA: "
                    f"{_list(shown)}{skipped}")
    if not checked:
        return _row("contrast", "not_proven", f"{CHECKS['contrast']['not_proven_reason']}{skipped}")
    return _row("contrast", "match",
                f"{checked} text elements at or above 4.5:1 (3:1 for large text){skipped}")


def _diff_rows(widths: dict[int, dict], before: dict | None, diffs: dict | None) -> list[dict]:
    if not diffs:
        reason = CHECKS["diff"]["not_proven_reason"]
        if before and before.get("error"):
            reason = f"the before page could not be captured: {before['error']}"
        return [_row("diff", "not_proven", reason)]
    rows = []
    norm = {int(k): v for k, v in diffs.items()}
    for w in sorted(norm, reverse=True):
        d = norm[w] or {}
        if d.get("error") or d.get("pct") is None:
            rows.append(_row("diff", "not_proven", d.get("error") or CHECKS["diff"]["not_proven_reason"], w))
        elif d["pct"] < DIFF_SAME_PCT:
            rows.append(_row("diff", "match", f"same as before ({d['pct']}% of pixels changed)", w))
        else:
            size = d.get("size")
            grown = ""
            bh = ((before or {}).get("widths") or {})
            b_row = bh.get(w) or bh.get(str(w)) or {}
            a_row = widths.get(w) or {}
            if b_row.get("height") and a_row.get("height") and b_row["height"] != a_row["height"]:
                grown = f"; the page height went from {b_row['height']} to {a_row['height']} px"
            rows.append(_row("diff", "differs",
                             f"{d['pct']}% of pixels changed against before"
                             f"{f' on a {size[0]}×{size[1]} canvas' if size else ''}{grown}", w))
    return rows


def _speed_row(measured: dict[int, dict]) -> dict:
    lcps = [(w, r.get("lcp_ms")) for w, r in measured.items() if r.get("lcp_ms") is not None]
    clss = [(w, r.get("cls")) for w, r in measured.items() if r.get("cls") is not None]
    if not lcps:
        return _row("speed_local", "not_proven", CHECKS["speed_local"]["not_proven_reason"])
    w_lcp, lcp = max(lcps, key=lambda x: x[1])
    w_cls, cls = max(clss, key=lambda x: x[1]) if clss else (None, None)
    detail = (f"local Chromium on this machine, not field data: worst LCP {lcp} ms ({w_lcp} px)"
              + (f", worst CLS {cls} ({w_cls} px)" if cls is not None else ", CLS not reported"))
    poor = lcp > LCP_POOR_MS or (cls is not None and cls > CLS_POOR)
    return _row("speed_local", "differs" if poor else "match", detail)


def overall(checks: list[dict]) -> str:
    """differs if a judged check differs; else not_proven if a judged check is unproven; else match."""
    judged = [c for c in checks if CHECKS.get(c.get("check"), {}).get("counts", True)]
    if not judged:
        return "not_proven"
    if any(c.get("verdict") == "differs" for c in judged):
        return "differs"
    if any(c.get("verdict") != "match" for c in judged):
        return "not_proven"
    return "match"


def _target(target: dict | None, url: str) -> dict:
    t = dict(target or {})
    kind = t.get("kind") or "url"
    if kind not in ("url", "draft", "desk_item"):
        raise ValueError(f"target.kind must be url, draft or desk_item, got {kind!r}")
    return {"kind": kind, "url": t.get("url") or url, "site": t.get("site"),
            "wp_id": t.get("wp_id"), "item": t.get("item")}


def run_proof(*, store: Store, url, host, connect_ip, widths, extra_hosts, chromium_path, target: dict,
              before_url: str | None = None, run_id: str | None = None) -> dict:
    """Capture `url` (and `before_url`), check it, and store the ProofRun under proofs/<id>/.

    Holds the store's Chromium slot for the whole run: a second run in any process gets
    ``store.Busy`` at once. Nothing is written outside the run's folder in the store.
    """
    run_id = check_id(run_id or new_id("proof"), "proof id")
    widths = [int(w) for w in (widths or runner.DEFAULT_WIDTHS)]
    tgt = _target(target, url)
    with store.chromium_slot():
        started_at, t0 = now_iso(), time.monotonic()
        out = store.path("proofs", run_id)
        out.mkdir(parents=True, exist_ok=True)
        common = dict(host=host, connect_ip=connect_ip or "", widths=widths,
                      extra_hosts=tuple(extra_hosts or ()), out_dir=out, chromium_path=chromium_path or "")
        cap = runner.capture(url, **common)
        before = runner.capture(before_url, prefix="before", **common) if before_url else None
        diffs = _diffs(out, cap, before) if before else None
        checks = evaluate(cap, before, diffs)
        before_widths = _widths(before)
        shots = []
        for w, r in _widths(cap).items():
            shot = {"width": w, "file": r.get("file"), "bytes": r.get("bytes", 0),
                    "height": r.get("height", 0), "load_ms": r.get("load_ms"), "status": r.get("status")}
            if before is not None:
                shot["before_file"] = (before_widths.get(w) or {}).get("file")
            shots.append(shot)
        console: list[str] = []
        for r in _widths(cap).values():
            for e in r.get("console_errors") or []:
                if e not in console:
                    console.append(e)
        blocked = list(cap.get("blocked_hosts") or [])
        for h in (before or {}).get("blocked_hosts") or []:
            if h not in blocked:
                blocked.append(h)
        run = {
            "id": run_id, "target": tgt, "widths": widths, "started_at": started_at,
            "finished_at": now_iso(), "seconds": round(time.monotonic() - t0, 2),
            "chromium": cap.get("chromium"), "verdict": overall(checks), "shots": shots,
            "checks": checks,
            "diff": [{"width": int(w), "pct": d.get("pct")} for w, d in (diffs or {}).items()
                     if d.get("pct") is not None],
            "blocked_hosts": blocked, "console_errors": console,
            "perf": {str(w): {"load_ms": r.get("load_ms"), "lcp_ms": r.get("lcp_ms"), "cls": r.get("cls")}
                     for w, r in _widths(cap).items()},
        }
        if cap.get("error"):
            run["error"] = cap["error"]
        if before is not None:
            run["before"] = {"url": before_url, "error": before.get("error")}
        store.write_json(out / "run.json", run)
        return run


def _diffs(out: Path, cap: dict, before: dict) -> dict:
    after_w, before_w = _widths(cap), _widths(before)
    result: dict[int, dict] = {}
    for w, r in after_w.items():
        a, b = r.get("file"), (before_w.get(w) or {}).get("file")
        if not a or not b:
            result[w] = {"error": "a screenshot is missing for this width"}
            continue
        try:
            from . import diff
            result[w] = diff.diff_png(out / a, out / b)
        except ImportError:
            result[w] = {"error": "Pillow is not installed, so the pixel diff cannot run"}
        except OSError as exc:
            result[w] = {"error": f"the screenshots could not be compared: {exc}"}
    return result
