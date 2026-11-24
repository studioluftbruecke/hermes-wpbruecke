"""Search Console for one post (F12, D13 a): the top search queries of a page over the last 28 days.

Sign-in is a Google service account: its JSON key is stored as the optional secret
``WPBRUECKE_GSC_KEY`` and the service account's e-mail is added as a Restricted user to the
site's Search Console property. The token is a signed JWT (RS256) exchanged at Google's token
endpoint; signing needs the ``cryptography`` package, which Hermes ships. Without the key, without
``cryptography`` or without access, the row is ``not_proven`` with that reason. Read-only scope.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Optional
from urllib.parse import quote

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"

Fetch = Callable[[str, str, dict, bytes], "tuple[int, Any]"]  # (method, url, headers, body) -> (status, json)


class GscUnavailable(Exception):
    """F12 is dark; the message is the reason the person reads."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def parse_key(raw: Optional[str]) -> dict:
    if not raw:
        raise GscUnavailable("no Search Console key (WPBRUECKE_GSC_KEY) is stored; see the README's Search Console steps")
    try:
        key = json.loads(raw)
    except ValueError:
        try:  # the same JSON, base64-encoded, survives any single-line field
            key = json.loads(base64.b64decode(raw.strip(), validate=True))
        except ValueError:
            raise GscUnavailable("WPBRUECKE_GSC_KEY is not a service account JSON key") from None
    if not isinstance(key, dict) or key.get("type") != "service_account" or not key.get("private_key") \
            or not key.get("client_email"):
        raise GscUnavailable("WPBRUECKE_GSC_KEY is not a service account JSON key (type, client_email, private_key)")
    return key


def default_signer(pem: str, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise GscUnavailable("signing the Search Console sign-in needs the cryptography package") from None
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def assertion(key: dict, now: float, signer: Callable[[str, bytes], bytes] = default_signer) -> str:
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": key.get("private_key_id", "")}).encode())
    claims = _b64(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                              "iat": int(now), "exp": int(now) + 3600}).encode())
    signing_input = f"{header}.{claims}".encode()
    return f"{header}.{claims}.{_b64(signer(key['private_key'], signing_input))}"


def token(key: dict, fetch: Fetch, now: float, signer=default_signer) -> str:
    body = ("grant_type=" + quote("urn:ietf:params:oauth:grant-type:jwt-bearer", safe="") +
            "&assertion=" + assertion(key, now, signer)).encode()
    status, data = fetch("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        reason = (data or {}).get("error_description") or (data or {}).get("error") if isinstance(data, dict) else None
        raise GscUnavailable(f"Google refused the service account sign-in (HTTP {status}{': ' + reason if reason else ''})")
    return data["access_token"]


def top_queries(raw_key: Optional[str], prop: str, page_url: str, fetch: Fetch, now: Optional[float] = None,
                signer=default_signer, days: int = 28, limit: int = 10) -> dict:
    now = time.time() if now is None else now
    if not prop:
        raise GscUnavailable("the site file names no Search Console property (gsc_property)")
    key = parse_key(raw_key)
    access = token(key, fetch, now, signer)
    end = time.strftime("%Y-%m-%d", time.gmtime(now - 2 * 86400))  # Search Console data lags about two days
    start = time.strftime("%Y-%m-%d", time.gmtime(now - (days + 2) * 86400))
    body = {"startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": limit,
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}]}
    status, data = fetch("POST", API.format(site=quote(prop, safe="")),
                         {"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
                         json.dumps(body).encode())
    if status == 403:
        raise GscUnavailable(f"{key['client_email']} has no access to {prop}; add it as a Restricted user in Search Console")
    if status != 200 or not isinstance(data, dict):
        raise GscUnavailable(f"Search Console answered HTTP {status}")
    rows = [{"query": r["keys"][0], "clicks": int(r.get("clicks", 0)), "impressions": int(r.get("impressions", 0)),
             "ctr": round(float(r.get("ctr", 0)), 4), "position": round(float(r.get("position", 0)), 1)}
            for r in data.get("rows") or []]
    return {"property": prop, "page": page_url, "start": start, "end": end, "rows": rows}


def row(raw_key: Optional[str], prop: str, page_url: str, fetch: Fetch, **kw) -> dict:
    """The SEO post report's ``search_queries`` row (weight 0: information, not part of the score)."""
    base = {"check": "search_queries", "label": "Search Console queries", "weight": 0}
    try:
        data = top_queries(raw_key, prop, page_url, fetch, **kw)
    except GscUnavailable as e:
        return {**base, "verdict": "not_proven", "detail": str(e)}
    if not data["rows"]:
        return {**base, "verdict": "match", "detail": f"no search queries for this page from {data['start']} to {data['end']}",
                "data": data}
    top = ", ".join(f"\"{r['query']}\" ({r['clicks']} clicks, {r['impressions']} impressions, pos {r['position']})"
                    for r in data["rows"][:5])
    return {**base, "verdict": "match", "detail": f"{data['start']} to {data['end']}: {top}", "data": data}
