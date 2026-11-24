"""The GeneratePress pack's build run: from a brief to a client handoff, one gated step at a time.

A run (contract shape ``BuildRun``) walks seven steps in order. Each step names who acts (Hermes,
you, the client) and who passes its gate. A step moves ``todo -> running -> waiting`` with
``advance``; at ``waiting`` its gate is open and only ``gate`` moves it on: ``pass`` makes it
``done``, ``changes`` sends it back to ``running`` with the note. A step cannot start before the
step in front of it passed its gate, and a done step never moves again. Nothing here publishes:
pages are drafts on the review desk, going live is the site owner's own click in WordPress.
"""
from __future__ import annotations

import time
import uuid

ACTORS = ("hermes", "you", "client")
MODES = ("generateblocks", "core")

STEPS = {
    "brief": {"label": "Brief", "actors": ["client"], "gate": "you",
              "what": "Who the site is for, what it must have, its tone; who edits it later."},
    "brand": {"label": "Brand", "actors": ["hermes", "you"], "gate": "you",
              "what": "Colours and fonts, staged as GeneratePress global colours for your approval."},
    "sitemap": {"label": "Sitemap", "actors": ["hermes", "client"], "gate": "you",
                "what": "The pages and their order; the client approves, you confirm."},
    "pages": {"label": "Pages as drafts", "actors": ["hermes"], "gate": "you",
              "what": "Every page as a draft on the review desk, each with its proof."},
    "header_footer": {"label": "Header, footer, menu", "actors": ["hermes"], "gate": "you",
                      "what": "Menu through core REST; header and footer as GeneratePress Elements or theme settings."},
    "proof": {"label": "Proof against the brief", "actors": ["hermes"], "gate": "you",
              "what": "Three widths, contrast, links, every page, checked against the brief."},
    "handoff": {"label": "Client handoff", "actors": ["hermes", "you", "client"], "gate": "you",
                "what": "A short guide for the client; going live is your click in WordPress."},
}
ORDER = list(STEPS)


class BuildError(ValueError):
    """A refused transition; ``code`` says which rule (unknown_step, out_of_order, done, state, who)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mode_for(generateblocks_active: bool) -> str:
    """generateblocks when the GenerateBlocks plugin is active on the site, core otherwise."""
    return "generateblocks" if generateblocks_active else "core"


def _brief(brief: dict | None) -> dict:
    b = dict(brief or {})
    must = b.get("must_have") or []
    return {"name": str(b.get("name") or ""), "audience": str(b.get("audience") or ""),
            "must_have": [str(m) for m in (must if isinstance(must, (list, tuple)) else [must])],
            "tone": str(b.get("tone") or "")}


def new_run(site_id: str, brief: dict | None = None, *, mode: str = "core", run_id: str | None = None,
            now: str | None = None) -> dict:
    """A fresh BuildRun. ``mode`` comes from the caller (``mode_for`` of the site's plugins)."""
    if not site_id:
        raise BuildError("site", "a build run needs a site id")
    if mode not in MODES:
        raise BuildError("mode", f"mode must be one of {MODES}")
    at = now or _now()
    steps = [{"key": k, "label": s["label"], "state": "todo", "actors": list(s["actors"]),
              "gate": {"who": s["gate"], "state": "closed", "note": ""}} for k, s in STEPS.items()]
    return {"id": run_id or f"build-{uuid.uuid4().hex[:10]}", "site": site_id, "brief": _brief(brief),
            "brand": {"colors": [], "fonts": {"body": "", "headings": ""}}, "sitemap": [], "steps": steps,
            "pages": [], "created_at": at, "updated_at": at, "mode": mode}


def _step(run: dict, key: str) -> tuple[int, dict]:
    for n, s in enumerate(run["steps"]):
        if s["key"] == key:
            return n, s
    raise BuildError("unknown_step", f"no step {key!r}; steps are {', '.join(ORDER)}")


def current(run: dict) -> str | None:
    """The first step that is not done (None when the run is finished)."""
    return next((s["key"] for s in run["steps"] if s["state"] != "done"), None)


def advance(run: dict, step: str, by: str, note: str = "", now: str | None = None) -> dict:
    """Move a step forward one stage: ``todo -> running``, ``running -> waiting`` (gate opens).
    Refused for a done step, a step whose gate is open, a step before whose predecessor passed its
    gate, and anyone who is neither an actor of the step nor Hermes."""
    n, s = _step(run, step)
    if by not in ACTORS:
        raise BuildError("who", f"by must be one of {ACTORS}")
    if s["state"] == "done":
        raise BuildError("done", f"{s['label']} is done; it does not move again")
    if n and run["steps"][n - 1]["state"] != "done":
        prev = run["steps"][n - 1]
        raise BuildError("out_of_order", f"{s['label']} waits for the gate of {prev['label']}")
    if by not in s["actors"] and by != "hermes":
        raise BuildError("who", f"{by} does not act on {s['label']}")
    if s["state"] == "waiting":
        raise BuildError("state", f"{s['label']} waits at its gate ({s['gate']['who']} decides)")
    if s["state"] == "todo":
        s["state"] = "running"
    else:
        s["state"] = "waiting"
        s["gate"] = {"who": s["gate"]["who"], "state": "open", "note": note}
    run["updated_at"] = now or _now()
    return run


def gate(run: dict, step: str, decision: str, by: str = "you", note: str = "", now: str | None = None) -> dict:
    """Decide an open gate: ``pass`` makes the step done, ``changes`` sends it back to running
    with the note (a note is required then). Only the gate's person decides."""
    _, s = _step(run, step)
    if decision not in ("pass", "changes"):
        raise BuildError("state", "decision must be pass or changes")
    if s["state"] == "done":
        raise BuildError("done", f"{s['label']} is done; its gate was passed")
    if s["state"] != "waiting":
        raise BuildError("state", f"{s['label']} is {s['state']}; its gate is not open yet")
    if by != s["gate"]["who"]:
        raise BuildError("who", f"the gate of {s['label']} is decided by {s['gate']['who']}, not {by}")
    if decision == "changes" and not note.strip():
        raise BuildError("state", "asking for changes needs a note saying what to change")
    if decision == "pass":
        s["state"] = "done"
        s["gate"] = {"who": s["gate"]["who"], "state": "passed", "note": note}
    else:
        s["state"] = "running"
        s["gate"] = {"who": s["gate"]["who"], "state": "changes", "note": note}
    run["updated_at"] = now or _now()
    return run


def add_page(run: dict, item_id: str, now: str | None = None) -> dict:
    """Record a desk item (a page draft) on the run while its pages step is running."""
    _, s = _step(run, "pages")
    if s["state"] != "running":
        raise BuildError("state", "pages are added while the pages step is running")
    if item_id not in run["pages"]:
        run["pages"].append(item_id)
    run["updated_at"] = now or _now()
    return run


def handoff_guide(run: dict, items: list[dict] | tuple = (), site_url: str = "", client: str = "") -> str:
    """The client's handoff guide in plain words (markdown): which pages exist and how to edit
    them in the block editor. ``items`` are the run's desk items (title, links.edit); without
    them the sitemap names the pages."""
    name = run["brief"].get("name") or run["site"]
    who = client or "you"
    by_id = {i.get("id"): i for i in items or ()}
    pages = []
    for pid in run.get("pages") or []:
        it = by_id.get(pid) or {}
        pages.append((it.get("title") or pid, ((it.get("links") or {}).get("edit")) or ""))
    if not pages:
        pages = [(p.get("title") or p.get("slug") or "", "") for p in run.get("sitemap") or []]
    admin = f"{site_url.rstrip('/')}/wp-admin/" if site_url else "your site's /wp-admin/"
    lines = [f"# Your new website: {name}", "",
             f"This guide is for {who}. It lists the pages of the site and shows how to change them yourself.", "",
             "## The pages", ""]
    if pages:
        for title, link in pages:
            lines.append(f"- **{title}**" + (f" (edit: {link})" if link else ""))
    else:
        lines.append("- No pages yet.")
    blocks_word = ("GenerateBlocks blocks (Element, Text)" if run.get("mode") == "generateblocks"
                   else "WordPress' own blocks (Group, Heading, Paragraph, Columns, Buttons)")
    lines += ["", "## Changing a page", "",
              f"1. Log in at {admin} with your own user name and password.",
              "2. Go to **Pages**, point at the page and click **Edit**. The block editor opens.",
              "3. Click into any text and type. Headings, paragraphs and buttons change in place.",
              "4. Click a button, then the link icon in the small toolbar, to change where it leads.",
              f"5. The page is made of {blocks_word}. Select a block to see its settings on the right;"
              " the colours already match the brand, so you rarely need to change them.",
              "6. To add something, click the **+** where you want it and pick a block, or copy a block"
              " you like with the three dots menu, **Duplicate**.",
              "7. Click **Save** to keep a draft, **Preview** to look at it. Your changes are kept as"
              " revisions; **Revisions** in the sidebar brings an older version back.", "",
              "## Good to know", "",
              "- Images: upload them in the image block and always fill in the alternative text (what the"
              " picture shows), it helps people with screen readers and search engines.",
              "- If a block shows a warning, do not click **Attempt recovery**; ask us first.",
              "- Nothing goes live by itself: a draft is published only with the **Publish** button."]
    return "\n".join(lines) + "\n"
