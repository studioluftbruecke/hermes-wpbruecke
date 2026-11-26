"""The proof of a FluentCRM email: FluentCRM's own rendered HTML in Hermes' Chromium at 600 and 390 px.

FluentCRM renders the campaign (render-email-preview through the connector's preview route); this module
serves that HTML from a loopback address for the length of one capture (``proof.runner``) and judges it
with the proof checks (``proof.checks.evaluate``: render, console, overflow, alt text, contrast). Images load from the site (its host is
the one other host the browser may reach). Links are checked here instead of in the page, because the
page is not on the site's host: every link must be http(s) or mailto, SmartCodes in links must be resolved
(FluentCRM's ``##crm.*##`` footer links are filled per recipient at send time and pass), and links to the
site's own pages are opened once without the password (FluentCRM's own action links are not opened).
Images must have loaded at every width. The run is stored like any proof (``proofs/<id>/``).
"""
from __future__ import annotations

import contextlib
import re
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse

WIDTHS = (600, 390)
MAX_LINKS = 20


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href"))


def links_of(page: str) -> list:
    p = _Links()
    p.feed(page or "")
    return p.links


@contextlib.contextmanager
def served(page: str):
    """The HTML at http://127.0.0.1:<port>/email.html while the block runs."""
    body = page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's name)
            if self.path.split("?")[0] != "/email.html":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 (http.server's name)
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="wpbruecke-email-proof", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/email.html"
    finally:
        server.shutdown()
        server.server_close()


def link_row(hrefs: list, site_host: str, opener: Callable[[str], int]) -> dict:
    """The links check for an email: shape of every link, then the site's own pages opened once."""
    bad, checked, skipped, other = [], [], 0, 0
    for href in dict.fromkeys(h or "" for h in hrefs):
        h = href.strip()
        low = h.lower()
        if not h or h == "#" or low.startswith("javascript:"):
            bad.append(f"{h or '(empty)'} (no target)")
            continue
        if re.fullmatch(r"##crm\.[a-z_]+##", h) or low.startswith("mailto:"):
            continue
        if "{{" in h:
            bad.append(f"{h[:80]} (an unresolved SmartCode)")
            continue
        if not low.startswith(("http://", "https://")):
            bad.append(f"{h[:80]} (not an http(s) link)")
            continue
        u = urlparse(h)
        if (u.hostname or "").lower() != site_host:
            other += 1
            continue
        if "fluentcrm" in (u.query or "").lower() or len(checked) >= MAX_LINKS:
            skipped += 1
            continue
        try:
            status = opener(h)
        except Exception as e:  # a link that cannot be opened is a broken one
            status = None
            bad.append(f"{h[:80]} ({type(e).__name__})")
        if status is not None:
            checked.append(h)
            if status >= 400:
                bad.append(f"{h[:80]} (HTTP {status})")
    notes = []
    if other:
        notes.append(f"{other} link{'s' if other != 1 else ''} to other hosts not opened")
    if skipped:
        notes.append(f"{skipped} FluentCRM action link{'s' if skipped != 1 else ''} not opened")
    tail = f"; {'; '.join(notes)}" if notes else ""
    if bad:
        return {"check": "broken_links", "verdict": "differs",
                "detail": f"{len(bad)} link{'s' if len(bad) != 1 else ''} in the email do not work: "
                          f"{', '.join(bad[:3])}{tail}"}
    return {"check": "broken_links", "verdict": "match",
            "detail": f"{len(checked)} link{'s' if len(checked) != 1 else ''} to the site answered{tail}"}


def image_row(capture: dict) -> dict:
    """Every image of the email loaded (a natural size) at every width."""
    broken, total = [], set()
    for w, row in ((capture or {}).get("widths") or {}).items():
        for img in row.get("images") or []:
            total.add(img.get("src"))
            if not img.get("w"):
                broken.append(f"{(img.get('src') or '')[:80]} at {w} px")
    if not total:
        return {"check": "images_load", "verdict": "match", "detail": "no images in the email"}
    if broken:
        return {"check": "images_load", "verdict": "differs",
                "detail": f"{len(broken)} image load{'s' if len(broken) != 1 else ''} failed: {', '.join(broken[:3])}"}
    return {"check": "images_load", "verdict": "match", "detail": f"all {len(total)} images loaded at every width"}


def prove(rt: Any, site: dict, page: str, target: dict, opener: Optional[Callable[[str], int]] = None) -> dict:
    """Capture the email at 600 and 390 px, check it, store the run under ``proofs/<id>/`` and return it
    (the ProofRun shape wp_proof stores, with the email's link and image rows)."""
    from ...proof import checks, runner
    from ...store import new_id, now_iso
    host = (urlparse(site["url"]).hostname or "").lower()
    extra = [host, *((site.get("proof") or {}).get("extra_hosts") or [])]
    if opener is None:
        client = rt.client(site, keyed=False)
        opener = lambda url: client.raw("GET", url, auth=False).status  # noqa: E731
    run_id = new_id("proof")
    with rt.store.chromium_slot():
        started_at, t0 = now_iso(), time.monotonic()
        out = rt.store.path("proofs", run_id)
        out.mkdir(parents=True, exist_ok=True)
        with served(page) as url:
            cap = runner.capture(url, host="127.0.0.1", widths=list(WIDTHS), extra_hosts=tuple(extra), out_dir=out,
                                 chromium_path=rt.settings.chromium_path)
        rows = [c for c in checks.evaluate(cap) if c.get("check") not in ("broken_links", "diff")]
        if any(c.get("check") == "render" and c.get("verdict") == "match" for c in rows):
            rows += [link_row(links_of(page), host, opener), image_row(cap)]
        else:
            rows.append({"check": "broken_links", "verdict": "not_proven", "detail": "the email was not rendered"})
        widths = {int(w): r or {} for w, r in (cap.get("widths") or {}).items()}
        run = {"id": run_id, "target": target, "widths": list(WIDTHS), "started_at": started_at,
               "finished_at": now_iso(), "seconds": round(time.monotonic() - t0, 2), "chromium": cap.get("chromium"),
               "verdict": checks.overall(rows), "checks": rows, "diff": [],
               "shots": [{"width": w, "file": r.get("file"), "bytes": r.get("bytes", 0), "height": r.get("height", 0),
                          "load_ms": r.get("load_ms"), "status": r.get("status")} for w, r in widths.items()],
               "blocked_hosts": list(cap.get("blocked_hosts") or []),
               "console_errors": sorted({e for r in widths.values() for e in r.get("console_errors") or []}),
               "perf": {}}
        if cap.get("error"):
            run["error"] = cap["error"]
        rt.store.write_json(out / "run.json", run)
    return run
