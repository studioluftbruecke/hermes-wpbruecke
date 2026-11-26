"""Backend routes for the WPBrücke page, mounted at `/api/plugins/wpbruecke/`.

The handlers are `api.py`'s functions; this file only adapts them to FastAPI. Hermes loads it as a
standalone module in the profile's plugin host child (no `ctx`), so the plugin folder is loaded here
as a package by path. Settings come from the profile config (`plugins.entries.wpbruecke.settings`),
secrets from Hermes' secret scope (read fresh per request in a plugin host child, see `_get_secret`),
and the data folder is the agent half's `ctx.state.data_dir`
(`plugin-data/agent-plugin-wpbruecke-<digest>`), resolved per call so it follows the profile.
Handlers are sync: FastAPI runs them on a worker thread, so a slow site never blocks the loop.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

PLUGIN = "wpbruecke"
_PACKAGE = "hermes_wpbruecke_dashboard"
_HOST_PROCESS_ENV = "HERMES_PLUGIN_HOST_PROCESS"  # set by Hermes in a plugin host process
_PREFIX = PLUGIN.upper() + "_"

router = APIRouter()


def _plugin():
    module = sys.modules.get(_PACKAGE)
    if module is not None:
        return module
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(_PACKAGE, root / "__init__.py", submodule_search_locations=[str(root)])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the {PLUGIN} package")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def _sub(name: str):
    _plugin()
    return importlib.import_module(f"{_PACKAGE}.{name}")


def _get_setting(key: str, default: Any = None) -> Any:
    from hermes_cli.config import load_config_readonly

    entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    return ((entries.get(PLUGIN) or {}).get("settings") or {}).get(key, default)


_SCOPE_TTL_S = 2.0  # one build serves a page load's burst of reads; a saved password shows within it
_SCOPE_CACHE: dict = {}


def _own_secrets(home: Path) -> Optional[dict]:
    """The plugin's own keys from Hermes' scope builder, built again after _SCOPE_TTL_S. Only
    `WPBRUECKE_*` keys are kept between requests."""
    now = time.monotonic()
    cached = _SCOPE_CACHE.get(str(home))
    if cached and now - cached["at"] < _SCOPE_TTL_S:
        return cached["values"]
    try:
        from agent.secret_scope import build_profile_secret_scope

        built = build_profile_secret_scope(home)
    except Exception:
        return None
    values = {k: v for k, v in (built or {}).items() if k.startswith(_PREFIX)}
    _SCOPE_CACHE[str(home)] = {"at": now, "values": values}
    return values


def _get_secret(name: str) -> Optional[str]:
    """The profile's current value. These routes run in the profile's plugin host child
    (`HERMES_PLUGIN_HOST_PROCESS=1`), whose environment holds the secrets as they were when it
    started, so a password saved in the settings form afterwards would stay invisible until Hermes
    restarts. There the plugin's own keys come from Hermes' scope builder (the one each gateway turn
    uses), and its answer is final: a key missing there is missing, never brought back from the
    launch environment. Everywhere else the request already runs in a fresh profile scope."""
    from agent.secret_scope import get_secret

    if os.environ.get(_HOST_PROCESS_ENV) == "1" and name.startswith(_PREFIX):
        try:
            current = _own_secrets(_home())
        except Exception:
            current = None
        if current is not None:
            return current.get(name) or None
    return get_secret(name)


def _profile() -> str:
    return _sub("profile").current_name(_home())


def _home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def data_namespace(key: str = PLUGIN) -> str:
    """The folder name Hermes gives a plugin's `ctx.state.data_dir` (`plugin-data/agent-plugin-<slug>-<digest>`),
    restated because Hermes has no public call for it; a test holds it equal to Hermes' own."""
    slug = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_-") else "-" for ch in key.lower()).strip("-_")
    return f"agent-plugin-{slug or 'plugin'}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}"


def _data_dir() -> Path:
    path = _home() / "plugin-data" / data_namespace()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rt():
    return _sub("runtime").Runtime(_get_setting, _get_secret, _data_dir())


def _reply(result: "tuple[int, dict]") -> JSONResponse:
    status, body = result
    return JSONResponse(body, status_code=status)


def _api():
    return _sub("api")


@router.get("/overview")
def overview() -> JSONResponse:
    return _reply(_api().overview(_rt()))


@router.get("/groups")
def groups() -> JSONResponse:
    return _reply(_api().groups_view(_rt(), _profile()))


@router.post("/sites/check")
def sites_check(body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().sites_check(_rt(), body, _profile()))


@router.post("/sites/{site_id}/confirm")
def site_confirm(site_id: str, body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().site_confirm(_rt(), site_id, body, _profile()))


@router.post("/sites/{site_id}/revoke")
def site_revoke(site_id: str) -> JSONResponse:
    return _reply(_api().site_revoke(_rt(), site_id))


@router.delete("/sites/{site_id}")
def site_delete(site_id: str) -> JSONResponse:
    return _reply(_api().site_delete(_rt(), site_id))


@router.post("/public-sites")
def public_site_create(body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().public_site_create(_rt(), body))


@router.get("/sites/{site_id}")
def site_detail(site_id: str) -> JSONResponse:
    return _reply(_api().site_detail(_rt(), site_id))


@router.post("/sites")
def site_create(body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().site_create(_rt(), body))


@router.patch("/sites/{site_id}")
def site_patch(site_id: str, body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().site_patch(_rt(), site_id, body))


@router.get("/sites/{site_id}/history")
def site_history(site_id: str) -> JSONResponse:
    return _reply(_api().site_history(_rt(), site_id))


@router.get("/sites/{site_id}/activity")
def site_activity(site_id: str) -> JSONResponse:
    return _reply(_api().site_activity(_rt(), site_id))


@router.get("/desk")
def desk(site: str = Query(""), kind: str = Query("")) -> JSONResponse:
    return _reply(_api().desk_list(_rt(), site, kind))


@router.get("/desk/{item_id}")
def desk_item(item_id: str) -> JSONResponse:
    return _reply(_api().desk_item(_rt(), item_id))


@router.post("/desk/{item_id}/review")
def desk_review(item_id: str) -> JSONResponse:
    return _reply(_api().desk_review(_rt(), item_id))


@router.post("/desk/{item_id}/changes")
def desk_changes(item_id: str, body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().desk_changes(_rt(), item_id, body))


@router.post("/desk/{item_id}/publish")
def desk_publish(item_id: str) -> JSONResponse:
    return _reply(_api().desk_act(_rt(), item_id, "publish"))


@router.post("/desk/{item_id}/schedule")
def desk_schedule(item_id: str, body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().desk_act(_rt(), item_id, "schedule", body))


@router.post("/desk/{item_id}/apply")
def desk_apply(item_id: str) -> JSONResponse:
    return _reply(_api().desk_act(_rt(), item_id, "apply"))


@router.get("/desk/{item_id}/review-link")
def review_link(item_id: str, action: str = Query("publish")) -> JSONResponse:
    return _reply(_api().review_link(_rt(), item_id, action))


@router.post("/jobs/proof")
def job_proof(body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().job_proof(_rt(), body, _sub("tools.proof").run_for_route))


@router.post("/jobs/seo-site")
def job_seo_site(body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().job_seo_site(_rt(), body, _sub("tools.seo").run_site_for_route))


@router.get("/jobs/{job_id}")
def job_get(job_id: str) -> JSONResponse:
    return _reply(_api().job_get(_rt(), job_id))


@router.get("/seo")
def seo(site: str = Query(""), post: str = Query("")) -> JSONResponse:
    return _reply(_api().seo_reports(_rt(), site, post))


@router.get("/build")
def build_list(site: str = Query("")) -> JSONResponse:
    return _reply(_api().build_list(_rt(), site))


@router.get("/build/{run_id}")
def build_get(run_id: str) -> JSONResponse:
    return _reply(_api().build_get(_rt(), run_id))


@router.post("/build/{run_id}/gate")
def build_gate(run_id: str, body: dict = Body(default={})) -> JSONResponse:
    return _reply(_api().build_gate(_rt(), run_id, body))


@router.get("/doctor")
def doctor(site: str = Query("")) -> JSONResponse:
    return _reply(_api().doctor(_rt(), site))


def _workspace() -> str:
    """``ssh`` when the profile's terminal backend is SSH: Desktop's file reads then go to that machine
    (Hermes' files router picks its reader by the same setting)."""
    from hermes_cli.config import load_config_readonly

    terminal = (load_config_readonly() or {}).get("terminal") or {}
    return "ssh" if str(terminal.get("backend") or "local").strip().lower() == "ssh" else "host"


@router.post("/connector-package")
def connector_package() -> JSONResponse:
    return _reply(_api().connector_package(_data_dir(), _workspace()))
