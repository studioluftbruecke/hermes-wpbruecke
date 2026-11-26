"""One scrubber for every tool result and every write-log line (plan AE1, A35).

``scrub(value, secrets)`` walks dicts, lists and strings and returns a copy with these removed:

- the configured secret values the runtime passes in (``WPBRUECKE_KEY<n>``, the PageSpeed and Search
  Console keys; ``known_secrets`` reads them), spaced and unspaced, wherever they appear, also with
  other separators between their four-character groups (``-``, ``_``, ``.``, ``+``, ``%20``,
  ``&nbsp;``, several spaces, newlines, tabs);
- base64 tokens that decode to text holding a configured secret or an application password: a bare
  ``base64(user:password)`` Basic credential without the word ``Basic``, or ``base64(password)``;
- application passwords by shape: six groups of four letters or digits separated by spaces, or the
  same 24 characters without spaces. A shape alone also matches plain words ("this page will have
  more text"), so a candidate counts only when at least two of its six groups carry a digit or an
  upper-case letter after the first character; a password WordPress generates fails that one time in
  about 200,000, natural text almost never passes it;
- ``Authorization: Basic ...`` / ``Bearer ...`` headers, ``Basic``/``Bearer`` followed by a token,
  and bare tokens of known shapes (JWT, Google ``ya29.`` access tokens and ``AIza`` API keys, GitHub
  and Slack tokens);
- the values of keys named like a secret (``password``, ``pwd``, ``pw``, ``pass``, ``secret``,
  ``token``, ``api_key``, ``authorization`` ...), both as dict keys (text, lists and dicts alike) and
  inside text (``"password": "..."``, ``password=...``, ``pwd=...``);
- the password in URL userinfo (``https://user:pass@host`` keeps ``user``);
- with ``previews=False`` (log lines): the token of the connector's signed preview link
  (``wpb_preview=<hmac>``). That link opens an unpublished draft for anyone holding it until it
  expires (two hours) or the post is saved again, so it is a secret. A tool result keeps it: the
  person asked for that preview, and the token dies on its own; a log line is kept for 90 days and
  shown on the page, so there it is masked.

Strings that start with ``data:`` (screenshots as data URLs) pass unchanged. The scrubber never
raises: a value it cannot read is returned as it came.
"""
from __future__ import annotations

import base64
import binascii
import functools
import re
from typing import Any, Callable, Iterable, Iterator, Optional

MASK = "[redacted]"

SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "pw", "pass", "secret", "token", "api_key", "apikey", "app_password",
    "application_password", "authorization", "private_key", "client_secret", "access_token",
    "refresh_token", "id_token", "bearer", "cookie", "set_cookie", "x_wp_nonce", "wpb_preview",
})
_SECRET_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_apikey")

_EDGE_L = r"(?<![A-Za-z0-9+/=_-])"
_EDGE_R = r"(?![A-Za-z0-9+/=_-])"
_SPACED = re.compile(_EDGE_L + r"([A-Za-z0-9]{4}(?: [A-Za-z0-9]{4}){5})" + _EDGE_R)
_UNSPACED = re.compile(_EDGE_L + r"([A-Za-z0-9]{24})" + _EDGE_R)
_AUTH_HEADER = re.compile(
    r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?)(?:(basic|bearer|token)\s+)?([^\s\"',;}]+)")
_SCHEME_TOKEN = re.compile(r"\b(Basic|Bearer|basic|bearer|BEARER|BASIC)(\s+)([A-Za-z0-9+/=._~-]{8,})")
_BARE_TOKENS = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"   # JWT
    r"|\bya29\.[A-Za-z0-9_-]{16,}"                                      # Google OAuth access token
    r"|\bAIza[0-9A-Za-z_-]{35}"                                          # Google API key
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bxox[abposr]-[A-Za-z0-9-]{10,}")
_KEYS_RE = r"password|passwd|pwd|pw|pass|secret|token|api_key|apikey|app_password|application_password|" \
           r"access_token|refresh_token|client_secret|private_key"
_QUOTED_KV = re.compile(r"(?i)([\"'](?:[a-z0-9_-]*_)?(?:" + _KEYS_RE + r")[\"']\s*:\s*)(\"(?:[^\"\\]|\\.)*\"|'[^']*'|\[[^\[\]]*\]|\{[^{}]*\})")
_FORM_KV = re.compile(r"(?i)(?<![A-Za-z0-9_])((?:[a-z0-9_-]*_)?(?:" + _KEYS_RE + r")=)([^\s&\"'<>]+)")
_LOOSE_KV = re.compile(r"(?i)\b((?:password|passwd|secret|api_key|access_token|client_secret|app_password|"
                       r"application_password)\s*:\s*)([^\s\"',;}]+)")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@\"'<>]+):([^/\s@\"'<>]+)@")
_PREVIEW = re.compile(r"(?i)(\bwpb_preview=)([A-Za-z0-9%]+)")


def secret_key(name: Any) -> bool:
    key = str(name or "").strip().lower().replace("-", "_")
    return key in SECRET_KEYS or key.endswith(_SECRET_SUFFIXES)


def _marked(group: str) -> bool:
    return any(c.isdigit() for c in group) or any(c.isupper() for c in group[1:])


def _password_like(candidate: str) -> bool:
    plain = candidate.replace(" ", "")
    groups = [plain[i:i + 4] for i in range(0, 24, 4)]
    return sum(1 for g in groups if _marked(g)) >= 2


def _random_like(token: str) -> bool:
    if any(c.isdigit() for c in token) or any(c in "+/=._~" for c in token):
        return True
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)


def _variants(secrets: Iterable[Optional[str]]) -> list:
    out = set()
    for s in secrets or ():
        s = str(s or "").strip()
        if len(s) < 8:
            continue
        out.add(s)
        joined = re.sub(r"\s+", "", s)
        if len(joined) >= 8:
            out.add(joined)
    return sorted(out, key=len, reverse=True)


_SEP = r"(?:[\s\-_.+]|%20|&nbsp;|&#160;)*"
_B64_STD = re.compile(r"[A-Za-z0-9+/]{12,}={0,2}")
_B64_URL = re.compile(r"[A-Za-z0-9_-]{12,}={0,2}")


@functools.lru_cache(maxsize=64)
def _separated(secrets: tuple) -> tuple:
    """A pattern per configured secret that also matches it with separators between its four-character
    groups (``Ab3d-eF6h``, ``Ab3d%20eF6h``, ``Ab3d&nbsp;eF6h``, two spaces, a newline ...)."""
    out = []
    for core in sorted({re.sub(r"\s+", "", s) for s in secrets}, key=len, reverse=True):
        if 8 <= len(core) <= 200:
            groups = [re.escape(core[i:i + 4]) for i in range(0, len(core), 4)]
            out.append(re.compile(_SEP.join(groups)))
    return tuple(out)


def _has_secret(text: str, secrets: list, separated: tuple) -> bool:
    return any(v in text for v in secrets) or any(p.search(text) for p in separated) \
        or any(_password_like(m.group(1)) for m in _SPACED.finditer(text)) \
        or any(_password_like(m.group(1)) for m in _UNSPACED.finditer(text))


def _decoded(token: str) -> Iterator[str]:
    """The texts a base64 token can stand for: standard and URL-safe alphabets, and the token read from
    each of its first four characters (glued to a word before it)."""
    body = token.rstrip("=")
    for start in range(4):
        part = body[start:]
        if len(part) % 4 == 1:
            part = part[:-1]
        if len(part) < 12:
            break
        for alt in (None, b"-_"):
            try:
                raw = base64.b64decode(part + "=" * (-len(part) % 4), altchars=alt, validate=True)
            except (binascii.Error, ValueError):
                continue
            yield raw.decode("utf-8", "replace")


def _mask_base64(text: str, secrets: list, separated: tuple) -> str:
    """Mask a base64 token that decodes to a known secret or an application password, e.g. a bare
    ``base64(user:password)`` Basic credential a plugin echoed without the word Basic."""
    def one(m: "re.Match") -> str:
        tok = m.group(0)
        return MASK if any(_has_secret(t, secrets, separated) for t in _decoded(tok)) else tok
    return _B64_URL.sub(one, _B64_STD.sub(one, text))


def _quoted_value(m: "re.Match") -> str:
    """``"password": <value>`` with the value masked: a quoted string keeps its quotes, a list or object
    becomes a quoted mask."""
    quote = m.group(2)[0] if m.group(2)[0] in "\"'" else '"'
    return m.group(1) + quote + MASK + quote


def scrub_text(text: str, secrets: Iterable[Optional[str]] = (), previews: bool = True) -> str:
    if not isinstance(text, str) or not text or text.startswith("data:"):
        return text
    secrets = _variants(secrets)
    for value in secrets:
        if value in text:
            text = text.replace(value, MASK)
    separated = _separated(tuple(secrets))
    for pattern in separated:
        text = pattern.sub(MASK, text)
    text = _mask_base64(text, secrets, separated)
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + (m.group(2) + " " if m.group(2) else "") + MASK, text)
    text = _SCHEME_TOKEN.sub(lambda m: m.group(1) + m.group(2) + MASK if _random_like(m.group(3)) else m.group(0), text)
    text = _BARE_TOKENS.sub(MASK, text)
    text = _QUOTED_KV.sub(_quoted_value, text)
    text = _FORM_KV.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) == MASK else MASK), text)
    text = _LOOSE_KV.sub(lambda m: m.group(1) + MASK, text)
    text = _USERINFO.sub(lambda m: m.group(1) + ":" + MASK + "@", text)
    text = _SPACED.sub(lambda m: MASK if _password_like(m.group(1)) else m.group(0), text)
    text = _UNSPACED.sub(lambda m: MASK if _password_like(m.group(1)) else m.group(0), text)
    if not previews:
        text = _PREVIEW.sub(lambda m: m.group(1) + MASK, text)
    return text


def scrub(value: Any, secrets: Iterable[Optional[str]] = (), previews: bool = True) -> Any:
    """A scrubbed copy of a tool result or log line (dicts, lists, strings; other values as they are)."""
    found = _variants(secrets)
    return _walk(value, found, previews, 0)


def _walk(value: Any, secrets: list, previews: bool, depth: int) -> Any:
    if depth > 40:
        return value
    if isinstance(value, str):
        return scrub_text(value, secrets, previews)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if secret_key(k) and isinstance(v, (str, list, tuple, dict)) and v \
                    and not (previews and str(k).lower() == "wpb_preview"):
                out[k] = MASK
            else:
                out[k] = _walk(v, secrets, previews, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_walk(v, secrets, previews, depth + 1) for v in value]
    return value


def secret_names() -> list:
    from . import settings as settings_mod
    names = [settings_mod.slot_env(n) for n in range(1, settings_mod.KEY_SLOTS + 1)]
    return [n for n in names if n] + [settings_mod.PSI_KEY_ENV, settings_mod.GSC_KEY_ENV]


def known_secrets(get_secret: Optional[Callable[[str], Optional[str]]]) -> list:
    """The values of WPBrücke's own secrets as the runtime sees them now; a getter that fails gives none
    (the shape rules still apply). Never logged."""
    if get_secret is None:
        return []
    out = []
    for name in secret_names():
        try:
            value = get_secret(name)
        except Exception:
            continue
        if value:
            out.append(str(value))
            if name.endswith("GSC_KEY"):
                out.extend(_json_secret_parts(str(value)))
    return out


def _json_secret_parts(blob: str) -> list:
    import json
    try:
        data = json.loads(blob)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return [str(v) for k, v in data.items() if (secret_key(k) or k == "private_key_id") and v]
