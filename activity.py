"""The write log: one JSON line per write WPBrücke does or refuses, per site (plan AE1, D31, A35).

Its first reader is Hermes: ``wp_doctor action=log`` answers "what happened with X" from it. The
page shows the last 50 lines in a closed Activity disclosure. Each line carries the post's ``label``
(title or slug at write time) so a question by name finds it later, after the title changed.

Layout: ``<data dir>/logs/<site>.jsonl``. Appends hold ``Store.lock("logs-<site>")`` and write one
line with one ``write`` on an ``O_APPEND`` descriptor, so the gateway, the plugin host child and the
tick never interleave or lose a line, and a rotation never races an append.

Rotation (D31: 5 MB or 90 days) drops the oldest lines in place; there is no second file. When an
append would take the file past ``max_bytes``, or its first line is older than ``max_age_s``, the
file is rewritten (temp file plus ``os.replace``) with the lines younger than ``max_age_s`` that fit
in ``TRIM_TO`` of ``max_bytes``, newest kept. Readers never see half a file; a reader that meets a
half-written last line skips it.

A line never holds a body, a password, a token or contact data: ``label`` and ``summary`` are short
and every line passes ``redact.scrub`` (preview tokens masked too) before it is written.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import redact
from .store import Store, StoreError, check_id

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_AGE_S = 90 * 86400
TRIM_TO = 0.8
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
LABEL_MAX = 120
SUMMARY_MAX = 240

FIELDS = ("ts", "site", "tool", "action", "wp_type", "wp_id", "label", "desk_item", "lane", "who", "result",
          "code", "summary", "read_back")
RESULTS = ("ok", "refused", "error")
LANES = ("chat", "desk", "automation", "tick")
READ_BACK_KEYS = ("status", "date", "content_hash", "confirmed")
QUERY_KEYS = ("site", "wp_id", "label", "q", "tool", "action", "result", "since", "until", "limit")


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _epoch(ts: Any) -> Optional[float]:
    try:
        return float(calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def parse_when(value: Any, end_of_day: bool = False) -> Optional[float]:
    """``2026-11-25`` or an ISO time (``2026-11-25T10:00``, ``...Z``, ``...+01:00``) -> epoch seconds."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (day + timedelta(days=1 if end_of_day else 0)).timestamp()
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"since/until must be a date (2026-11-25) or an ISO time, got {text[:40]!r}") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _short(value: Any, limit: int) -> Optional[str]:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ActivityLog:
    def __init__(self, store: Store, secrets: Iterable[Optional[str]] = (), clock: Callable[[], float] = time.time,
                 max_bytes: int = MAX_BYTES, max_age_s: float = MAX_AGE_S) -> None:
        self.store, self.secrets, self.clock = store, list(secrets or ()), clock
        self.max_bytes, self.max_age_s = int(max_bytes), float(max_age_s)

    @property
    def dir(self) -> Path:
        path = self.store.root / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, site: str) -> Path:
        return self.dir / f"{check_id(site, 'site id')}.jsonl"

    def sites(self) -> list:
        return sorted(p.stem for p in self.dir.glob("*.jsonl"))

    # --- write ---------------------------------------------------------------------------------
    def line(self, **fields: Any) -> dict:
        """One ActivityLine from a write's facts, scrubbed (preview tokens masked)."""
        fields = {k: redact.scrub_text(v, self.secrets, previews=False) if isinstance(v, str) else v
                  for k, v in fields.items()}  # before any cut, so no half secret is kept
        result = fields.get("result") if fields.get("result") in RESULTS else "error"
        lane = fields.get("lane") if fields.get("lane") in LANES else "chat"
        read_back = fields.get("read_back")
        out = {
            "ts": _iso(self.clock()), "site": str(fields.get("site") or ""),
            "tool": _short(fields.get("tool"), 40) or "?", "action": _short(fields.get("action"), 40) or "?",
            "wp_type": _short(fields.get("wp_type"), 40), "wp_id": _int(fields.get("wp_id")),
            "label": _short(fields.get("label"), LABEL_MAX), "desk_item": _short(fields.get("desk_item"), 80),
            "lane": lane, "who": _short(fields.get("who"), 80) or "agent", "result": result,
            "code": _short(fields.get("code"), 60) if result != "ok" else None,
            "summary": _short(fields.get("summary"), SUMMARY_MAX),
            "read_back": {k: read_back.get(k) for k in READ_BACK_KEYS if read_back.get(k) is not None}
            if isinstance(read_back, dict) and read_back else None,
        }
        return redact.scrub(out, self.secrets, previews=False)

    def append(self, **fields: Any) -> dict:
        line = self.line(**fields)
        path = self.path(line["site"])
        data = (json.dumps(line, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
        with self.store.lock(f"logs-{line['site']}"):
            self._rotate(path, len(data))
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
        return line

    def _rotate(self, path: Path, incoming: int) -> None:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        cutoff = self.clock() - self.max_age_s
        too_big = size + incoming > self.max_bytes
        if not too_big:
            first = _first_ts(path)
            if first is None or first >= cutoff:
                return
        kept, total, budget = [], 0, int(self.max_bytes * TRIM_TO) - incoming
        for raw in reversed(path.read_bytes().splitlines()):
            parsed = _parse(raw)
            if parsed is None:
                continue
            ts = _epoch(parsed.get("ts"))
            if ts is not None and ts < cutoff:
                break
            if total + len(raw) + 1 > budget:
                break
            kept.append(raw)
            total += len(raw) + 1
        Store.write_bytes(path, b"".join(r + b"\n" for r in reversed(kept)))

    # --- read ----------------------------------------------------------------------------------
    def read(self, site: str) -> list:
        try:
            raw = self.path(site).read_bytes()
        except FileNotFoundError:
            return []
        return [p for p in (_parse(r) for r in raw.splitlines()) if p is not None]

    def query(self, query: Optional[dict] = None) -> dict:
        """LogQuery -> {"lines": newest first, "matched": n before the limit, "limit": n}."""
        q = normalise_query(query)
        sites = [q["site"]] if q["site"] else self.sites()
        rows: list = []
        for site in sites:
            for n, line in enumerate(self.read(site)):
                if _matches(line, q):
                    rows.append((_epoch(line.get("ts")) or 0.0, n, line))
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        lines = [redact.scrub(r[2], self.secrets, previews=False) for r in rows[:q["limit"]]]
        return {"lines": lines, "matched": len(rows), "limit": q["limit"]}


def normalise_query(query: Optional[dict]) -> dict:
    query = query if isinstance(query, dict) else {}
    site = str(query.get("site") or "").strip()
    if site:
        check_id(site, "site id")
    wp_id = query.get("wp_id")
    if wp_id not in (None, ""):
        try:
            wp_id = int(wp_id)
        except (TypeError, ValueError):
            raise ValueError("wp_id must be a number") from None
    else:
        wp_id = None
    result = str(query.get("result") or "").strip() or None
    if result and result not in RESULTS:
        raise ValueError(f"result must be one of {', '.join(RESULTS)}")
    try:
        limit = int(query.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise ValueError("limit must be a number") from None
    return {"site": site or None, "wp_id": wp_id, "label": str(query.get("label") or "").strip().lower() or None,
            "q": str(query.get("q") or "").strip().lower() or None, "tool": str(query.get("tool") or "").strip() or None,
            "action": str(query.get("action") or "").strip() or None, "result": result,
            "since": parse_when(query.get("since")), "until": parse_when(query.get("until"), end_of_day=True),
            "limit": max(1, min(MAX_LIMIT, limit))}


def _matches(line: dict, q: dict) -> bool:
    if q["wp_id"] is not None and line.get("wp_id") != q["wp_id"]:
        return False
    if q["label"] and q["label"] not in str(line.get("label") or "").lower():
        return False
    if q["q"]:
        hay = " ".join(str(line.get(k) or "") for k in ("label", "summary", "tool", "action", "code", "desk_item"))
        if q["q"] not in hay.lower():
            return False
    for key in ("tool", "action", "result"):
        if q[key] and line.get(key) != q[key]:
            return False
    ts = _epoch(line.get("ts"))
    if q["since"] is not None and (ts is None or ts < q["since"]):
        return False
    if q["until"] is not None and (ts is None or ts >= q["until"]):
        return False
    return True


def _parse(raw: bytes) -> Optional[dict]:
    try:
        line = json.loads(raw)
    except ValueError:
        return None
    return line if isinstance(line, dict) else None


def _first_ts(path: Path) -> Optional[float]:
    with open(path, "rb") as fh:
        for raw in fh:
            parsed = _parse(raw)
            if parsed is not None:
                return _epoch(parsed.get("ts"))
    return None


def quiet(fn: Callable) -> Callable:
    """For the helpers that build a line from a write's answer: a failure there is logged and swallowed,
    so the log never changes what a write or a refusal answers."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.warning("wpbruecke: no write-log line from %s", fn.__name__, exc_info=True)
            return None
    return wrapper


def record(store: Any, secrets: Iterable[Optional[str]] = (), **fields: Any) -> Optional[dict]:
    """Append one line; never raises, so a log problem never changes a write's answer. A line naming no
    valid site id is not written (there is no site to file it under)."""
    try:
        if not store or not str(fields.get("site") or ""):
            return None
        check_id(fields["site"], "site id")
        return ActivityLog(store, secrets).append(**fields)
    except StoreError:
        return None
    except Exception:
        logger.warning("wpbruecke: the write log did not take a line", exc_info=True)
        return None


def for_runtime(rt: Any, **fields: Any) -> Optional[dict]:
    """``record`` with the runtime's store and its current secrets."""
    return record(getattr(rt, "store", None), redact.known_secrets(getattr(rt, "get_secret", None)), **fields)
