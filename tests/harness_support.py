"""Opt-in WordPress Playground runs for the connector and the Elementor pack (``WPB_HARNESS=1``).

The harness is the development repository's Playground setup: ``start.sh`` boots a pinned WordPress
with this checkout's connector and seeded users, ``stop.sh`` ends it, ``run/`` holds the seeded
application passwords and page ids. It is not part of the published plugin. ``WPB_HARNESS_DIR`` points
at it; without that variable it is looked up in the repository layout next to the plugin. Without
``WPB_HARNESS=1``, or without the folder, every harness test skips and says why.

One Playground runs at a time: ``start.sh`` waits for the heavy lock and holds it until ``stop.sh``.
A boot takes one to six minutes. Nothing here prints a password.

Environment: ``WPB_HARNESS`` (``1`` opts in), ``WPB_HARNESS_DIR``, ``WPB_HARNESS_STUB_DIR`` (the full
pair's control stub), ``WPB_HARNESS_PORT`` (9400), ``WPB_HARNESS_BOOT_S`` (1800, the lock wait
included), and ``ELEMENTOR`` (a release or ``latest``: the full pair runs on it instead of the pin).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from http.client import HTTPConnection
from pathlib import Path

import pytest

from wpb_fakes import ROOT, load_plugin, mod

REPO = ROOT.parents[1]
HARNESS = Path(os.environ.get("WPB_HARNESS_DIR") or REPO / "experiments" / "wpbruecke" / "harness")
STUB = Path(os.environ.get("WPB_HARNESS_STUB_DIR") or HARNESS.parent / "live" / "stubs" / "wpb-ae2-stub")
STUB_GUEST = "/wordpress/wp-content/plugins/wpb-ae2-stub"
PORT = int(os.environ.get("WPB_HARNESS_PORT") or 9400)
BASE = f"http://127.0.0.1:{PORT}"
BOOT_S = float(os.environ.get("WPB_HARNESS_BOOT_S") or 1800)


def skip_reason(*needs: Path) -> str | None:
    """Why the harness tests cannot run here, or None."""
    if os.environ.get("WPB_HARNESS") != "1":
        return "opt-in Playground test: set WPB_HARNESS=1 (boots WordPress Playground, minutes)"
    for path in (HARNESS / "start.sh", *needs):
        if not path.exists():
            return (f"no Playground harness at {path} (it lives in the development repository, not in the "
                    "published plugin; set WPB_HARNESS_DIR)")
    for tool in ("node", "bash", "flock"):
        if not any((Path(d) / tool).exists() for d in os.environ.get("PATH", "").split(os.pathsep) if d):
            return f"the harness needs {tool} on PATH"
    return None


@dataclass
class Harness:
    blueprint: str
    ready: dict
    creds: dict = field(repr=False)
    seed: dict

    def pw(self, user: str) -> str:
        return self.creds[user]["pw"]

    def user(self, login: str) -> tuple[str, str]:
        return login, self.pw(login)

    @property
    def plugins(self) -> dict:
        return {slug: row.get("version") for slug, row in (self.ready.get("booted") or {}).get("plugins", {}).items()}


def boot(blueprint: str, log_dir: Path, extra_mounts: tuple = ()):
    """Start the harness, yield a Harness once it reads back its pins, stop it afterwards."""
    run = HARNESS / "run"
    ready_file = run / "ready.json"
    ready_file.unlink(missing_ok=True)
    env = dict(os.environ, WPB_HARNESS_PORT=str(PORT))
    if extra_mounts:
        env["EXTRA_MOUNTS"] = " ".join(f"{host}:{guest}" for host, guest in extra_mounts)
    log_path = log_dir / f"start-{blueprint}.log"
    started = time.time()
    with open(log_path, "w") as log:
        proc = subprocess.Popen(["bash", str(HARNESS / "start.sh"), blueprint], stdout=log, stderr=subprocess.STDOUT,
                                env=env, cwd=HARNESS)
    try:
        ready = None
        while ready is None:
            if ready_file.exists() and ready_file.stat().st_mtime >= started:
                data = json.loads(ready_file.read_text() or "{}")
                if data.get("match"):
                    ready = data
                    break
            if proc.poll() is not None:
                pytest.fail(f"the {blueprint} harness ended before it was ready (exit {proc.returncode}):\n"
                            + log_path.read_text()[-2000:])
            if time.time() - started > BOOT_S:
                pytest.fail(f"the {blueprint} harness was not ready after {BOOT_S:.0f} s:\n" + log_path.read_text()[-2000:])
            time.sleep(3)
        time.sleep(1)
        yield Harness(blueprint, ready, json.loads((run / "creds.json").read_text()),
                      json.loads((run / "seed.json").read_text()))
    finally:
        subprocess.run(["bash", str(HARNESS / "stop.sh")], env=env, cwd=HARNESS, capture_output=True, timeout=120)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=30)


# --- plain HTTP: harness control, anonymous reads, a person's login --------------------------------------

def http(method, path, user=None, pw=None, body=None, form=None, cookies=None, timeout=180):
    headers = {}
    if user:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if form is not None:
        data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    conn = HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheaders(), resp.read()
    finally:
        conn.close()


def rest(user, pw, method, path, body=None):
    status, _, data = http(method, "/wp-json" + path, user, pw, body=body)
    try:
        return status, json.loads(data or b"null")
    except ValueError:
        return status, {"raw": data[:200].decode("utf-8", "replace")}


def anon(path):
    """An anonymous page read. Playground's workers may answer one 500 while a file write settles."""
    for _ in range(3):
        status, headers, data = http("GET", path)
        if status != 500:
            break
        time.sleep(2)
    return status, {k.lower(): v for k, v in headers}, data.decode("utf-8", "replace")


def live_path(admin: tuple, post_id: int, kind: str = "pages") -> str:
    """The post's public path (pretty permalinks answer ?page_id= with a redirect)."""
    _, post = rest(*admin, "GET", f"/wp/v2/{kind}/{post_id}")
    return urllib.parse.urlparse((post or {}).get("link") or f"/?page_id={post_id}").path or "/"


def find(elements, element_id):
    for element in elements or []:
        if element.get("id") == element_id:
            return element
        hit = find(element.get("elements"), element_id)
        if hit:
            return hit
    return None


def preview_html(user, pw, post_id):
    status, link = rest(user, pw, "POST", "/wpbruecke/v1/preview-link", {"post_id": post_id})
    if status != 200:
        return status, ""
    url = urllib.parse.urlparse(link["url"])
    got, _, body = anon(url.path + ("?" + url.query if url.query else ""))
    return got, body


def login_cookies(admin: tuple, login: str) -> dict | None:
    """Log a harness user in through wp-login.php like a person (the control stub sets a fresh password)."""
    status, got = rest(*admin, "POST", "/wpb-ae2/v1/password", {"login": login})
    if status != 200:
        return None
    cookies = {"wordpress_test_cookie": "WP%20Cookie%20check"}
    _, headers, _ = http("POST", "/wp-login.php", form={"log": login, "pwd": got["pw"], "wp-submit": "Log In",
                                                         "testcookie": "1", "redirect_to": "/wp-admin/"}, cookies=cookies)
    for key, value in headers:
        if key.lower() == "set-cookie":
            name, _, rest_ = value.partition("=")
            value = rest_.split(";", 1)[0]
            if name.startswith("wordpress_") and value and value != "deleted":
                cookies[name] = value
    return cookies if any(k.startswith("wordpress_logged_in_") for k in cookies) else None


# --- the plugin, as one harness user ----------------------------------------------------------------------

class _Settings(dict):
    def __call__(self, key, default=None):
        return self.get(key, default)


class Agent:
    """The plugin's own handlers for one harness user: its own data folder, its password in memory only."""

    def __init__(self, login: str, pw: str, packs: bool = True):
        self.login = login
        self.tmp = Path(tempfile.mkdtemp(prefix=f"wpb-harness-{login}-"))
        self.settings = _Settings(site1_url=BASE, site1_user=login)
        self.secret = lambda name: pw if name == "WPBRUECKE_KEY1" else None  # noqa: E731
        self.kw = dict(wporg_fetch=lambda url: {}, client_timeout=120)
        run = mod("lanes").RunContext()
        plugin = load_plugin()
        self.draft_h = plugin.make_draft_handler(self.settings, self.tmp, lambda: run, self.secret,
                                                 tickets=mod("hooks").Tickets(), **self.kw)
        self.content_h = plugin.make_content_handler(self.settings, self.tmp, self.secret, **self.kw)
        self.proof_h = plugin.make_proof_handler(self.settings, self.tmp, self.secret, **self.kw)
        self.doctor_h = plugin.make_doctor_handler(self.settings, self.tmp, self.secret, **self.kw)
        api = mod("api")
        check = api.sites_check(self.rt(), {"group": 1})[1]
        self.check = {k: check.get(k) for k in ("state", "role", "level")}
        if check.get("state") == "needs_confirm":
            api.site_confirm(self.rt(), check["site_id"], {"level": check["level"], "caps_fp": check["caps_fp"]})
        self.site = self.rt().sites.ids()[0]
        if packs:
            self.rt().sites.update(self.site, {"packs": {"elementor": True}}, "person")

    def rt(self):
        return mod("runtime").Runtime(self.settings, self.secret, self.tmp, **self.kw)

    def state(self) -> str:
        return mod("api").sites_check(self.rt(), {"group": 1})[1].get("state")

    def draft(self, **args) -> dict:
        return json.loads(self.draft_h({"site": self.site, **args}, session_id=f"harness-{self.login}"))

    def content(self, **args) -> dict:
        return json.loads(self.content_h({"site": self.site, **args}))

    def doctor(self, **args) -> dict:
        return json.loads(self.doctor_h({"site": self.site, **args}))

    def proof(self, item: str) -> dict:
        res = self.proof_h({"item": item})
        if isinstance(res, dict) and res.get("_multimodal"):
            return json.loads(res["text_summary"])
        return json.loads(res) if isinstance(res, str) else res

    def log_lines(self) -> list:
        return mod("activity").ActivityLog(self.rt().store).read(self.site)
