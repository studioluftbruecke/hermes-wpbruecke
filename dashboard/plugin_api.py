"""Backend routes for the WPBrücke page, mounted at `/api/plugins/wpbruecke/`.

The handlers are `api.py`'s functions; this file only adapts them to FastAPI. Hermes loads it as a
standalone module in the profile's plugin host child (no `ctx`), so the plugin folder is loaded here
as a package by path. Settings come from the profile config (`plugins.entries.wpbruecke.settings`),
secrets from Hermes' secret scope, and the data folder is the agent half's `ctx.state.data_dir`
(`plugin-data/agent-plugin-wpbruecke-<digest>`), resolved per call so it follows the profile.
Handlers are sync: FastAPI runs them on a worker thread, so a slow site never blocks the loop.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

PLUGIN = "wpbruecke"
_PACKAGE = "hermes_wpbruecke_dashboard"

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


def _get_secret(name: str) -> Optional[str]:
    from agent.secret_scope import get_secret

    return get_secret(name)


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
