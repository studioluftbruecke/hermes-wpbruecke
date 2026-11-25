"""WordPressClient: core REST over HTTPS with an application password, nothing else.

- Basic auth from the site's settings group; the key is never logged, printed or put in an error, and
  never sent over plain http except to this computer (loopback).
- ``connect_ip`` (PLAN constraint 10): the TCP connection goes to that address while TLS and the
  Host header keep the real host name (SNI), for sites reachable only through a VPN.
- Error kinds: ``auth`` (401), ``forbidden`` (403), ``not_found`` (404), ``rate_limited`` (429, with
  ``retry_after``), ``unreachable`` (DNS, TCP, TLS, timeout), ``bad_response`` (not JSON where JSON
  was expected), ``http`` (anything else >= 400). WordPress' own ``code`` (``rest_cannot_publish``)
  rides along. No retry loops (PLAN risks: WAFs).
- ``transport`` is injectable: ``transport(method, url, headers, body, timeout) -> (status, headers,
  bytes)``; tests pass recorded answers, the plugin passes nothing.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urljoin, urlparse

USER_AGENT = "WPBruecke/0.1 (+Hermes Agent plugin)"
Transport = Callable[[str, str, dict, Optional[bytes], float], "tuple[int, dict, bytes]"]


class WPError(Exception):
    def __init__(self, kind: str, message: str, status: int = 0, code: str = "", retry_after: Optional[int] = None):
        super().__init__(message)
        self.kind, self.message, self.status, self.code, self.retry_after = kind, message, status, code, retry_after

    def to_dict(self) -> dict:
        out = {"error": self.kind, "message": self.message}
        if self.status:
            out["status"] = self.status
        if self.code:
            out["wp_code"] = self.code
        if self.retry_after is not None:
            out["retry_after"] = self.retry_after
        return out


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    ms: int

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except ValueError:
            raise WPError("bad_response", f"expected JSON, got {self.body[:80]!r}", self.status) from None

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def header(self, name: str) -> Optional[str]:
        low = name.lower()
        for k, v in self.headers.items():
            if k.lower() == low:
                return v
        return None


def is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def may_carry_key(url: str) -> bool:
    """A key goes over https, or over http to this computer only."""
    parsed = urlparse(url)
    return parsed.scheme == "https" or (parsed.scheme == "http" and is_loopback(parsed.hostname or ""))


def _stdlib_transport(connect_ip: str = ""):
    """http.client never follows a redirect: a 3xx comes back as it is, so a key never crosses one."""
    ctx = ssl.create_default_context()

    def send(method: str, url: str, headers: dict, body: Optional[bytes], timeout: float):
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
            if connect_ip:
                def connect(c=conn):
                    sock = socket.create_connection((connect_ip, port), timeout)
                    c.sock = ctx.wrap_socket(sock, server_hostname=host)
                conn.connect = connect  # type: ignore[assignment]
        else:
            conn = http.client.HTTPConnection(connect_ip or host, port, timeout=timeout)
            headers = {**headers, "Host": parsed.netloc}
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read(20 * 1024 * 1024)
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    return send


class WordPressClient:
    def __init__(self, url: str, login: str = "", password: Optional[str] = None, connect_ip: str = "",
                 timeout: float = 25.0, transport: Optional[Transport] = None) -> None:
        self.url = url.rstrip("/")
        self.host = urlparse(self.url).hostname or ""
        self.login = login
        carry = bool(login and password) and may_carry_key(self.url)
        self._auth = ("Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()) if carry else None
        self.connect_ip = connect_ip
        self.timeout = timeout
        self._send = transport or _stdlib_transport(connect_ip)

    def __repr__(self) -> str:  # never the key
        return f"WordPressClient({self.url!r}, login={self.login!r}, key={'set' if self._auth else 'none'})"

    @property
    def authenticated(self) -> bool:
        return self._auth is not None

    def absolute(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            target = urlparse(path)
            if target.hostname != self.host:
                raise WPError("forbidden", f"refusing a request to {target.hostname}: this client talks to {self.host} only")
            return path
        return urljoin(self.url + "/", path.lstrip("/"))

    def raw(self, method: str, path: str, params: Optional[dict] = None, body: Any = None, data: Optional[bytes] = None,
            headers: Optional[dict] = None, auth: bool = True, timeout: Optional[float] = None) -> Response:
        url = self.absolute(path)
        if params:
            url += ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        h = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
        if auth and self._auth:
            h["Authorization"] = self._auth
        payload = data
        if body is not None:
            payload = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        t0 = time.monotonic()
        try:
            status, rh, rb = self._send(method, url, h, payload, timeout or self.timeout)
        except WPError:
            raise
        except (OSError, socket.timeout, ssl.SSLError, http.client.HTTPException) as e:
            raise WPError("unreachable", f"{self.host} did not answer: {type(e).__name__}: {str(e)[:160]}") from None
        return Response(status, rh, rb, int((time.monotonic() - t0) * 1000))

    def request(self, method: str, path: str, **kw: Any) -> Response:
        resp = self.raw(method, path, **kw)
        if resp.status >= 400:
            raise self._error(resp)
        return resp

    @staticmethod
    def _error(resp: Response) -> WPError:
        code, message = "", ""
        try:
            body = json.loads(resp.body.decode("utf-8", "replace"))
            if isinstance(body, dict):
                code = str(body.get("code") or "")
                message = str(body.get("message") or "")
        except ValueError:
            message = resp.body[:120].decode("utf-8", "replace")
        kind = {401: "auth", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(resp.status, "http")
        retry = None
        if resp.status == 429:
            try:
                retry = int(resp.header("Retry-After") or 0) or None
            except ValueError:
                retry = None
        text = f"HTTP {resp.status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
        return WPError(kind, text[:300], resp.status, code, retry)

    # --- conveniences -----------------------------------------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None).json()

    def get_with_total(self, path: str, **params: Any) -> "tuple[Any, int]":
        resp = self.request("GET", path, params=params or None)
        try:
            total = int(resp.header("X-WP-Total") or 0)
        except ValueError:
            total = 0
        return resp.json(), total

    def get_all(self, path: str, limit: int = 500, **params: Any) -> list:
        out, page = [], 1
        while len(out) < limit:
            resp = self.request("GET", path, params={**params, "per_page": 100, "page": page})
            items = resp.json()
            if not isinstance(items, list) or not items:
                break
            out.extend(items)
            try:
                pages = int(resp.header("X-WP-TotalPages") or 1)
            except ValueError:
                pages = 1
            if page >= pages:
                break
            page += 1
        return out[:limit]

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, body=body, **kw).json()

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params=params or None).json()
