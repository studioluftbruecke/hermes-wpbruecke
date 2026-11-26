"""The Elementor element tree: lookup, what each element lets an edit change, edits, and the hash that
proves a write landed as meant.

Shapes (plan § Data shapes):

    ElementorDoc  = {post_id, version, edit_mode, elements: [Element], page_settings, hash}
    Element       = {id, elType, widgetType|None, settings, elements: [Element]}   (Elementor's stored JSON)
    ElementorEdit = {"element_id": str, "set": {path: value}}

A path is dotted: ``title``, ``link.url``, ``image.id``, ``icon_list.<item _id>.text``. ``set`` may also
nest (``{"link": {"url": ...}}``); ``normalize_edits`` flattens it. A list value would replace a
repeater, adding or removing items, so it is refused.

``content_hash`` covers the tree's shape (id, type and nesting of every element), the value at every
editable path of the six widget types, and every element's dynamic tags (``__dynamic__``, repeater items
too). Elementor normalises other settings on save (icons, defaults), so the hash leaves those out: what it
proves is that no element came or went, no dynamic tag was dropped, and that the texts, links and images
are exactly what the save reported.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from . import widgets as W


class PackRefused(Exception):
    """A request the pack refuses; ``code`` and a one-sentence ``message`` (the refusal shape)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class ElementorDoc:
    post_id: int
    version: str
    elements: list
    status: str = ""
    type: str = "page"
    title: str = ""
    source: int = 0
    staged: bool = False
    template: str = ""
    page_settings: dict = field(default_factory=dict)
    unknown_types: tuple = ()
    unknown_dynamic: tuple = ()
    unfiltered_html: bool = True
    kses_would_strip: bool = False
    modified_gmt: str = ""
    edit_mode: str = "builder"

    @property
    def hash(self) -> str:
        return content_hash(self.elements)

    @classmethod
    def from_route(cls, data: dict) -> "ElementorDoc":
        elements = data.get("elements")
        return cls(post_id=int(data.get("post_id") or 0), version=str(data.get("version") or ""),
                   elements=elements if isinstance(elements, list) else [], status=str(data.get("status") or ""),
                   type=str(data.get("type") or "page"), title=str(data.get("title") or ""),
                   source=int(data.get("source") or 0), staged=bool(data.get("staged")),
                   template=str(data.get("template") or ""),
                   page_settings=data.get("page_settings") if isinstance(data.get("page_settings"), dict) else {},
                   unknown_types=tuple(str(t) for t in data.get("unknown_types") or []),
                   unknown_dynamic=tuple(str(t) for t in data.get("unknown_dynamic") or []),
                   unfiltered_html=bool(data.get("unfiltered_html", True)),
                   kses_would_strip=bool(data.get("kses_would_strip")),
                   modified_gmt=str(data.get("modified_gmt") or ""), edit_mode=str(data.get("edit_mode") or "builder"))

    def shape(self) -> dict:
        """The plan's ElementorDoc dict."""
        return {"post_id": self.post_id, "version": self.version, "edit_mode": self.edit_mode,
                "elements": self.elements, "page_settings": self.page_settings, "hash": self.hash}


# --- the tree ---------------------------------------------------------------------------------------------

def walk(elements: Any) -> Iterator[dict]:
    for el in elements if isinstance(elements, list) else []:
        if isinstance(el, dict):
            yield el
            yield from walk(el.get("elements"))


def find(elements: Any, element_id: str) -> Optional[dict]:
    return next((el for el in walk(elements) if str(el.get("id")) == str(element_id)), None)


def skeleton(elements: Any) -> list:
    return [[str(el.get("id") or ""), str(el.get("elType") or ""), str(el.get("widgetType") or ""),
             skeleton(el.get("elements"))] for el in (elements if isinstance(elements, list) else [])
            if isinstance(el, dict)]


def _settings(el: dict) -> dict:
    s = el.get("settings")
    return s if isinstance(s, dict) else {}


def _dynamic(container: dict) -> dict:
    d = container.get("__dynamic__")
    return d if isinstance(d, dict) else {}


def get_path(settings: dict, path: str) -> Any:
    """The value at a dotted path; a repeater segment picks the item by ``_id``."""
    cur: Any = settings
    for seg in path.split("."):
        if isinstance(cur, list):
            cur = next((i for i in cur if isinstance(i, dict) and str(i.get("_id")) == seg), None)
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
        if cur is None:
            return None
    return cur


def _expand(el: dict) -> "list[tuple[str, str]]":
    """Concrete (path, pattern) pairs of an element of the six types: repeater patterns once per item."""
    widget = str(el.get("widgetType") or "")
    out = []
    for pattern in W.EDITABLE.get(widget, ()):
        if "*" in pattern:
            head, tail = pattern.split(".*.", 1)
            for item in _settings(el).get(head) or []:
                if isinstance(item, dict) and item.get("_id"):
                    out.append((f"{head}.{item['_id']}.{tail}", pattern))
        else:
            out.append((pattern, pattern))
    return out


def _dynamic_for(el: dict, path: str) -> bool:
    settings = _settings(el)
    parts = path.split(".")
    if parts[0] in _dynamic(settings):
        return True
    if len(parts) > 2 and isinstance(settings.get(parts[0]), list):
        item = next((i for i in settings[parts[0]] if isinstance(i, dict) and str(i.get("_id")) == parts[1]), None)
        return bool(item) and parts[2] in _dynamic(item)
    return False


def read_only_reason(el: dict) -> Optional[str]:
    """Why an element takes no edit at all, or None for a widget of the six types."""
    if W.is_atomic(el):
        return "a V4 atomic element: read-only in this version"
    if el.get("elType") != "widget":
        return f"a layout element ({el.get('elType') or 'unknown'}): elements are never added, removed or moved"
    widget = str(el.get("widgetType") or "")
    if widget not in W.EDITABLE:
        return f"the {widget or 'unnamed'} widget is outside the pack's six widget types"
    return None


def editable_paths(el: dict) -> list:
    """Setting paths an edit may change on this element (empty: read-only). Paths fed by a dynamic tag are
    left out: the tag would hide the edit."""
    if read_only_reason(el):
        return []
    return [path for path, _ in _expand(el) if not _dynamic_for(el, path)]


def dynamic_paths(el: dict) -> list:
    if read_only_reason(el):
        return []
    return [path for path, _ in _expand(el) if _dynamic_for(el, path)]


def _canon(value: Any) -> Any:
    if value is None or (isinstance(value, (dict, list, str)) and not value):
        return ""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _dynamic_sets(el: dict) -> dict:
    """An element's dynamic tags: ``""`` -> ``settings.__dynamic__``, ``"<repeater>.<item _id>."`` -> that
    item's ``__dynamic__``; empty ones left out (the connector's wpbruecke_elementor_dynamic_raw)."""
    settings = _settings(el)
    out = {}
    if settings.get("__dynamic__"):
        out[""] = settings["__dynamic__"]
    for key, value in settings.items():
        if key == "__dynamic__" or not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("_id") is not None and item.get("__dynamic__"):
                out[f"{key}.{item['_id']}."] = item["__dynamic__"]
    return out


def dynamic_tags(elements: Any) -> dict:
    """Element id -> its dynamic tags, for every element that has any."""
    return {str(el.get("id")): sets for el in walk(elements) for sets in [_dynamic_sets(el)] if sets}


def content_hash(elements: Any) -> str:
    """sha256 over the shape, every editable-type value and every element's dynamic tags (see the module
    text)."""
    values = []
    for el in walk(elements):
        if read_only_reason(el):
            continue
        values.append([str(el.get("id")), {path: _canon(get_path(_settings(el), path)) for path, _ in _expand(el)}])
    blob = json.dumps([skeleton(elements), values, dynamic_tags(elements)], sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- edits ------------------------------------------------------------------------------------------------

def _flatten(prefix: str, value: Any, out: dict) -> None:
    if isinstance(value, dict):
        if not value:
            raise PackRefused("bad_args", f"elementor_edits: {prefix or 'set'} is empty")
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, list):
        raise PackRefused("elementor_structure", f"elementor_edits: {prefix} is a list; a list would add or remove "
                          "items, so name each existing item by its _id (icon_list.<_id>.text). " + W.SCOPE)
    else:
        out[prefix] = value


def normalize_edits(raw: Any) -> list:
    """ElementorEdit list -> [{"element_id": str, "set": {path: value}}], one entry per element, nothing
    looked up yet. Refuses shapes that are not edits of existing settings."""
    if not isinstance(raw, list) or not raw:
        raise PackRefused("bad_args", "elementor_edits is a list of {element_id, set: {path: value}}")
    out, seen = [], set()
    for e in raw:
        if not isinstance(e, dict) or not str(e.get("element_id") or "").strip() or not isinstance(e.get("set"), dict):
            raise PackRefused("bad_args", "each elementor_edits entry is {element_id, set: {path: value}}")
        extra = sorted(set(e) - {"element_id", "set"})
        if extra:
            raise PackRefused("elementor_structure", f"elementor_edits entries take element_id and set only, not "
                              f"{', '.join(extra)}. " + W.SCOPE)
        eid = str(e["element_id"]).strip()
        if eid in seen:
            raise PackRefused("bad_args", f"element {eid} is named twice; put its paths in one edit")
        seen.add(eid)
        flat: dict = {}
        _flatten("", e["set"], flat)
        out.append({"element_id": eid, "set": flat})
    return out


def _check_value(pattern: str, path: str, value: Any) -> None:
    lf = W.leaf(pattern)
    if lf in W.FLAGS:
        if value not in (True, False, "on", "", 0, 1, "0", "1"):
            raise PackRefused("bad_args", f"{path} is true or false")
    elif lf == "id":
        if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, str) and value.isdigit())):
            raise PackRefused("bad_args", f"{path} is a media library id (a number; 0 clears it)")
    elif not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise PackRefused("bad_args", f"{path} takes text")
    elif len(str(value).encode("utf-8")) > W.MAX_VALUE_BYTES:
        raise PackRefused("bad_value", f"{path} is {len(str(value).encode('utf-8'))} bytes; one edit value holds "
                          "at most 64 KB. Nothing was sent.")
    elif lf == "url" and not W.url_ok(str(value)):
        raise PackRefused("bad_args", f"{path}: a link or image URL is http(s), mailto:, tel: or relative")


def unknown_dynamic_reason(doc: ElementorDoc) -> str:
    return (f"Elementor on this site cannot resolve the dynamic tags of {', '.join(doc.unknown_dynamic)} (their "
            "plugin, e.g. Elementor Pro, is not active), and its save would delete them: a page with dynamic tags "
            "from a plugin that is not active cannot be edited here; the person edits it in Elementor with that "
            "plugin active")


def check_edits(doc: ElementorDoc, edits: list) -> None:
    """Refuse, before any write, every edit outside the pack's scope on this tree."""
    if doc.kses_would_strip:
        raise PackRefused("kses_would_strip", "WordPress would strip HTML from this page for this site's user (it "
                          "lacks unfiltered_html), so a save would change parts the edit does not touch; a user "
                          "with unfiltered_html makes this edit. Nothing was sent.")
    if doc.unknown_dynamic:
        raise PackRefused("elementor_unknown_dynamic", unknown_dynamic_reason(doc) + ". Nothing was sent.")
    for e in edits:
        eid = e["element_id"]
        el = find(doc.elements, eid)
        if el is None:
            raise PackRefused("elementor_structure", f"post {doc.post_id} has no element {eid}; edits change "
                              f"existing elements only (wp_content get lists them). {W.SCOPE}")
        why = read_only_reason(el)
        if why:
            raise PackRefused("elementor_read_only", f"element {eid} is read-only: {why}. {W.SCOPE}")
        allowed = editable_paths(el)
        dynamic = dynamic_paths(el)
        widget = str(el.get("widgetType"))
        for path, value in e["set"].items():
            if path in dynamic:
                raise PackRefused("elementor_dynamic", f"{path} of element {eid} comes from a dynamic tag "
                                  "(settings.__dynamic__), which would hide the edit; change it in Elementor. "
                                  "Nothing was sent.")
            pattern = W.pattern_for(widget, path)
            if pattern is None or path not in allowed:
                head = path.split(".")[0]
                if head == W.REPEATERS.get(widget) and pattern is not None:
                    raise PackRefused("elementor_structure", f"{widget} {eid} has no item {path.split('.')[1]}; "
                                      f"items are never added. {W.SCOPE}")
                raise PackRefused("elementor_read_only", f"{path} of {widget} {eid} is not editable; editable: "
                                  f"{', '.join(allowed) or 'nothing'}. {W.SCOPE}")
            _check_value(pattern, path, value)
        for media in W.MEDIA_KEYS:
            if f"{media}.alt" in e["set"]:
                new_id = e["set"].get(f"{media}.id", None if f"{media}.url" in e["set"] else
                                      (get_path(_settings(el), f"{media}.id") or 0))
                if new_id not in (None, 0, "0", ""):
                    raise PackRefused("elementor_alt", f"{media}.alt of {eid}: a media library image shows the "
                                      "alt text of its media item; set it there, or give an outside image by "
                                      f"{media}.url with its alt. Nothing was sent.")


def apply_stored(elements: list, stored: dict) -> list:
    """A copy of the tree with the values the save reported (element id -> path -> value) set."""
    tree = copy.deepcopy(elements)
    for eid, paths in (stored or {}).items():
        el = find(tree, eid)
        if el is None:
            continue
        if not isinstance(el.get("settings"), dict):
            el["settings"] = {}
        for path, value in (paths or {}).items():
            _set_path(el["settings"], str(path).split("."), value)
    return tree


def _set_path(cur: dict, parts: list, value: Any) -> None:
    for i, seg in enumerate(parts[:-1]):
        nxt = cur.get(seg)
        if isinstance(nxt, list):
            item = next((it for it in nxt if isinstance(it, dict) and str(it.get("_id")) == parts[i + 1]), None)
            if item is None:
                return
            _set_path(item, parts[i + 2:], value)
            return
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[parts[-1]] = value


def readback_problem(before: list, stored: dict, after: list) -> Optional[str]:
    """None when the saved tree is the old one with exactly the reported values set; else what differs."""
    if skeleton(before) != skeleton(after):
        return "the saved tree has other elements than before (an element was added, removed or moved)"
    was, now = dynamic_tags(before), dynamic_tags(after)
    lost = sorted(eid for eid in set(was) | set(now) if was.get(eid) != now.get(eid))
    if lost:
        return f"the dynamic tags of {', '.join(lost)} changed although the edit did not touch them"
    if content_hash(apply_stored(before, stored)) != content_hash(after):
        return "the saved texts, links or images differ from what the save reported"
    return None
