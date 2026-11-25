"""The connection check of one settings group (contract § 9).

``check(group, record, sites, transport, password=...)`` walks the steps in order and stops at the
first state that needs the person. Nothing carries the password before step 6, and step 6 runs only
when ``pairing`` allows the check to send it. Every state, its sentence and its next action live in
one table, ``STATES``. ``run_check(rt, n)`` adds the side effects: the pairing record, the site
file's ``username``, and ``groups/<n>.check.json`` (the page derives its state from it after it
remounts).
"""
from __future__ import annotations

import xmlrpc.client
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from . import access as access_mod
from . import groups as groups_mod
from .client import WordPressClient, WPError, is_loopback
from .store import now_iso

CONNECTOR_NS = "wpbruecke/v1"
HTACCESS_LINE = 'SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1'
WRONG_CREDENTIAL_CODES = ("incorrect_password", "invalid_username", "invalid_email")


@dataclass(frozen=True)
class State:
    ok: bool
    message: str          # str.format template over the result's fields plus n, host, detail, ...
    label: str
    action: str           # settings | check | confirm | open | remove | none


STATES = {
    "free": State(False, "Site {n} has no address yet.", "Open the settings", "settings"),
    "bad_address": State(False, "Site {n} · address is not a web address ({detail}).", "Fix the address", "settings"),
    "no_user": State(False, "Site {n} has an address but no WordPress user. Fill Site {n} · WordPress user.",
                     "Open the settings", "settings"),
    "no_password": State(False, "Site {n} has no application password yet. Paste it into Site {n} · application "
                                "password.", "Open the settings", "settings"),
    "host_changed": State(False, "Site {n} now points to {host}; its stored password belongs to {old_host}. Paste the "
                                 "new site's password into Site {n}.", "Open the settings", "settings"),
    "not_https": State(False, "{host} is not an https address, and WPBrücke sends a password only over https. Use the "
                              "https address in Site {n} · address.", "Open the settings", "settings"),
    "unreachable": State(False, "{host} did not answer ({detail}). Check the address, or try again when the site is "
                                "up.", "Check again", "check"),
    "http_auth": State(False, "{host} asks for a web server password before WordPress answers. Lift it for /wp-json/ "
                              "and /xmlrpc.php, then check again.", "Check again", "check"),
    "plain_permalinks": State(False, "{host} uses Plain permalinks, so WordPress' REST API has no address. In "
                                     "wp-admin: Settings, Permalinks: pick any option except Plain.", "Check again",
                              "check"),
    "no_rest": State(False, "{host} does not answer as WordPress (no REST API at /wp-json/). Check Site {n} · address.",
                     "Open the settings", "settings"),
    "use_address": State(False, "{host} sends WordPress on to {suggested}. Put that address into Site {n} · address.",
                         "Open the settings", "settings"),
    "app_passwords_off": State(False, "Application passwords are turned off on {host} (often by a security plugin). "
                                      "Turn them on, then check again.", "Check again", "check"),
    "duplicate": State(False, "{host} is already connected as {other}. Use that one, or empty Site {n} · address.",
                       "Open the settings", "settings"),
    "wrong_credentials": State(False, "WordPress refused the user or the application password of Site {n}. Check Site "
                                      "{n} · WordPress user and paste the password again.", "Open the settings",
                               "settings"),
    "header_stripped": State(False, "The user and password are right, but the web server drops the login header. Add "
                                    "this line to WordPress' .htaccess: " + HTACCESS_LINE, "Check again", "check"),
    "cannot_tell": State(False, "WordPress refused the login, and with XML-RPC off WPBrücke cannot tell why: either the "
                                "user or password is wrong (check Site {n}), or the web server drops the login header "
                                "(add to .htaccess: " + HTACCESS_LINE + ").", "Open the settings", "settings"),
    "no_role_here": State(False, "{username} has no role on this site of the network. Add the user to this site in "
                                 "wp-admin, Users.", "Check again", "check"),
    "refused": State(False, "{reason}", "Open the settings", "settings"),
    "needs_confirm": State(True, "Connected as {username} at {host}. WordPress lets this user {level_words}. Hermes "
                                 "will do exactly that.", "Confirm", "confirm"),
    "confirmed": State(True, "Connected as {username} at {host}. Hermes can {level_words}.", "", "none"),
    "lowered": State(True, "WordPress now lets {username} only {level_words} at {host}; Hermes follows the narrower "
                           "role.", "", "none"),
    "stopped": State(False, "WPBrücke stopped {host}: {reason}. WordPress now lets {username} {level_words}.",
                     "Confirm again", "confirm"),
    "group_empty": State(False, "Site {n} has no address any more; its site file {site_id} stays. Finish removing it, "
                                "or fill Site {n} again.", "Finish removing", "remove"),
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def result(n: int, state: str, host: str = "", site_id: Optional[str] = None, settings_href: str = "",
           href: Optional[str] = None, **fields: Any) -> dict:
    """A CheckResult with ``state``'s sentence and next action from STATES."""
    spec = STATES[state]
    out = {"group": n, "site_id": site_id, "host": host, "state": state, "ok": spec.ok, "message": "",
           "next": {"label": spec.label, "action": spec.action}, "username": None, "role": None, "level": None,
           "level_words": None, "caps_fp": None, "uploads": None, "connector": None, "access": None,
           "effective": None, "migrated": None, "checked_at": now_iso()}
    for k in list(out):
        if k in fields:
            out[k] = fields.pop(k)
    out["message"] = spec.message.format_map(_Safe({**out, "n": n, **fields}))
    link = href or (settings_href if spec.action == "settings" else None)
    if link:
        out["next"]["href"] = link
    return out


_FRAME = ("group", "state", "ok", "message", "next", "host", "site_id", "checked_at")


def judge(res: dict, access: Optional[dict], reason: str = "", settings_href: str = "") -> dict:
    """Step 8: the result's state from the live level and the recorded access."""
    fields = {k: v for k, v in res.items() if k not in _FRAME}
    fields["access"] = access

    def to(state: str, **extra: Any) -> dict:
        return result(res["group"], state, res.get("host", ""), res.get("site_id"), settings_href=settings_href,
                      **fields, **extra)

    level = res.get("level")
    if level not in access_mod.LEVELS:
        if access:  # a confirmed site whose user now has refused rights or no role stops (contract § 6)
            fields.update(level=None, effective="stopped", level_words=_refused_words(level, reason))
            return to("stopped", reason=_refused_reason(access, level, reason, res.get("role")))
        fields.update(level=None, effective=None)
        return to("refused", reason=reason or access_mod.REFUSED_ADMIN)
    eff, _ = access_mod.effective(access, level, res.get("caps_fp") or "")
    fields["effective"] = eff
    if eff == "unconfirmed":
        return to("needs_confirm")
    if eff == "stopped":
        return to("stopped", reason=access_mod.stop_reason(access, level, res.get("caps_fp") or ""))
    return to(eff)


def _refused_words(level: Optional[str], reason: str) -> str:
    if level == "none":
        return "do nothing on this site (no role)"
    if reason == access_mod.REFUSED_READER:
        return "write, because the reader role is not held read-only"
    if reason == access_mod.REFUSED_MIXED_READER:
        return "use a second role beside the reader role, which WPBrücke refuses"
    return "administer the site, which WPBrücke refuses"


def _refused_reason(access: dict, level: Optional[str], reason: str, role: Optional[str]) -> str:
    reader = access.get("role") == access_mod.READER_ROLE
    if level == "none":
        return ("the user has no role on the site any more (the WPBrücke connector removes the reader role while it "
                "is deactivated)") if reader else "the user has no role on this site any more"
    if reason == access_mod.REFUSED_READER:
        return "the WPBrücke connector is off or older than 0.2.1, so the reader role is not held read-only"
    if reason == access_mod.REFUSED_MIXED_READER:
        return "the user has the WPBrücke reader role and another role; give it one role"
    return f"the user now has rights Hermes refuses ({role or 'its role'})"


@dataclass
class Outcome:
    result: dict
    record: Optional[groups_mod.PairingRecord] = None
    username: Optional[str] = None


def _basic_challenge(resp) -> bool:
    return "basic" in str(resp.header("WWW-Authenticate") or "").lower()


def _json(resp) -> Any:
    try:
        return resp.json()
    except WPError:
        return None


def _suggest(base: str, location: str) -> str:
    target = urljoin(base + "/", location)
    p = urlparse(target)
    path = p.path
    for tail in ("/wp-json/", "/wp-json"):
        if path.endswith(tail):
            path = path[: -len(tail)]
    return f"{p.scheme}://{p.netloc}{path}".rstrip("/")


def _xmlrpc_profile(client: WordPressClient, user: str, password: str) -> str:
    """'ok', 'refused' (fault 403) or 'off' (XML-RPC unavailable or anything else)."""
    body = xmlrpc.client.dumps((1, user, password), "wp.getProfile").encode()
    try:
        resp = client.raw("POST", "/xmlrpc.php", data=body, headers={"Content-Type": "text/xml"}, auth=False)
    except WPError:
        return "off"
    if resp.status != 200:
        return "off"
    try:
        xmlrpc.client.loads(resp.body)
        return "ok"
    except xmlrpc.client.Fault as f:
        return "refused" if f.faultCode == 403 else "off"
    except Exception:
        return "off"


def check(group: groups_mod.Group, record: Optional[groups_mod.PairingRecord], sites: list,
          transport: Any = None, *, password: Optional[str] = None, others: Any = (), connect_ip: str = "",
          settings_href: str = "", timeout: float = 20.0) -> Outcome:
    """Steps 1 to 8 of contract § 9. ``sites``: every site file; ``others``: the other groups."""
    n, host = group.n, group.host
    mine = next((s for s in sites if s.get("key_slot") == n), None)
    site_id = mine["id"] if mine else None

    def stop(state: str, **fields: Any) -> Outcome:
        return Outcome(result(n, state, host, site_id, settings_href=settings_href, **fields))

    if group.error:
        return stop("bad_address", detail=group.error)
    if not group.url:
        return stop("group_empty" if site_id else "free")
    # 1. never a password over http, except to this computer
    if urlparse(group.url).scheme != "https" and not is_loopback(host):
        return stop("not_https")
    # 2. the REST index, without the key and without following a redirect
    public = WordPressClient(group.url, connect_ip=connect_ip, timeout=timeout, transport=transport)
    try:
        resp = public.raw("GET", "/wp-json/", auth=False)
    except WPError as e:
        return stop("unreachable", detail=e.message)
    if 300 <= resp.status < 400:
        return stop("use_address", suggested=_suggest(group.url, resp.header("Location") or ""))
    if resp.status == 401 and _basic_challenge(resp):
        return stop("http_auth")
    if resp.status == 404:
        try:
            plain = public.raw("GET", "/?rest_route=/", auth=False)
        except WPError:
            plain = None
        if plain is not None and plain.status == 200 and isinstance(_json(plain), dict):
            return stop("plain_permalinks")
        return stop("no_rest")
    index = _json(resp) if resp.status == 200 else None
    if not isinstance(index, dict):
        return stop("no_rest")
    # 3. application passwords on, and the address WordPress calls its own
    auth = index.get("authentication")
    if not isinstance(auth, dict) or "application-passwords" not in auth:
        return stop("app_passwords_off")
    for key in ("url", "home"):
        own = str(index.get(key) or "")
        if own and groups_mod.host(own) and groups_mod.host(own) != host:
            return stop("use_address", suggested=own.rstrip("/"))
    # 4. one site per host: another group, or another keyed site file
    other = next((f"Site {g.n}" for g in others if g.n != n and g.url and g.host == host), None)
    if other is None:
        other = next((f"site {s['id']}" for s in sites if s.get("key_slot") not in (0, n)
                      and groups_mod.host(s.get("url", "")) == host), None)
    if other:
        return stop("duplicate", other=other)
    # 5. may the check send this group's password to this host?
    state = groups_mod.pairing(group, record)
    if state not in groups_mod.CHECK_MAY_SEND:
        return stop(state, old_host=record.host if record else "another site")
    # 6. users/me with the key
    keyed = WordPressClient(group.url, group.user, password, connect_ip=connect_ip, timeout=timeout,
                            transport=transport)
    if not keyed.authenticated:
        return stop("no_password")
    try:
        me_resp = keyed.raw("GET", "/wp-json/wp/v2/users/me", params={"context": "edit"})
    except WPError as e:
        return stop("unreachable", detail=e.message)
    if me_resp.status == 401:
        body = _json(me_resp)
        code = str(body.get("code") or "") if isinstance(body, dict) else ""
        if code in WRONG_CREDENTIAL_CODES:
            return stop("wrong_credentials")
        verdict = _xmlrpc_profile(public, group.user, password or "")
        return stop({"ok": "header_stripped", "refused": "wrong_credentials"}.get(verdict, "cannot_tell"))
    me = _json(me_resp) if me_resp.status == 200 else None
    if not isinstance(me, dict):
        return stop("unreachable", detail=f"users/me answered HTTP {me_resp.status}")
    # 7. who the user is and what its role allows
    username = str(me.get("username") or me.get("slug") or group.user)
    roles = [str(r) for r in me.get("roles") or []]
    access = (mine or {}).get("access")
    if not roles and not access:
        return stop("no_role_here", username=username)
    caps = me.get("capabilities") or {}
    connector = None
    if CONNECTOR_NS in (index.get("namespaces") or []):
        try:
            info = keyed.get("/wp-json/wpbruecke/v1/info")
        except WPError:
            info = None
        if isinstance(info, dict) and info.get("version"):
            connector = str(info["version"])
    level, reason = access_mod.classify(roles, caps, connector) if roles else ("none", "no role")
    try:
        intro = keyed.get("/wp-json/wp/v2/users/me/application-passwords/introspect")
    except WPError:
        intro = None
    uuid = str(intro["uuid"]) if isinstance(intro, dict) and intro.get("uuid") else None
    rec = groups_mod.new_record(group, site_id or "", username, uuid)
    if level == "none" and not access:
        return Outcome(result(n, "no_role_here", host, site_id, settings_href=settings_href, username=username), rec,
                       username)
    base = result(n, "needs_confirm", host, site_id, username=username, role=roles[0] if roles else None, level=level,
                  caps_fp=access_mod.caps_fingerprint(caps, roles), uploads=access_mod.uploads(caps),
                  connector=connector, level_words=access_mod.level_words(level, caps))
    # 8. compare with what the person confirmed
    return Outcome(judge(base, access, reason, settings_href), rec, username)


def run_check(rt: Any, n: int, settings_href: str = "", legacy: Optional[Callable[[str], Any]] = None) -> dict:
    """Check group n now, persist what it proved, and return the CheckResult. A check forgets the cached
    live reading of the group's site, so the next action reads the role again."""
    from . import enforce
    group = rt.group(n)
    record = rt.records.get(n)
    sites = [s for s in rt.sites.all() if "error" not in s]
    mine = next((s for s in sites if s.get("key_slot") == n), None)
    password = rt.group_password(n) if groups_mod.pairing(group, record) in groups_mod.CHECK_MAY_SEND else None
    out = check(group, record, sites, rt.transport, password=password, others=rt.groups(),
                connect_ip=(mine or {}).get("connect_ip") or "", settings_href=settings_href,
                timeout=rt.client_timeout)
    if out.record is not None:
        rt.records.put(out.record)
    if mine is not None and out.username and mine.get("username") != out.username:
        rt.sites.update(mine["id"], {"username": out.username}, "check")
    res = out.result
    if mine is not None and legacy is not None:
        res["migrated"] = legacy(mine["id"])
    rt.records.put_check(n, res)
    if mine is not None:
        enforce.forget(rt, mine["id"])
    return res
