"""Who is running this turn, and may it write?

Three lanes:
- **click**: the person on the page (routes); WordPress' own login does publishing on the review screen.
- **agent**: an attended chat turn; writes within the site's access level run without a card (plan D6);
  guarded site-file fields still ask (hooks.py).
- **unattended**: cron, webhook, msgraph_webhook, api_server, single-query ``chat -q`` / ``-z``, and a
  plugin host that cannot tell (fail closed). Reads, proofs and the digest run; writes are refused
  before any request, except one thing: a **recurring-draft automation** the person wrote in a site
  file. Its Hermes cron job is named ``wpbruecke:<site>:<automation id>``; inside that cron run, and
  only there, ``wp_draft`` may create drafts (and upload their images) on that one site at the drafts
  or publish level, and publish them only when the automation says ``publish: true`` and the level is
  publish (plan D7), and then only a post that this same cron run created (its desk item records the
  run as ``automation_run``; review N3). Hermes binds the cron identity in a ContextVar the model cannot reach
  (``cron.execution_identity``).

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
    cron_execution_id: str = "" # the cron fire's execution id (one per run of the job)
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

    def run_key(self) -> str:
        """``<job name>#<execution id>`` for a WPBrücke automation's cron run, else "" (no id, no key)."""
        if self.automation() is None or not self.cron_execution_id:
            return ""
        return f"{self.cron_job_name}#{self.cron_execution_id}"


def run_context_from(ctx: Any) -> RunContext:
    cron_exec = _ctx_call(ctx, "current_cron_execution")
    job_name = str(getattr(cron_exec, "job_name", "") or "") if cron_exec is not None else ""
    execution_id = str(getattr(cron_exec, "execution_id", "") or "") if cron_exec is not None else ""
    cron = cron_exec is not None or _session_env("HERMES_CRON_SESSION").strip().lower() in _TRUTHY
    platform = str(_ctx_call(ctx, "session_surface") or _session_env("HERMES_SESSION_PLATFORM")).strip().lower()
    finite = _finite_turn()
    single = bool(finite) or _session_env("HERMES_SINGLE_QUERY_SESSION").strip().lower() in _TRUTHY
    host_blind = (os.environ.get(HOST_PROCESS_ENV, "").strip().lower() in _TRUTHY and platform in HOST_BLIND_SURFACES)
    return RunContext(cron_job_name=job_name, cron_execution_id=execution_id, cron=cron, single_query=single, platform=platform,
                      unknown=finite is None and not host_blind, host_blind=host_blind)


AUTOMATION_ACTIONS = ("create", "upload_media")


def automation_permission(run: RunContext, site: Optional[dict], action: str) -> "tuple[bool, str]":
    """May this unattended run write ``action`` on ``site``? (allowed, reason). Reads only the site file, so
    a refusal comes before any request. The level itself is enforce.require's: an automation writes at
    drafts or publish; it publishes only when its entry says ``publish: true`` (D7), and ``publish``
    needs the publish level like any other publish."""
    auto = run.automation()
    if auto is None:
        return False, "no WPBrücke automation runs this job"
    site_id, aid = auto
    if site is None or site_id != site.get("id"):
        return False, f"the automation {aid} belongs to site {site_id}, not {(site or {}).get('id')}"
    entry = next((a for a in site.get("automations") or [] if a.get("id") == aid), None)
    if entry is None:
        return False, f"site {site_id} has no automation {aid} in its site file"
    if not entry.get("enabled", True):
        return False, f"the automation {aid} is switched off in the site file"
    if action == "publish" and entry.get("publish") is True:
        return True, f"automation {aid} on {site_id} (publish: true)"
    if action not in AUTOMATION_ACTIONS:
        why = "an automation may only create drafts (and upload their images)"
        if action == "publish":
            why += f"; automation {aid} does not say publish: true, so its post stays a draft"
        return False, why
    return True, f"automation {aid} on {site_id}"


def automation_may_publish(run: RunContext, item: Optional[dict], wp_id: Any) -> "tuple[bool, str]":
    """May this automation run publish ``wp_id``? Only a post its own run created: ``item`` is the desk
    item for that id, and its ``automation_run`` must be this run's key (review N3)."""
    auto = run.automation()
    aid = auto[1] if auto else "?"
    what = f"post {wp_id}" if wp_id not in (None, "") else "that post"
    key = run.run_key()
    if not key:
        return False, (f"An automation publishes only the post its own run created, and this run of automation {aid} "
                       "has no execution id to tell its own posts by")
    if (item is None or item.get("automation_run") != key or item.get("kind") not in ("new_post", "new_page")
            or (wp_id not in (None, "") and str(item.get("wp_id")) != str(wp_id))):
        return False, (f"An automation publishes only the post its own run created; {what} was not created by this "
                       f"run of automation {aid}")
    return True, f"{what} was created by this run of automation {aid}"
