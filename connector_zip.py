"""Build the WPBrücke connector zip that WordPress' Plugins → Upload Plugin installer accepts.

The zip holds ``connector/wpbruecke-connector/`` under the top folder ``wpbruecke-connector/`` (the
installer needs one top folder). It is deterministic: sorted names, fixed timestamps (1980-01-01),
fixed permissions (files 0644, folders 0755), so two builds of the same files have the same sha256.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from pathlib import Path

CONNECTOR_VERSION = "0.2.1"
SLUG = "wpbruecke-connector"
CONNECTOR_DIR = Path(__file__).resolve().parent / "connector"
SOURCE = CONNECTOR_DIR / SLUG
MAIN_FILE = SOURCE / f"{SLUG}.php"
ZIP_NAME = f"{SLUG}.zip"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SKIP_NAMES = {".DS_Store", "Thumbs.db", "__pycache__"}


def header_version(main_file: Path = MAIN_FILE) -> str | None:
    """The ``Version:`` line of the plugin header."""
    match = re.search(r"^[ \t/*#@]*Version:\s*(\S+)", main_file.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def source_files(source: Path = SOURCE) -> list[Path]:
    """Every file of the plugin folder, sorted, without hidden files, caches or zips."""
    out = []
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in SKIP_NAMES or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink() and path.suffix != ".zip":
            out.append(path)
    return sorted(out, key=lambda p: p.relative_to(source).as_posix())


def _target(out: Path) -> Path:
    out = Path(out)
    if out.suffix.lower() != ".zip":
        out = out / ZIP_NAME
    return out.resolve()


def _info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.create_system = 3  # unix, so external_attr carries the mode
    info.external_attr = mode << 16
    if name.endswith("/"):
        info.external_attr |= 0x10  # MS-DOS directory flag
        info.compress_type = zipfile.ZIP_STORED
    else:
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_zip(out: Path) -> dict:
    """Write the connector zip to ``out`` (a ``.zip`` path or a folder) and describe it.

    Returns ``{"path", "bytes", "sha256", "files"}`` (``files``: names inside the zip, files only).
    Refuses to write inside the connector folder, where the zip would end up in the next build.
    """
    target = _target(out)
    connector = CONNECTOR_DIR.resolve()
    if target == connector or connector in target.parents:
        raise ValueError(f"refusing to write the zip inside the connector folder: {target}")
    version = header_version()
    if version != CONNECTOR_VERSION:
        raise ValueError(f"plugin header Version {version!r} does not match CONNECTOR_VERSION {CONNECTOR_VERSION!r}")
    files = source_files()
    if MAIN_FILE.resolve() not in {f.resolve() for f in files}:
        raise ValueError("the main plugin file is missing")

    dirs: set[str] = {f"{SLUG}/"}
    for path in files:
        parts = path.relative_to(SOURCE).parts[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add(f"{SLUG}/" + "/".join(parts[:i]) + "/")
    entries: list[tuple[str, Path | None]] = [(d, None) for d in dirs]
    entries += [(f"{SLUG}/{p.relative_to(SOURCE).as_posix()}", p) for p in files]
    entries.sort(key=lambda e: e[0])

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wpbruecke-connector-", suffix=".zip", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh, zipfile.ZipFile(fh, "w") as zf:
            for name, path in entries:
                if path is None:
                    zf.writestr(_info(name, 0o40755), b"")
                else:
                    zf.writestr(_info(name, 0o100644), path.read_bytes(), compresslevel=9)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    data = target.read_bytes()
    return {
        "path": str(target),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "files": [name for name, path in entries if path is not None],
    }


if __name__ == "__main__":  # python connector_zip.py <out.zip or folder>
    import json
    import sys

    print(json.dumps(build_zip(Path(sys.argv[1] if len(sys.argv) > 1 else ".")), indent=2))
