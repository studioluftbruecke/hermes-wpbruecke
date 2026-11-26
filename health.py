"""Health of a site, read from its public home page only (no key, no cookie, no redirect followed).

Paused plugins live in a recovery session and the maintenance switch runs before any plugin loads,
so nothing behind the key can say more than the home page does. WordPress answers both a fatal error
and its maintenance mode with the same ``wp_die`` page (``id="error-page"``, ``wp-die-message``,
both untranslated); maintenance adds ``503`` with ``Retry-After``.

Verdicts: ``updating`` (503 with Retry-After), ``broken`` (any other 5xx, or the error page markup),
``unknown`` (the home page gave no answer), ``ok`` otherwise.
"""
from __future__ import annotations

import re
from typing import Any, Optional

VERDICTS = ("ok", "updating", "broken", "unknown")
_ERROR_PAGE = re.compile(r"""id\s*=\s*["']?error-page\b""", re.I)
_DIE_MESSAGE = re.compile(r"wp-die-message", re.I)
SCAN_BYTES = 256 * 1024


def classify(status: Optional[int], retry_after: Optional[str] = None, text: str = "") -> dict:
    if status is None:
        return {"verdict": "unknown", "signals": []}
    signals: list = [{"name": "home_status", "value": status}]
    if retry_after is not None:
        signals.append({"name": "retry_after", "value": retry_after})
    head = text[:SCAN_BYTES]
    error_page = bool(_ERROR_PAGE.search(head) and _DIE_MESSAGE.search(head))
    if error_page:
        signals.append({"name": "error_page", "value": True})
    if status == 503 and retry_after is not None:
        verdict = "updating"
    elif status >= 500 or error_page:
        verdict = "broken"
    else:
        verdict = "ok"
    return {"verdict": verdict, "signals": signals}


def from_response(resp: Any) -> dict:
    """The Health of one home page answer (a client Response), or ``unknown`` for None."""
    if resp is None:
        return classify(None)
    return classify(resp.status, resp.header("Retry-After"), resp.body[:SCAN_BYTES].decode("utf-8", "replace"))


def signal(health: dict, name: str) -> Any:
    return next((s.get("value") for s in (health or {}).get("signals") or [] if s.get("name") == name), None)


def reason(health: dict) -> str:
    status = signal(health, "home_status")
    page = " with WordPress' error page" if signal(health, "error_page") else ""
    if health.get("verdict") == "updating":
        return f"the home page still answers HTTP {status} (maintenance mode) on a second check; an update may have stopped halfway"
    return f"the home page answers HTTP {status}{page}"


def row(facts: dict, tick_site: Optional[dict]) -> dict:
    """The /overview row's ``health``: the facts' verdict and, when the tick has seen the same state,
    since when."""
    verdict = ((facts or {}).get("health") or {}).get("verdict")
    if verdict not in VERDICTS:
        verdict = "unknown"
    t = tick_site or {}
    since = None
    if verdict == "broken" and t.get("health") in ("broken", "updating"):
        since = t.get("broken_since") or t.get("updating_since")
    elif verdict == "updating" and t.get("health") == "updating":
        since = t.get("updating_since")
    elif verdict == "unknown" and t.get("reachable") is False:
        since = t.get("down_since")
    return {"verdict": verdict, "since": float(since) if since is not None else None}


def for_site(rt: Any, site_id: str) -> dict:
    """The overview row's ``health`` from the cached facts and the tick state (no request)."""
    tick = rt.store.read_json(rt.store.path("tick", "state.json"), {}) or {}
    return row(rt.facts.read(site_id) or {}, tick.get(site_id))
