"""Site files as readable YAML without a YAML dependency in the plugin's tests.

``dump`` writes block-style YAML for the shapes a site file has: nested mappings, lists of scalars
(flow style), lists of mappings (block style), and JSON-quoted strings (always valid YAML). ``load``
uses Hermes' own YAML parser when it is importable (every real run) and otherwise a small parser for
exactly what ``dump`` writes (the plugin's tests run without Hermes). A file a person edits by hand
is therefore read by a full YAML parser in Hermes.
"""
from __future__ import annotations

import json
import re
from typing import Any

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value), ensure_ascii=False)


def _key(key: Any) -> str:
    k = str(key)
    return k if _PLAIN_KEY.match(k) and k not in ("true", "false", "null", "yes", "no", "on", "off") else json.dumps(k)


def _emit(value: Any, indent: int, out: list) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, dict):
                if v:
                    out.append(f"{pad}{_key(k)}:")
                    _emit(v, indent + 1, out)
                else:
                    out.append(f"{pad}{_key(k)}: {{}}")
            elif isinstance(v, list) and any(isinstance(i, dict) for i in v):
                out.append(f"{pad}{_key(k)}:")
                for item in v:
                    if not isinstance(item, dict):
                        raise ValueError("a list may hold mappings or scalars, not both")
                    lines: list = []
                    _emit(item, indent + 2, lines)
                    if not lines:
                        out.append(f"{pad}  - {{}}")
                        continue
                    first = lines[0].lstrip()
                    out.append(f"{pad}  - {first}")
                    out.extend(lines[1:])
            elif isinstance(v, list):
                out.append(f"{pad}{_key(k)}: [" + ", ".join(_scalar(i) for i in v) + "]")
            else:
                out.append(f"{pad}{_key(k)}: {_scalar(v)}")
    else:
        raise ValueError("the top level of a site file is a mapping")


def dump(data: dict, header: str = "") -> str:
    out: list = [f"# {line}" if line else "#" for line in header.splitlines()] if header else []
    _emit(data, 0, out)
    return "\n".join(out) + "\n"


# --- loading ------------------------------------------------------------------------------------

def load(text: str) -> dict:
    try:
        import hermes_yaml as yaml  # Hermes' own parser (every real run)
    except ImportError:
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None
    if yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = _subset_load(text)
    if not isinstance(data, dict):
        raise ValueError("a site file must be a mapping")
    return data


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw in ("", "null", "~"):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "{}":
        return {}
    if raw.startswith("[") and raw.endswith("]"):
        return json.loads(raw)
    if raw.startswith('"'):
        return json.loads(raw)
    try:
        return json.loads(raw)
    except ValueError:
        return raw.split(" #", 1)[0].strip()


def _split_key(line: str) -> "tuple[str, str]":
    if line.startswith('"'):
        end = line.index('":') + 1
        return json.loads(line[:end]), line[end + 1:]
    key, _, rest = line.partition(":")
    return key.strip(), rest


def _subset_load(text: str) -> dict:
    lines = [(len(l) - len(l.lstrip(" ")), l.strip()) for l in text.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    pos = 0

    def block(indent: int) -> Any:
        nonlocal pos
        if pos < len(lines) and lines[pos][1].startswith("- "):
            items = []
            while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
                first = lines[pos][1][2:]
                lines[pos] = (indent + 2, first)
                if first == "{}":
                    pos += 1
                    items.append({})
                    continue
                items.append(mapping(indent + 2))
            return items
        return mapping(indent)

    def mapping(indent: int) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(lines) and lines[pos][0] == indent and not lines[pos][1].startswith("- "):
            key, rest = _split_key(lines[pos][1])
            pos += 1
            if rest.strip() == "":
                if pos < len(lines) and lines[pos][0] > indent:
                    out[key] = block(lines[pos][0])
                else:
                    out[key] = None
            else:
                out[key] = _parse_scalar(rest)
        return out

    return mapping(0) if lines else {}
