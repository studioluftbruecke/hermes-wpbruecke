"""Who is running this turn, and may it write?

Three lanes:
- **click**: the person on the page (routes); WordPress' own login does publishing on the review screen.
- **agent**: an attended chat turn; writes go through Hermes' approval card when the site's key can
  change live content, or always when the site says ``agent_writes: always`` (hooks.py).
- **unattended**: cron, webhook, msgraph_webhook, api_server, single-query ``chat -q`` / ``-z``, and a
  plugin host that cannot tell (fail closed). Reads, proofs and the digest run; writes are refused
  before any request, except one thing: a **recurring-draft automation** the person wrote in a site
  file (D18 a, provisional). Its Hermes cron job is named ``wpbruecke:<site>:<automation id>``; inside
  that cron run, and only there, ``wp_draft`` may create drafts (and upload their images) on that one
  site, with a key that cannot publish or edit live posts. Hermes binds the cron identity in a
  ContextVar the model cannot reach (``cron.execution_identity``).

Restated from Hermes' own rule (``tools/approval_context.py`` is not a public import).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

UNATTENDED_PLATFORMS = frozenset({"webhook", "msgraph_webhook", "api_server"})
HOST_BLIND_SURFACES = frozenset({"", "cli", "oneshot"})
HOST_PROCESS_ENV = "HERMES_PLUGIN_HOST_PROCESS"
JOB_PREFIX = "wpbruecke:"
_TRUTHY = {"1", "true", "yes", "on"}


def _session_env(name: str) -> str:
    try:
        from gateway.session_context import get_session_env
        value = get_session_env(name, "")
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(name, "") or ""


def _finite_turn() -> Optional[bool]:
    """True on a gateway-admitted ``chat -q`` / ``-z`` turn; None when Hermes' marker cannot be read."""
    try:
        from gateway.session_finite import finite_turn_required
    except ImportError:
        return None
    try:
        return finite_turn_required() is True
    except Exception:
        return None


def _ctx_call(ctx: Any, name: str) -> Any:
    try:
        return getattr(ctx, name)() if ctx is not None and hasattr(ctx, name) else None
    except Exception:
        return None


@dataclass(frozen=True)
class RunContext:
    cron_job_name: str = ""     # the cron job's name when this turn is a cron run
    cron: bool = False
    single_query: bool = False
    platform: str = ""
    unknown: bool = False       # Hermes' one-shot marker could not be read: fail closed
    host_blind: bool = False    # plugin-host isolation on a surface where -z cannot be told from chat

    def unattended_reason(self) -> Optional[str]:
        if self.cron:
            return "this is a scheduled job"
        if self.platform in UNATTENDED_PLATFORMS:
            return f"this session runs on the unattended {self.platform} surface"
        if self.single_query:
            return "this is a single-query run (hermes chat -q or -z)"
        if self.unknown:
            return ("WPBrücke cannot tell whether a person is present (this Hermes moved gateway.session_finite; "
                    "update WPBrücke)")
        if self.host_blind:
            return ("plugins run isolated in the plugin host here, where a one-shot run cannot be told from a chat "
                    "(set plugins.isolation: in_process for approval cards on the command line)")
        return None

    def automation(self) -> Optional["tuple[str, str]"]:
        """(site, automation id) when this is a cron run of a WPBrücke recurring-draft job, else None."""
        if not (self.cron and self.cron_job_name.startswith(JOB_PREFIX)):
            return None
        parts = self.cron_job_name[len(JOB_PREFIX):].split(":")
        return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def run_context_from(ctx: Any) -> RunContext:
    cron_exec = _ctx_call(ctx, "current_cron_execution")
    job_name = str(getattr(cron_exec, "job_name", "") or "") if cron_exec is not None else ""
    cron = cron_exec is not None or _session_env("HERMES_CRON_SESSION").strip().lower() in _TRUTHY
    platform = str(_ctx_call(ctx, "session_surface") or _session_env("HERMES_SESSION_PLATFORM")).strip().lower()
    finite = _finite_turn()
    single = bool(finite) or _session_env("HERMES_SINGLE_QUERY_SESSION").strip().lower() in _TRUTHY
    host_blind = (os.environ.get(HOST_PROCESS_ENV, "").strip().lower() in _TRUTHY and platform in HOST_BLIND_SURFACES)
    return RunContext(cron_job_name=job_name, cron=cron, single_query=single, platform=platform,
                      unknown=finite is None and not host_blind, host_blind=host_blind)


def automation_permission(run: RunContext, site: dict, facts: dict, action: str) -> "tuple[bool, str]":
    """May this unattended run create a draft on ``site``? (allowed, reason)."""
    from .facts import can_change_live
    auto = run.automation()
    if auto is None:
        return False, "no WPBrücke automation runs this job"
    site_id, aid = auto
    if site_id != site.get("id"):
        return False, f"the automation {aid} belongs to site {site_id}, not {site.get('id')}"
    entry = next((a for a in site.get("automations") or [] if a.get("id") == aid), None)
    if entry is None:
        return False, f"site {site_id} has no automation {aid} in its site file"
    if not entry.get("enabled", True):
        return False, f"the automation {aid} is switched off in the site file"
    if action not in ("create", "upload_media"):
        return False, "an automation may only create drafts (and upload their images)"
    rights = facts.get("rights")
    if not rights:
        return False, "the site's key could not be checked, so the automation may not write"
    if can_change_live(rights):
        return False, ("the site's key can publish or edit live posts; unattended drafts need a key that cannot "
                       "(the connector's drafter role or Contributor)")
    return True, f"automation {aid} on {site_id}"
