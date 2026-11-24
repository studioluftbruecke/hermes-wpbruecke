"""The agent lane's gates: Hermes' approval card for writes, one-use tickets, the unattended refusal,
and the guard that keeps the agent from widening its own rules (design rules 2 and 3).

``pre_tool_call`` returns Hermes' public directive ``{"action": "approve", "message", "rule_key"}``
so Hermes asks the person with the plan's text, or ``{"action": "block", "message"}``, or None. The
plugin never approves anything itself. Hermes skips a hook that raises, so the gate leaves a one-use
ticket for exactly the call it sent to the card, and the handler refuses a card-needing call without
one; ``post_approval_response`` revokes tickets on deny. Hooks are process-wide in the multiplexed
gateway, so every hook first checks that WPBrücke is on in the running profile.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

RULE_PREFIX = "plugin_rule:"
RULE_NS = "wpbruecke:"
GATED_TOOLS = ("wp_draft", "wp_sites", "wp_seo", "wp_build")


class Tickets:
    APPROVED = frozenset({"once", "session", "always"})

    def __init__(self, ttl_s: float = 900.0, clock: Optional[Callable[[], float]] = None) -> None:
        self.ttl_s, self._clock, self._t, self._lock = ttl_s, clock or time.monotonic, {}, threading.Lock()

    @staticmethod
    def key(tool: str, session_id: Any, args: Any) -> str:
        material = json.dumps({"t": tool, "s": str(session_id or ""), "a": args if isinstance(args, dict) else {}},
                              sort_keys=True, default=str)
        return hashlib.sha256(material.encode()).hexdigest()

    def issue(self, key: str, rule_key: str) -> None:
        with self._lock:
            self._t.setdefault(key, []).append((self._clock(), rule_key))

    def take(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            live = [t for t in self._t.get(key, []) if now - t[0] <= self.ttl_s]
            if not live:
                self._t.pop(key, None)
                return False
            live.pop(0)
            if live:
                self._t[key] = live
            else:
                self._t.pop(key, None)
            return True

    def revoke(self, key: str) -> None:
        with self._lock:
            self._t.pop(key, None)

    def revoke_rule(self, rule_key: str) -> None:
        with self._lock:
            for k in list(self._t):
                kept = [t for t in self._t[k] if t[1] != rule_key]
                if kept:
                    self._t[k] = kept
                else:
                    del self._t[k]


TICKETS = Tickets()


def block(code: str, message: str) -> dict:
    text = f"refused ({code}): {message}"
    if "Nothing was sent" not in text:
        text += " Nothing was sent to WordPress."
    return {"action": "block", "message": text}


class Spec:
    """How the gate sees one tool: whether a call writes, its plan text, whether it needs a card."""

    def __init__(self, gated: Callable[[dict], bool], decide: Callable[[dict, Any], dict]) -> None:
        self.gated, self.decide = gated, decide


def make_gate(specs: dict, run_context: Callable[[], Any], enabled_here: Callable[[], bool],
              tickets: Tickets = TICKETS):
    """``specs[tool].decide(args, run)`` -> {"verdict": allow|card|block, "message", "rule_key", "code"}."""

    def wpbruecke_write_gate(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any):
        spec = specs.get(tool_name)
        if spec is None:
            return None
        args = args if isinstance(args, dict) else {}
        if not spec.gated(args):
            return None
        try:
            if not enabled_here():
                return None
        except Exception:
            pass
        key = tickets.key(tool_name, session_id, args)
        tickets.revoke(key)  # first, so nothing below leaves an older ticket usable
        try:
            d = spec.decide(args, run_context())
        except Exception as e:  # fail closed
            return block("internal", f"{type(e).__name__}: {e}")
        if d["verdict"] == "block":
            return block(d.get("code", "refused"), d["message"])
        if d["verdict"] == "allow":
            return None
        rule_key = d.get("rule_key") or f"{RULE_NS}{tool_name}"
        tickets.issue(key, rule_key)
        return {"action": "approve", "message": d["message"], "rule_key": rule_key}

    return wpbruecke_write_gate


def make_observer(tickets: Tickets = TICKETS):
    def wpbruecke_approval_observer(pattern_key: str = "", choice: Any = None, **_: Any) -> None:
        rule = str(pattern_key or "")
        rule = rule[len(RULE_PREFIX):] if rule.startswith(RULE_PREFIX) else rule
        if rule.startswith(RULE_NS) and str(choice or "") not in Tickets.APPROVED:
            tickets.revoke_rule(rule)
    return wpbruecke_approval_observer


# --- The agent may not quietly loosen its own guardrails ---------------------------------------------
# Watched: this profile's config.yaml and .env, the plugin copy it loaded, WPBrücke's data folder (the
# site files carry the hard rule, publishing and automations), and `hermes` commands that change this
# profile's settings or this plugin. Reads (cat, grep, hermes config get) pass.

GUARDED_TOOLS = ("write_file", "patch", "terminal", "execute_code")
PLUGIN_DIR = "wpbruecke"
DATA_MARKER = "agent-plugin-wpbruecke"
_SETTINGS_FILES = ("config.yaml", ".env")
_SETTINGS_KEYS = ("plugins.entries.wpbruecke", "approvals.mode")
_BARE_FILE_RE = re.compile(r"(?:^|[\s'\"=(<>|;&,])(?:\./)?(?:config\.yaml|\.env)(?=$|[\s'\"),;|&>])")
_HARMLESS_REDIRECT_RE = re.compile(r"\d*>&\d+|\d*>\s*/dev/null")
_WRITE_RE = re.compile(r">|\btee\b|\bsed\b[^|;&]*\s-i|\b(rm|mv|cp|truncate|chmod|install|dd|ln)\b|"
                       r"\b(python3?|perl|ruby|node|bash|sh|zsh)\b|\bconfig\s+(set|unset|edit|migrate|reset)\b|"
                       r"\bplugins\s+(disable|enable|install|remove|uninstall)\b")
PLUGIN_ROOT = Path(__file__).resolve().parent


def settings_touch(tool_name: str, args: Any, home: Optional[Path]) -> Optional[str]:
    if tool_name not in GUARDED_TOOLS or not isinstance(args, dict):
        return None
    if tool_name in ("write_file", "patch"):
        raw = str(args.get("path") or "")
        if not raw:
            return None
        path = Path(raw).expanduser()
        text = path.as_posix()
        if DATA_MARKER in text:
            return f"edits WPBrücke's site files or desk ({raw})"
        roots = [PLUGIN_ROOT]
        if home is not None:
            link = Path(home) / "plugins" / PLUGIN_DIR
            roots += [link]
            try:
                roots.append(link.resolve())
            except OSError:
                pass
        for root in roots:
            try:
                (path.resolve() if path.is_absolute() else path).relative_to(root)
                return f"edits WPBrücke's own code ({raw})"
            except (ValueError, OSError):
                continue
        if not path.is_absolute() and f"plugins/{PLUGIN_DIR}/" in f"{text}/":
            return f"edits WPBrücke's own code ({raw})"
        if home is not None and path.is_absolute():
            return f"edits {path}" if path.resolve() in {(Path(home) / n).resolve() for n in _SETTINGS_FILES} else None
        return f"edits {raw}" if path.name in _SETTINGS_FILES else None
    text = str(args.get("command") or args.get("code") or "")
    lowered = text.lower()
    if tool_name == "terminal" and (not _WRITE_RE.search(_HARMLESS_REDIRECT_RE.sub(" ", lowered)) or "--help" in lowered):
        return None
    if DATA_MARKER in lowered:
        return "runs a command that can change WPBrücke's site files or desk"
    if str(PLUGIN_ROOT).lower() in lowered or (home is not None and f"{str(home).lower()}/plugins/{PLUGIN_DIR}" in lowered):
        return "runs a command that can change WPBrücke's own code"
    names_file = _BARE_FILE_RE.search(lowered) or (home is not None and any(
        f"{str(home).lower()}/{n}" in lowered for n in _SETTINGS_FILES))
    if names_file or any(k in lowered for k in _SETTINGS_KEYS):
        return "runs a command that touches config.yaml, .env or WPBrücke's settings"
    if re.search(r"\bhermes\b.*\b(config\s+(set|unset|edit)|plugins\s+(disable|enable|remove|uninstall))\b", lowered) \
            and ("wpbruecke" in lowered or "config" in lowered):
        return "changes Hermes settings through the hermes command"
    return None


def make_settings_guard(run_context: Callable[[], Any], enabled_here: Callable[[], bool],
                        home: Callable[[], Optional[Path]]):
    def wpbruecke_settings_guard(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any):
        if tool_name not in GUARDED_TOOLS:
            return None
        try:
            if not enabled_here():
                return None
            what = settings_touch(tool_name, args, home())
        except Exception:
            return None
        if not what:
            return None
        reason = run_context().unattended_reason()
        if reason:
            return block("guardrail", f"This call {what}, which can change WPBrücke's own rules, and {reason}, so no "
                                      "one can approve it.")
        return {"action": "approve", "rule_key": f"{RULE_NS}settings:{tool_name}",
                "message": (f"This call {what}. That can change WPBrücke's guardrails (the hard rule, publishing, "
                            "automations, which key a site uses). Approve only if you asked for it.")}
    return wpbruecke_settings_guard
