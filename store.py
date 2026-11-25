"""The one writer of WPBrücke's plugin data (PLAN constraint 16).

Three processes write the same files: the gateway (agent tools), the profile's plugin host child
(dashboard routes) and the cron tick. Every write here is a temp file plus ``os.replace`` (a reader
never sees half a file), every read-modify-write holds an ``fcntl`` lock per store, the Chromium slot
is a lock file (so "one browser at a time" holds across processes), and jobs live on disk so
``GET /jobs/{id}`` survives a reload of the plugin host child.

Layout under the data dir (``ctx.state.data_dir`` in the agent half, the same folder in routes)::

    sites/<id>.yaml, sites/<id>.history/        site files and their versions (sites.py)
    facts/<id>.json                             SiteFacts cache (facts.py)
    desk/<item>.json                            desk items (desk.py)
    proofs/<run>/run.json, proofs/<run>/*.png   proof runs (proof/)
    seo/<report>.json                           SEO reports (seo/)
    jobs/<id>.json                              long work started from a route
    build/<run>.json                            GeneratePress pack runs
    groups/<n>.json, groups/<n>.check.json      pairing record and last connection check per group
    migration/<site>.json, migration/done.json  what a 0.1.0 site file said, and the migration marker
    locks/<store>.lock                          one lock file per store
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Optional

STORES = ("sites", "facts", "desk", "proofs", "seo", "jobs", "build", "tick", "groups", "migration")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class StoreError(Exception):
    pass


class Busy(StoreError):
    """The Chromium slot (or another exclusive lock) is held by another process."""


def check_id(value: Any, what: str = "id") -> str:
    """Ids become file names: a slug only, so `../` can never leave the data dir."""
    text = str(value or "").strip()
    if not _ID_RE.match(text):
        raise StoreError(f"{what} must be a lowercase slug (a-z, 0-9, -), got {text[:40]!r}")
    return text


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    def __init__(self, data_dir: Any) -> None:
        if not data_dir:
            raise StoreError("WPBrücke has no data folder in this context")
        self.root = Path(data_dir)

    # --- paths -------------------------------------------------------------------------------
    def dir(self, store: str) -> Path:
        if store not in STORES:
            raise StoreError(f"unknown store {store}")
        path = self.root / store
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, store: str, name: str) -> Path:
        base = self.dir(store)
        target = (base / name).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            raise StoreError(f"path escapes the {store} store")
        return target

    # --- locks -------------------------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self, store: str, blocking: bool = True, timeout_s: float = 30.0) -> Iterator[None]:
        locks = self.root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        fd = os.open(locks / f"{store}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if blocking:
                deadline = time.monotonic() + timeout_s
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as e:
                        if e.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                            raise Busy(f"the {store} store is locked by another process") from e
                        time.sleep(0.05)
            else:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as e:
                    raise Busy(f"{store} is busy in another process") from e
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def chromium_slot(self):
        """One browser at a time across every process; raises Busy at once when held."""
        return self.lock("chromium", blocking=False)

    # --- atomic io ---------------------------------------------------------------------------
    @staticmethod
    def write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def write_json(self, path: Path, data: Any) -> None:
        self.write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def read_json(path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except ValueError:
            return default

    # --- jobs (long work started from a route) -----------------------------------------------
    def job_create(self, kind: str, params: dict) -> dict:
        job = {"id": new_id("job"), "kind": kind, "params": params, "state": "queued",
               "created_at": now_iso(), "result": None, "error": None}
        self.write_json(self.path("jobs", f"{job['id']}.json"), job)
        return job

    def job_update(self, job_id: str, **changes: Any) -> dict:
        check_id(job_id, "job id")
        with self.lock("jobs"):
            path = self.path("jobs", f"{job_id}.json")
            job = self.read_json(path, None)
            if job is None:
                raise StoreError(f"no job {job_id}")
            job.update(changes)
            job["updated_at"] = now_iso()
            self.write_json(path, job)
            return job

    def job_get(self, job_id: str) -> Optional[dict]:
        check_id(job_id, "job id")
        return self.read_json(self.path("jobs", f"{job_id}.json"), None)
