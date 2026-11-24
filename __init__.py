"""WPBrücke · for WordPress: run one WordPress site or many from Hermes.

Hermes writes drafts, proves every change in a real browser, checks SEO, and leaves publishing to a
person. This file only wires: tools, hooks, the ``@wp:`` reference, the CLI command and
the bundled skill. Sibling modules are imported lazily (``_mod``) and once in ``register()`` so a
broken install fails at load. Secrets are read at call time through Hermes' secret scope
(``agent.secret_scope.get_secret``), never from ``os.environ`` or a file (catalog rule 11).
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Callable, Optional

PLUGIN = "wpbruecke"
TOOLSET = "wpbruecke"
EMOJI = "🌐"

logger = logging.getLogger(__name__)

SettingGetter = Callable[[str, Any], Any]
SecretGetter = Callable[[str], Optional[str]]

_LIVE: dict = {}  # this load's getters and data dir; the @wp: provider and CLI read them at call time


def _mod(name: str):
    return importlib.import_module(f".{name}", __package__ or __name__)


def _default_secret(name: str) -> Optional[str]:
    from agent.secret_scope import get_secret  # Hermes' profile-scoped secret store

    return get_secret(name)


def _state_dir(ctx) -> Any:
    try:
        state = ctx.state
    except Exception:
        return None
    if hasattr(state, "get") and hasattr(state, "set") and hasattr(state, "data_dir"):
        try:
            return state.data_dir
        except Exception:
            return None
    return None


def make_runtime(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret, **kw: Any):
    return _mod("runtime").Runtime(get_setting, get_secret, data_dir, **kw)


def _json(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _guarded(name: str, body: Callable[[], Any]) -> str:
    try:
        return _json(body())
    except Exception as e:  # never a traceback for the model
        logger.warning("wpbruecke %s failed", name, exc_info=True)
        return _json({"ok": False, "error": "internal", "message": f"{type(e).__name__}: {e}"})


def _no_data() -> dict:
    return {"ok": False, "error": "no_state", "message": "WPBrücke has no plugin data folder in this context."}


# --- M1: doctor and sites --------------------------------------------------------------------------

def make_doctor_handler(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret, **kw):
    def handler(args: dict, **_: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            return _mod("tools.doctor").run(rt, args.get("site") or None, bool(args.get("refresh", True)))
        return _guarded("wp_doctor", body)
    return handler


def make_sites_handler(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret,
                       author: str = "hermes", **kw):
    def handler(args: dict, **_: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            return _mod("tools.sites").handle(rt, args, author=author)
        return _guarded("wp_sites", body)
    return handler


def register(ctx) -> None:
    for name in ("store", "yamlish", "settings", "sites", "client", "facts", "features", "runtime",
                 "tools.doctor", "tools.sites", "cli", "profile", "reference", "tools.content", "lanes", "desk", "hooks",
                 "tools.draft", "tools.desk", "tick", "api", "tools.proof", "tools.seo", "tools.build",
                 "proof.checks", "proof.diff", "proof.runner", "seo.post", "seo.site", "seo.fields", "gp.blocks", "gp.build"):
        _mod(name)  # a broken install fails at load, not at the first call

    def get_setting(key: str, default: Any = None) -> Any:
        return ctx.get_config(key, default)

    data_dir = _state_dir(ctx)
    _LIVE.update(get_setting=get_setting, data_dir=data_dir)
    doctor, sites = _mod("tools.doctor"), _mod("tools.sites")
    ctx.register_tool(name="wp_doctor", toolset=TOOLSET, schema=doctor.SCHEMA,
                      handler=make_doctor_handler(get_setting, data_dir), description=doctor.SCHEMA["description"],
                      emoji=EMOJI)
    ctx.register_tool(name="wp_sites", toolset=TOOLSET, schema=sites.SCHEMA,
                      handler=make_gated_sites_handler(get_setting, data_dir, _run_context_fn(ctx)),
                      description=sites.SCHEMA["description"],
                      emoji=EMOJI)
    cli = _mod("cli")
    ctx.register_cli_command(name="wpbruecke", help="WPBrücke: your WordPress sites from Hermes",
                             setup_fn=cli.setup, handler_fn=cli.make_handler(get_setting, data_dir, _default_secret),
                             description="WPBrücke commands (doctor, tick, watch-setup, connector-zip).")
    _register_content(ctx, get_setting, data_dir)


# --- M3: content reads, @wp: references, the bundled skill ------------------------------------------

def make_content_handler(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret, **kw):
    def handler(args: dict, **_: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            return _mod("tools.content").handle(make_runtime(get_setting, data_dir, get_secret, **kw), args)
        return _guarded("wp_content", body)
    return handler


def make_reference_provider(module_name: str):
    """A ContextReferenceProvider for `@wp:`; None outside Hermes. Hermes cannot unregister a prefix, so
    the provider resolves the plugin module by name on every call (a reload's code answers) and returns
    None where the plugin is off in the running profile."""
    try:
        from agent.context_references import ContextCompletionItem, ContextReferenceProvider
    except ImportError:
        return None
    import asyncio
    import sys

    def live():
        module = sys.modules.get(module_name)
        return module if module is not None and getattr(module, "_LIVE", {}).get("data_dir") else None

    ref = _mod("reference")

    def runtime_of(module):
        return lambda: module.make_runtime(module._LIVE["get_setting"], module._LIVE["data_dir"])

    class WordPressReferences(ContextReferenceProvider):
        prefix = ref.PREFIX
        description = ref.DESCRIPTION

        async def expand(self, target: str):
            module = live()
            if module is None or not importlib.import_module(".profile", module_name).enabled_here():
                return None
            refmod = importlib.import_module(".reference", module_name)
            return await asyncio.to_thread(refmod.expand, target, runtime_of(module))

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            module = live()
            if module is None or not importlib.import_module(".profile", module_name).enabled_here():
                return []
            refmod = importlib.import_module(".reference", module_name)
            rows = await asyncio.to_thread(refmod.completions, query, runtime_of(module), limit)
            return [ContextCompletionItem(text=t, display=d, meta=m) for t, d, m in rows]

    return WordPressReferences()


SKILL_NAME = "wpbruecke"


def _register_content(ctx, get_setting: SettingGetter, data_dir: Any) -> None:
    from pathlib import Path
    content = _mod("tools.content")
    ctx.register_tool(name="wp_content", toolset=TOOLSET, schema=content.SCHEMA,
                      handler=make_content_handler(get_setting, data_dir), description=content.SCHEMA["description"],
                      emoji=EMOJI)
    skill = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
    if hasattr(ctx, "register_skill") and skill.exists():
        try:
            ctx.register_skill(SKILL_NAME, skill, "Work on WordPress sites with WPBrücke: drafts only, proof, SEO.")
        except ValueError:
            pass  # already registered by an earlier load in this process
    provider = make_reference_provider(__name__)
    if provider is not None and hasattr(ctx, "register_context_reference"):
        try:
            ctx.register_context_reference(provider)  # a second load's duplicate is refused with a warning
        except Exception:
            logger.debug("wpbruecke: @wp: provider already registered", exc_info=True)
    _register_writes(ctx, get_setting, data_dir)


# --- M4: desk, drafts, the write gate, lanes, the own-config guard ----------------------------------

def _run_context_fn(ctx):
    def run():
        return _mod("lanes").run_context_from(ctx)
    return run


def decide_draft(rt, args: dict, run) -> dict:
    """The gate's verdict for one wp_draft call (also used by the handler for the unattended lane)."""
    draft, lanes = _mod("tools.draft"), _mod("lanes")
    try:
        p = draft.plan(rt, args)
    except draft.Refused as r:
        return {"verdict": "block", "code": r.code, "message": r.message}
    reason = run.unattended_reason()
    if reason:
        ok, why = lanes.automation_permission(run, p.site, p.facts, p.action)
        if ok:
            return {"verdict": "allow", "automation": why}
        return {"verdict": "block", "code": "unattended",
                "message": f"Writes to WordPress need a person, and {reason} ({why}). Nothing was sent. Reads, "
                           "proofs and SEO checks still work."}
    if not p.needs_card:
        return {"verdict": "allow"}
    return {"verdict": "card", "message": p.approval_text(), "rule_key": f"wpbruecke:wp_draft:{p.site['id']}"}


def decide_sites(rt, args: dict, run) -> dict:
    touched = _mod("tools.sites").touches_guarded(args)
    reason = run.unattended_reason()
    if reason:
        return {"verdict": "block", "code": "unattended",
                "message": f"Changing {', '.join(touched)} in a site file needs a person, and {reason}."}
    site = args.get("site") or (args.get("fields") or {}).get("id") or "?"
    preview = ""
    if args.get("action") == "update":
        res = _mod("tools.sites").handle(rt, {**args, "preview": True})
        preview = "\n".join(res.get("diff") or []) if res.get("ok") else res.get("message", "")
    elif args.get("action") == "add":
        preview = "\n".join(f"{k}: {v}" for k, v in (args.get("fields") or {}).items())
    else:
        preview = f"{args.get('action')} the whole site file" + (f" (version {args.get('version')})" if args.get("version") else "")
    return {"verdict": "card", "rule_key": f"wpbruecke:wp_sites:{site}",
            "message": (f"This changes WPBrücke's rules for site {site} ({', '.join(touched)}):\n{preview}\n"
                        "These fields decide what Hermes may do on the site.")}


def make_draft_handler(get_setting: SettingGetter, data_dir: Any, run_context, get_secret: SecretGetter = _default_secret,
                       tickets: Any = None, **kw):
    hooks = _mod("hooks")
    tickets = tickets if tickets is not None else hooks.TICKETS

    def handler(args: dict, **kwargs: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            key = tickets.key("wp_draft", kwargs.get("session_id"), args)
            run = run_context()
            d = decide_draft(rt, args, run)
            if d["verdict"] == "block":
                tickets.revoke(key)
                return {"ok": False, "error": d.get("code", "refused"), "message": d["message"], "sent": False}
            approved = tickets.take(key) or d["verdict"] == "allow"
            out = _mod("tools.draft").run(rt, args, approved=approved)
            if d.get("automation") and out.get("ok"):
                out["lane"] = f"unattended, allowed by {d['automation']}"
            return out
        return _guarded("wp_draft", body)
    return handler


def make_gated_sites_handler(get_setting: SettingGetter, data_dir: Any, run_context,
                             get_secret: SecretGetter = _default_secret, tickets: Any = None, **kw):
    hooks = _mod("hooks")
    tickets = tickets if tickets is not None else hooks.TICKETS

    def handler(args: dict, **kwargs: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            if _mod("tools.sites").touches_guarded(args):
                key = tickets.key("wp_sites", kwargs.get("session_id"), args)
                d = decide_sites(rt, args, run_context())
                if d["verdict"] == "block":
                    tickets.revoke(key)
                    return {"ok": False, "error": d["code"], "message": d["message"], "sent": False}
                if not tickets.take(key):
                    return {"ok": False, "error": "not_approved", "message": (
                        "Changing these site-file fields needs the person's approval in Hermes and no card was "
                        "answered for this exact call. Nothing was saved.")}
            return _mod("tools.sites").handle(rt, args, author="hermes")
        return _guarded("wp_sites", body)
    return handler


def make_desk_handler(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret, **kw):
    def handler(args: dict, **_: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            return _mod("tools.desk").handle(make_runtime(get_setting, data_dir, get_secret, **kw), args)
        return _guarded("wp_desk", body)
    return handler


def _gate_specs(get_setting: SettingGetter, data_dir: Any) -> dict:
    hooks, sites_tool = _mod("hooks"), _mod("tools.sites")

    def rt():
        return make_runtime(get_setting, data_dir)
    specs = {
        "wp_draft": hooks.Spec(lambda a: True, lambda a, run: decide_draft(rt(), a, run)),
        "wp_sites": hooks.Spec(lambda a: bool(sites_tool.touches_guarded(a)), lambda a, run: decide_sites(rt(), a, run)),
    }
    _SPECS.update(specs)
    return _SPECS


_SPECS: dict = {}  # later milestones add wp_seo and wp_build specs here


def _register_writes(ctx, get_setting: SettingGetter, data_dir: Any) -> None:
    hooks, profile = _mod("hooks"), _mod("profile")
    run_context = _run_context_fn(ctx)
    specs = _gate_specs(get_setting, data_dir)
    ctx.register_hook("pre_tool_call", hooks.make_gate(specs, run_context, profile.enabled_here))
    ctx.register_hook("post_approval_response", hooks.make_observer())
    ctx.register_hook("pre_tool_call", hooks.make_settings_guard(run_context, profile.enabled_here, profile.current_home))
    draft, desk = _mod("tools.draft"), _mod("tools.desk")
    ctx.register_tool(name="wp_draft", toolset=TOOLSET, schema=draft.SCHEMA,
                      handler=make_draft_handler(get_setting, data_dir, run_context),
                      description=draft.SCHEMA["description"], emoji=EMOJI)
    ctx.register_tool(name="wp_desk", toolset=TOOLSET, schema=desk.SCHEMA,
                      handler=make_desk_handler(get_setting, data_dir), description=desk.SCHEMA["description"],
                      emoji=EMOJI)
    _register_proof_seo_build(ctx, get_setting, data_dir, run_context)


# --- M5, M6, M9: proof, SEO, the GeneratePress pack ---------------------------------------------------

def make_proof_handler(get_setting: SettingGetter, data_dir: Any, get_secret: SecretGetter = _default_secret, **kw):
    def handler(args: dict, **_: Any):
        args = args if isinstance(args, dict) else {}
        if not data_dir:
            return _json(_no_data())
        try:
            out = _mod("tools.proof").handle(make_runtime(get_setting, data_dir, get_secret, **kw), args)
        except Exception as e:  # never a traceback for the model
            logger.warning("wpbruecke wp_proof failed", exc_info=True)
            out = {"ok": False, "error": "internal", "message": f"{type(e).__name__}: {e}"}
        return out if isinstance(out, dict) and out.get("_multimodal") else _json(out)
    return handler


def _draft_runner(rt, args_key_tool: str, args: dict, session_id: Any, run, tickets) -> Callable[[dict], dict]:
    """A wp_draft call made on behalf of wp_seo / wp_build: approved by the ticket of the outer call."""
    key = tickets.key(args_key_tool, session_id, args)

    def run_draft(draft_args: dict) -> dict:
        d = decide_draft(rt, draft_args, run)
        if d["verdict"] == "block":
            tickets.revoke(key)
            return {"ok": False, "error": d.get("code", "refused"), "message": d["message"], "sent": False}
        approved = tickets.take(key) or d["verdict"] == "allow"
        return _mod("tools.draft").run(rt, draft_args, approved=approved)
    return run_draft


def make_seo_handler(get_setting: SettingGetter, data_dir: Any, run_context, get_secret: SecretGetter = _default_secret,
                     tickets: Any = None, **kw):
    tickets = tickets if tickets is not None else _mod("hooks").TICKETS

    def handler(args: dict, **kwargs: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            runner = _draft_runner(rt, "wp_seo", args, kwargs.get("session_id"), run_context(), tickets)
            return _mod("tools.seo").handle(rt, args, run_draft=runner)
        return _guarded("wp_seo", body)
    return handler


def make_build_handler(get_setting: SettingGetter, data_dir: Any, run_context, get_secret: SecretGetter = _default_secret,
                       tickets: Any = None, **kw):
    tickets = tickets if tickets is not None else _mod("hooks").TICKETS

    def handler(args: dict, **kwargs: Any) -> str:
        args = args if isinstance(args, dict) else {}

        def body():
            if not data_dir:
                return _no_data()
            rt = make_runtime(get_setting, data_dir, get_secret, **kw)
            run = run_context()
            approved_brand = False
            if args.get("action") == "set_brand":
                d = decide_build(rt, args, run)
                key = tickets.key("wp_build", kwargs.get("session_id"), args)
                if d["verdict"] == "block":
                    tickets.revoke(key)
                    return {"ok": False, "error": d.get("code", "refused"), "message": d["message"], "sent": False}
                approved_brand = tickets.take(key) or d["verdict"] == "allow"
            runner = _draft_runner(rt, "wp_build", args, kwargs.get("session_id"), run, tickets)
            return _mod("tools.build").handle(rt, args, run_draft=runner, approved_brand=approved_brand)
        return _guarded("wp_build", body)
    return handler


def decide_seo(rt, args: dict, run) -> dict:
    try:
        draft_args = _mod("tools.seo").fix_args(rt, args)
    except ValueError as e:
        return {"verdict": "block", "code": "bad_args", "message": str(e)}
    return decide_draft(rt, draft_args, run)


def decide_build(rt, args: dict, run) -> dict:
    build = _mod("tools.build")
    if args.get("action") == "build_page":
        try:
            return decide_draft(rt, build.draft_args(rt, args), run)
        except ValueError as e:
            return {"verdict": "block", "code": "bad_args", "message": str(e)}
    try:
        site = build._load(rt, str(args.get("run") or ""))["site"]
    except ValueError as e:
        return {"verdict": "block", "code": "bad_args", "message": str(e)}
    d = decide_draft(rt, {"site": site, "action": "create", "title": "brand"}, run)
    if d["verdict"] == "card":
        colors = ", ".join(f"{c.get('name') or c.get('slug')} {c.get('color')}" for c in args.get("colors") or [])
        d["message"] = (f"This stages brand colours on site {site} for the person's review (nothing changes live until "
                        f"they apply it on the connector's review screen): {colors}; fonts {args.get('fonts') or {}}")
        d["rule_key"] = f"wpbruecke:wp_build:{site}"
    return d


def _register_proof_seo_build(ctx, get_setting: SettingGetter, data_dir: Any, run_context) -> None:
    hooks = _mod("hooks")

    def rt():
        return make_runtime(get_setting, data_dir)
    _SPECS["wp_seo"] = hooks.Spec(lambda a: a.get("action") == "stage_fix", lambda a, run: decide_seo(rt(), a, run))
    _SPECS["wp_build"] = hooks.Spec(lambda a: a.get("action") in ("set_brand", "build_page"),
                                    lambda a, run: decide_build(rt(), a, run))
    proof, seo, build = _mod("tools.proof"), _mod("tools.seo"), _mod("tools.build")
    ctx.register_tool(name="wp_proof", toolset=TOOLSET, schema=proof.SCHEMA,
                      handler=make_proof_handler(get_setting, data_dir), description=proof.SCHEMA["description"],
                      emoji=EMOJI)
    ctx.register_tool(name="wp_seo", toolset=TOOLSET, schema=seo.SCHEMA,
                      handler=make_seo_handler(get_setting, data_dir, run_context), description=seo.SCHEMA["description"],
                      emoji=EMOJI)
    ctx.register_tool(name="wp_build", toolset=TOOLSET, schema=build.SCHEMA,
                      handler=make_build_handler(get_setting, data_dir, run_context),
                      description=build.SCHEMA["description"], emoji=EMOJI)
