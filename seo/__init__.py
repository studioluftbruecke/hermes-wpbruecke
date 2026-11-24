"""SEO checks: one post (``post.check_post``), the site (``site.check_site``), and where a meta
description is written (``fields.description_target``).

Every check is a row ``{check, label, verdict, detail, weight}`` with an optional ``fix`` (a change
Hermes can stage as a desk item: ``{kind: description|title|alt|internal_links, value, why}``) or an
optional ``action`` (a person's click outside the desk, e.g. a wp-admin setting). Verdicts are
``match``, ``differs`` or ``not_proven``; a ``not_proven`` row always says why in ``detail``.

Score rule (the same for post and site reports), a plain sum:

- the weights of one report add up to 100;
- a ``match`` row earns its full weight, a ``differs`` row earns 0;
- a ``not_proven`` row is left out: its weight leaves the denominator, and the earned sum is scaled
  back to 100 (``round(100 * earned / proven_weight)``);
- when every row is ``not_proven`` the score is ``None`` (nothing was measured).
"""
from __future__ import annotations

VERDICTS = ("match", "differs", "not_proven")
FIX_KINDS = ("description", "title", "alt", "internal_links")


def row(check: str, label: str, verdict: str, detail: str, weight: int, **extra) -> dict:
    """One check row; refuses an unknown verdict, an unknown fix kind or a silent not_proven."""
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    if not (detail or "").strip():
        raise ValueError(f"check {check!r} has no detail")
    fix = extra.get("fix")
    if fix is not None and fix.get("kind") not in FIX_KINDS:
        raise ValueError(f"unknown fix kind {fix.get('kind')!r}")
    out = {"check": check, "label": label, "verdict": verdict, "detail": detail, "weight": weight}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def score(checks: list) -> int | None:
    """Plain weighted sum, see the module docstring."""
    proven = [c for c in checks if c["verdict"] != "not_proven"]
    total = sum(c["weight"] for c in proven)
    if not total:
        return None
    earned = sum(c["weight"] for c in proven if c["verdict"] == "match")
    return round(100 * earned / total)
