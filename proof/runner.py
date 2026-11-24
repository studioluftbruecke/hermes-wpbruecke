"""Screenshot one page at several widths in a short-lived headless Chromium.

The launcher is copied from Shaderbrücke's tested renderer: Hermes' own Chromium, spawned with
``os.posix_spawn`` (no shell redirection, so pipe fds above 9 are fine) through a one-line
``/bin/sh`` that turns core dumps off and changes into the throwaway profile; a minimal environment
from an allowlist (the gateway holds every profile's keys); a throwaway ``--user-data-dir`` that is
never TMPDIR itself; a short TMPDIR (Chromium's socket paths break past ~107 bytes); CDP over
``--remote-debugging-pipe`` (JSON on fds 3/4, NUL-terminated). One wall-clock deadline covers the
whole capture, and the process group is always killed.

The browser reaches only the site: ``--host-resolver-rules`` maps every other host to NOTFOUND
(``EXCLUDE`` for the site and its ``extra_hosts``), and WebRTC gets no UDP path. With
``connect_ip`` the site's host is mapped to that address (TLS still uses the real host name).
A request that fails because of the rule names its host in ``blocked_hosts``.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import select
import shutil
import signal
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_WIDTHS = (1440, 768, 390)
VIEWPORT_HEIGHT = 900
MAX_LINKS_CHECKED = 30
MAX_ERRORS = 20
IDLE_QUIET_S = 0.5      # network idle: no request in flight for this long
IDLE_MAX_S = 3.0        # ...but never wait longer than this for it
LOAD_EVENT_MAX_S = 20.0
_NEVER_IDLE_TYPES = ("EventSource", "WebSocket")

_MARKER = Path("/etc/hermes/chromium-executable-path")


class _Deadline(Exception):
    pass


def find_chromium(override: str = "") -> str | None:
    """The settings override, then the Chromium Hermes itself records, then PATH."""
    override = (override or "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    if _MARKER.is_file():
        candidate = _MARKER.read_text(encoding="utf-8").strip()
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --- resolver rule ------------------------------------------------------------------------------

def resolver_rules(host: str, connect_ip: str = "", extra_hosts=()) -> str:
    """The ``--host-resolver-rules`` value that lets the browser reach only the site.

    With ``connect_ip`` the site's host maps to that address and the extra hosts resolve normally
    (``EXCLUDE``); without it the site's host and the extra hosts are excluded from the NOTFOUND map.
    """
    host = (host or "").strip().lower()
    if not host or any(c in host for c in " ,\t\n"):
        raise ValueError(f"not a host name: {host!r}")
    extras = []
    for extra in extra_hosts or ():
        e = str(extra).strip().lower()
        if not e or any(c in e for c in " ,\t\n"):
            raise ValueError(f"not a host name: {extra!r}")
        if e != host and e not in extras:
            extras.append(e)
    if connect_ip:
        ip = connect_ip.strip()
        if not ip or any(c in ip for c in " ,\t\n"):
            raise ValueError(f"not an address: {connect_ip!r}")
        rules = [f"MAP {host} {ip}", "MAP * ~NOTFOUND"]
    else:
        rules = ["MAP * ~NOTFOUND", f"EXCLUDE {host}"]
    rules += [f"EXCLUDE {e}" for e in extras]
    return ", ".join(rules)


# --- child process (copied from Shaderbrücke renderer.py) ----------------------------------------

# The only variables Chromium inherits. The gateway's environment holds every profile's API keys
# and tokens; none of them may reach the browser process. TMPDIR must not be the user-data-dir
# itself: Chromium crashes at start (SIGTRAP) when it is.
_INHERITED_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")

# Chromium keeps a Unix socket under TMPDIR and under the profile; a socket path over ~107 bytes
# makes it exit at start. Hermes points TMPDIR into the profile home, which can be long.
MAX_TEMP_BASE = 60

# posix_spawn cannot change directory or limits, so Chromium starts through a one-line /bin/sh:
# core dumps off (a crash never drops a core file into the gateway's working directory) and cwd =
# the throwaway profile. fds 3/4 are in place when sh starts and `exec` keeps them.
_LAUNCHER = "/bin/sh"
_LAUNCH_SCRIPT = 'ulimit -c 0 2>/dev/null; cd "$HOME" || exit 1; exec "$0" "$@"'


def short_temp_base() -> str:
    """The host temp dir when its path is short, else the first short writable fallback."""
    candidates = [os.environ.get("TMPDIR", ""), tempfile.gettempdir(), "/tmp", "/var/tmp", "/dev/shm"]
    for candidate in candidates:
        if candidate and len(candidate) <= MAX_TEMP_BASE and os.path.isdir(candidate) \
                and os.access(candidate, os.W_OK):
            return candidate
    return tempfile.gettempdir()


def child_env(profile_dir: str) -> dict[str, str]:
    """Lookup and locale variables, a short temp dir, and the throwaway profile as HOME."""
    env = {k: os.environ[k] for k in _INHERITED_ENV if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    env["TMPDIR"] = short_temp_base()
    env["HOME"] = profile_dir
    return env


def browser_args(chrome: str, profile: str, rules: str) -> list[str]:
    return [
        chrome, "--headless=new", "--remote-debugging-pipe",
        "--no-sandbox", "--disable-dev-shm-usage", "--enable-unsafe-swiftshader",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
        "--disable-sync", "--password-store=basic", "--hide-scrollbars", "--mute-audio",
        f"--host-resolver-rules={rules}",
        # The resolver rule stops HTTP; WebRTC would still send STUN over UDP to an IP literal.
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        f"--user-data-dir={profile}", "about:blank",
    ]


class _Pipe:
    """Minimal CDP client over --remote-debugging-pipe that keeps the events it reads."""

    def __init__(self, write_fd: int, read_fd: int, deadline: float):
        self._w, self._r, self.deadline = write_fd, read_fd, deadline
        self._buf = b""
        self._next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self._next_id += 1
        msg = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self._w, json.dumps(msg).encode() + b"\0")
        while True:
            reply = self._read_message()
            if reply.get("id") == self._next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})
            if "method" in reply:
                self.events.append(reply)

    def pump(self, seconds: float) -> None:
        """Read events for up to `seconds` (never past the deadline)."""
        until = min(time.monotonic() + seconds, self.deadline)
        while time.monotonic() < until:
            msg = self._read_message(until)
            if msg is None:
                return
            if "method" in msg:
                self.events.append(msg)

    def _read_message(self, until: float | None = None) -> dict | None:
        while b"\0" not in self._buf:
            limit = self.deadline if until is None else min(until, self.deadline)
            left = limit - time.monotonic()
            if left <= 0:
                if until is not None and until < self.deadline:
                    return None
                raise _Deadline()
            ready, _, _ = select.select([self._r], [], [], left)
            if not ready:
                continue
            chunk = os.read(self._r, 1 << 20)
            if not chunk:
                raise RuntimeError("browser closed the DevTools pipe")
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\0", 1)
        return json.loads(raw)


def _high_fd(fd: int) -> int:
    """Move `fd` to 10 or above (close-on-exec), so the spawn's dup2 onto 3/4 never collides."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
    os.close(fd)
    return moved


def _reap(pid: int, grace_s: float) -> int | None:
    until = time.monotonic() + grace_s
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return -1
        if done:
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= until:
            return None
        time.sleep(0.05)


class _Child:
    """One Chromium child: spawn, talk CDP over the pipe, always kill the group."""

    def __init__(self, chrome: str, rules: str, timeout_s: float):
        self.chrome, self.rules, self.timeout_s = chrome, rules, timeout_s
        self.profile = ""
        self.pid = 0
        self.cdp: _Pipe | None = None
        self._cmd_w = self._res_r = -1
        self.started = time.monotonic()

    def __enter__(self) -> "_Child":
        try:
            return self._spawn()
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _spawn(self) -> "_Child":
        self.profile = tempfile.mkdtemp(prefix="wpb-chrome-", dir=short_temp_base())
        cmd_r, cmd_w = (_high_fd(fd) for fd in os.pipe())
        res_r, res_w = (_high_fd(fd) for fd in os.pipe())
        log_fd = os.open(os.path.join(self.profile, "browser.log"),
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
        argv = browser_args(self.chrome, self.profile, self.rules)
        self.started = time.monotonic()
        try:
            self.pid = os.posix_spawn(_LAUNCHER, [_LAUNCHER, "-c", _LAUNCH_SCRIPT, *argv],
                                      child_env(self.profile), setpgroup=0,
                                      file_actions=[
                                          (os.POSIX_SPAWN_OPEN, 0, "/dev/null", os.O_RDONLY, 0),
                                          (os.POSIX_SPAWN_DUP2, log_fd, 1),
                                          (os.POSIX_SPAWN_DUP2, log_fd, 2),
                                          (os.POSIX_SPAWN_DUP2, cmd_r, 3),
                                          (os.POSIX_SPAWN_DUP2, res_w, 4),
                                      ])
        finally:
            for fd in (cmd_r, res_w, log_fd):
                os.close(fd)
            self._cmd_w, self._res_r = cmd_w, res_r
        self.cdp = _Pipe(cmd_w, res_r, self.started + self.timeout_s)
        return self

    def log_tail(self, limit: int = 600) -> str:
        try:
            data = Path(self.profile, "browser.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return data.strip()[-limit:]

    def __exit__(self, *exc_info) -> None:
        try:
            if self.cdp is not None:
                self.cdp.deadline = max(self.cdp.deadline, time.monotonic() + 2.0)
                self.cdp.call("Browser.close")
        except Exception:
            pass
        exit_code = _reap(self.pid, 2.0) if self.pid else None
        if self.pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)
            if exit_code is None:
                _reap(self.pid, 5.0)
        for fd in (self._cmd_w, self._res_r):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self._cmd_w = self._res_r = -1
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)


# --- in-page probes ------------------------------------------------------------------------------

_SCROLL_JS = """(async (maxHeight) => {
  const step = Math.max(200, window.innerHeight - 100);
  const bottom = Math.min(document.documentElement.scrollHeight, maxHeight);
  const frame = () => new Promise(r => requestAnimationFrame(() => r()));
  for (let y = 0; y < bottom; y += step) { window.scrollTo(0, y); await frame(); }
  window.scrollTo(0, 0); await frame();
  return true;
})(__MAX__)"""

_PAGE_JS = """(async (cfg) => {
  const out = {};
  const nav = performance.getEntriesByType('navigation')[0];
  out.load_ms = nav && nav.loadEventEnd > 0 ? Math.round(nav.loadEventEnd - nav.startTime) : null;
  const observe = (type, fn) => new Promise(resolve => {
    try {
      const po = new PerformanceObserver(list => { for (const e of list.getEntries()) fn(e); });
      po.observe({type, buffered: true});
      setTimeout(() => { po.disconnect(); resolve(true); }, 150);
    } catch (e) { resolve(false); }
  });
  let lcp = null, cls = 0;
  const lcpOk = await observe('largest-contentful-paint', e => { lcp = e.startTime; });
  const clsOk = await observe('layout-shift', e => { if (!e.hadRecentInput) cls += e.value; });
  out.lcp_ms = lcpOk && lcp !== null ? Math.round(lcp) : null;
  out.cls = clsOk ? Math.round(cls * 10000) / 10000 : null;
  const de = document.documentElement;
  out.overflow_px = Math.max(0, Math.round(de.scrollWidth - window.innerWidth));
  out.page_height = Math.max(de.scrollHeight, document.body ? document.body.scrollHeight : 0);
  out.images = Array.from(document.images).slice(0, 100).map(i => ({
    src: (i.currentSrc || i.src || '').slice(0, 300), alt: i.getAttribute('alt'),
    w: i.naturalWidth, h: i.naturalHeight}));

  // Same-host links: HEAD from inside the page (the resolver rule applies), at most cfg.maxLinks.
  out.links = [];
  if (cfg.links) {
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href]')) {
      let u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
      if (u.protocol !== 'http:' && u.protocol !== 'https:') continue;
      u.hash = '';
      if (seen.has(u.href)) continue;
      seen.add(u.href);
      out.links.push({href: u.href, status: null, same: u.host === location.host});
      if (out.links.length >= 100) break;
    }
    let n = 0;
    await Promise.all(out.links.map(async l => {
      if (!l.same || n >= cfg.maxLinks) return;
      n += 1;
      const once = async method => {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 6000);
        try { return (await fetch(l.href, {method, redirect: 'follow', credentials: 'omit',
                                            cache: 'no-store', signal: ctl.signal})).status; }
        finally { clearTimeout(timer); }
      };
      try {
        let s = await once('HEAD');
        if (s === 405 || s === 501) s = await once('GET');
        l.status = s;
      } catch (e) { l.error = String(e && e.message || e).slice(0, 120); }
    }));
    out.links.forEach(l => { l.checked = l.same && (l.status !== null || !!l.error); });
  }

  // Contrast of visible text against its effective background (WCAG 2 formula).
  const parse = c => {
    const m = /^rgba?\\(([^)]+)\\)$/.exec((c || '').trim());
    if (!m) return null;
    const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(parseFloat);
    if (p.length < 3 || p.some(isNaN)) return null;
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a)).concat([1]);
  };
  const lum = c => {
    const ch = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const background = el => {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const c = parse(s.backgroundColor);
      if (c === null) return null;
      if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let base = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };
  const failing = [];
  let checked = 0, unknown = 0;
  const done = new Set();
  const walker = document.createTreeWalker(document.body || de, NodeFilter.SHOW_TEXT);
  while (walker.nextNode() && checked + unknown < 600) {
    const node = walker.currentNode;
    const text = node.textContent.replace(/\\s+/g, ' ').trim();
    const el = node.parentElement;
    if (text.length < 2 || !el || done.has(el)) continue;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION'].includes(el.tagName)) continue;
    done.add(el);
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;
    if (r.top + window.scrollY > cfg.maxHeight) continue;
    const fg = parse(s.color), bg = background(el);
    if (!fg || !bg) { unknown += 1; continue; }
    const f = over(fg, bg);
    const l1 = lum(f), l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(s.fontSize) || 16, weight = parseInt(s.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    checked += 1;
    if (ratio < (large ? 3 : 4.5)) {
      failing.push({text: text.slice(0, 60), ratio: Math.round(ratio * 100) / 100, large});
    }
  }
  failing.sort((a, b) => a.ratio - b.ratio);
  out.contrast = failing.slice(0, 20);
  out.contrast_checked = checked;
  out.contrast_unknown = unknown;
  return out;
})(__CFG__)"""


# --- one tab per width ---------------------------------------------------------------------------

class _Tab:
    """A fresh target for one width; folds the session's CDP events into its own state."""

    def __init__(self, child: _Child, width: int, site_host: str, extra_hosts: set[str]):
        self.cdp = child.cdp
        self.width = width
        self.site_host = site_host
        self.extra_hosts = extra_hosts
        self.target = self.cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.cdp.call("Target.attachToTarget",
                                     {"targetId": self.target, "flatten": True})["sessionId"]
        self._seen = 0
        self.loader_id = ""
        self.doc_status: dict[str, int] = {}
        self.methods: dict[str, str] = {}
        self.load_fired = False
        self.inflight: set[str] = set()
        self.urls: dict[str, str] = {}
        self.console_errors: list[str] = []
        self.failed: list[dict] = []
        self.last_activity = time.monotonic()

    def call(self, method: str, params: dict | None = None) -> dict:
        result = self.cdp.call(method, params, session=self.session)
        self.fold()
        return result

    def evaluate(self, expression: str, *, await_promise: bool = False):
        params = {"expression": expression, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        res = self.call("Runtime.evaluate", params)
        if "exceptionDetails" in res:
            d = res["exceptionDetails"]
            raise RuntimeError("page script failed: "
                               + str(d.get("exception", {}).get("description") or d.get("text")))
        return res.get("result", {}).get("value")

    def _error(self, text: str) -> None:
        text = " ".join(str(text or "").split())[:300]
        if text and text not in self.console_errors and len(self.console_errors) < MAX_ERRORS:
            self.console_errors.append(text)

    def fold(self) -> None:
        events = self.cdp.events
        while self._seen < len(events):
            ev = events[self._seen]
            self._seen += 1
            if ev.get("sessionId") != self.session:
                continue
            m, p = ev.get("method"), ev.get("params") or {}
            if m == "Network.requestWillBeSent":
                rid = p.get("requestId")
                req_url = (p.get("request") or {}).get("url", "")
                self.urls[rid] = req_url
                self.methods[rid] = (p.get("request") or {}).get("method", "GET")
                # blob:/data: URLs and streams never "finish"; waiting on them only burns the cap.
                if req_url.startswith(("http:", "https:")) and p.get("type") not in _NEVER_IDLE_TYPES:
                    self.inflight.add(rid)
                self.last_activity = time.monotonic()
            elif m == "Network.responseReceived":
                resp = p.get("response") or {}
                rid = p.get("requestId")
                if p.get("type") == "Document":
                    self.doc_status[rid] = resp.get("status")
                elif int(resp.get("status") or 0) >= 400 and self._counts(rid):
                    self._failed(resp.get("url") or self.urls.get(rid, ""), f"HTTP {resp.get('status')}")
            elif m in ("Network.loadingFinished", "Network.loadingFailed"):
                rid = p.get("requestId")
                self.inflight.discard(rid)
                self.last_activity = time.monotonic()
                if m == "Network.loadingFailed" and not p.get("canceled") and self._counts(rid):
                    self._failed(self.urls.get(rid, ""), p.get("errorText") or "failed")
            elif m == "Page.loadEventFired":
                self.load_fired = True
            elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
                parts = []
                for arg in p.get("args") or []:
                    parts.append(str(arg.get("value") if "value" in arg else arg.get("description", "")))
                self._error("console.error: " + " ".join(parts))
            elif m == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails") or {}
                self._error((d.get("exception") or {}).get("description") or d.get("text"))
            elif m == "Log.entryAdded":
                entry = p.get("entry") or {}
                # Network entries ("Failed to load resource") are reported as failed requests.
                if entry.get("level") == "error" and entry.get("source") != "network":
                    self._error(entry.get("text"))

    @property
    def status(self) -> int | None:
        return self.doc_status.get(self.loader_id)

    def _counts(self, rid: str) -> bool:
        """The link check's own HEAD requests and the browser's favicon fetch are not page requests."""
        if self.methods.get(rid) == "HEAD":
            return False
        return urlparse(self.urls.get(rid, "")).path != "/favicon.ico"

    def _failed(self, url: str, error: str) -> None:
        if len(self.failed) >= 50:
            return
        host = (urlparse(url).hostname or "").lower()
        row = {"url": url[:300], "host": host, "error": error}
        if row not in self.failed:
            self.failed.append(row)

    def wait(self, cond, max_s: float) -> bool:
        until = time.monotonic() + max_s
        while True:
            self.fold()
            if cond():
                return True
            if time.monotonic() >= until:
                return False
            self.cdp.pump(min(0.1, max(0.0, until - time.monotonic())))

    def wait_idle(self, max_s: float = IDLE_MAX_S) -> bool:
        return self.wait(lambda: not self.inflight
                         and time.monotonic() - self.last_activity >= IDLE_QUIET_S, max_s)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.cdp.call("Target.closeTarget", {"targetId": self.target})


def _capture_width(child: _Child, url: str, width: int, *, host: str, extra: set[str], out_dir: Path,
                   tag: str, max_height: int, check_links: bool) -> dict:
    row: dict = {"file": None, "bytes": 0, "height": 0, "load_ms": None, "status": None,
                 "lcp_ms": None, "cls": None, "overflow_px": None, "console_errors": [],
                 "failed_requests": [], "images": [], "links": [], "contrast": []}
    t0 = time.monotonic()
    tab = _Tab(child, width, host, extra)
    try:
        for domain in ("Page", "Runtime", "Log", "Network"):
            tab.call(f"{domain}.enable")
        tab.call("Network.setCacheDisabled", {"cacheDisabled": True})
        tab.call("Emulation.setDeviceMetricsOverride",
                 {"width": width, "height": VIEWPORT_HEIGHT, "deviceScaleFactor": 1, "mobile": False})
        nav = tab.call("Page.navigate", {"url": url})
        tab.loader_id = nav.get("loaderId", "")
        if nav.get("errorText"):
            row["error"] = f"the browser could not load the page: {nav['errorText']}"
            tab.fold()
            row["failed_requests"] = tab.failed
            return row
        if not tab.wait(lambda: tab.load_fired, LOAD_EVENT_MAX_S):
            row["load_timeout"] = True
        with contextlib.suppress(RuntimeError):
            tab.evaluate("document.fonts.ready.then(() => true)", await_promise=True)
        row["idle"] = tab.wait_idle()
        tab.evaluate(_SCROLL_JS.replace("__MAX__", str(int(max_height))), await_promise=True)
        tab.last_activity = time.monotonic()  # lazy images start a frame after the scroll
        row["idle"] = tab.wait_idle(2.0) and row["idle"]
        cfg = {"links": check_links, "maxLinks": MAX_LINKS_CHECKED, "maxHeight": max_height}
        data = tab.evaluate(_PAGE_JS.replace("__CFG__", json.dumps(cfg)), await_promise=True) or {}
        height = max(1, min(int(data.get("page_height") or VIEWPORT_HEIGHT), int(max_height)))
        shot = tab.call("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": True, "fromSurface": True,
            "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}})
        png = base64.b64decode(shot.get("data", ""))
        name = f"{width}-{tag}.png" if tag else f"{width}.png"
        tmp = out_dir / f".{name}.tmp"
        tmp.write_bytes(png)
        os.replace(tmp, out_dir / name)
        tab.fold()
        links = data.get("links") or []
        row.update({
            "file": name, "bytes": len(png), "height": height,
            "page_height": int(data.get("page_height") or 0),
            "load_ms": data.get("load_ms"), "status": tab.status, "lcp_ms": data.get("lcp_ms"),
            "cls": data.get("cls"), "overflow_px": data.get("overflow_px"),
            "images": data.get("images") or [],
            "links": [{"href": l["href"], "status": l.get("status"), "same": bool(l.get("same")),
                       **({"error": l["error"]} if l.get("error") else {})} for l in links],
            "links_checked": check_links,
            "contrast": data.get("contrast") or [],
            "contrast_checked": data.get("contrast_checked", 0),
            "contrast_unknown": data.get("contrast_unknown", 0),
        })
        row["console_errors"] = tab.console_errors
        row["failed_requests"] = tab.failed
        return row
    finally:
        tab.close()
        row["capture_ms"] = int((time.monotonic() - t0) * 1000)


def _blocked(failed: list[dict], host: str, extra: set[str]) -> list[str]:
    out = []
    for f in failed:
        h = f.get("host") or ""
        if h and h != host and h not in extra and "NAME_NOT_RESOLVED" in (f.get("error") or ""):
            if h not in out:
                out.append(h)
    return out


def capture(url: str, *, host: str, connect_ip: str = "", widths=DEFAULT_WIDTHS, extra_hosts=(),
            out_dir: Path, chromium_path: str = "", timeout_s: float = 60, prefix: str = "",
            max_height: int = 4000) -> dict:
    """Screenshot `url` at every width into `out_dir` and measure the page.

    Files are named ``<width>.png``, or ``<width>-<prefix>.png`` with a prefix (``before``).
    ``file`` in each width row is that name, relative to `out_dir`. Links are checked once, at the
    first width. On a browser failure the result carries ``error`` and no widths.
    """
    started = time.monotonic()
    host = (host or urlparse(url).hostname or "").lower()
    extra = {str(h).strip().lower() for h in extra_hosts or () if str(h).strip()}
    result: dict = {"url": url, "chromium": None, "widths": {}, "blocked_hosts": [], "seconds": 0.0}
    rules = resolver_rules(host, connect_ip, sorted(extra))
    chrome = find_chromium(chromium_path)
    if not chrome:
        result["error"] = ("no Chromium found (set the chromium_path setting)" if chromium_path
                           else "no Chromium found on this host")
        return result
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    child = _Child(chrome, rules, timeout_s)
    try:
        with child:
            try:
                result["chromium"] = child.cdp.call("Browser.getVersion").get("product")
                for i, width in enumerate(widths):
                    w = int(width)
                    result["widths"][w] = _capture_width(
                        child, url, w, host=host, extra=extra, out_dir=out_dir,
                        tag=prefix, max_height=max_height, check_links=(i == 0))
            except _Deadline:
                result["error"] = f"timed out after {timeout_s:g} s"
            except (RuntimeError, OSError, ValueError) as exc:
                tail = child.log_tail()
                result["error"] = f"{exc}" + (f" (browser log: {tail})" if tail else "")
    except OSError as exc:
        result["error"] = f"the browser did not start: {exc}"
    blocked: list[str] = []
    for row in result["widths"].values():
        for h in _blocked(row.get("failed_requests") or [], host, extra):
            if h not in blocked:
                blocked.append(h)
    result["blocked_hosts"] = blocked
    result["seconds"] = round(time.monotonic() - started, 2)
    return result
