"""Comparaison de deux versions d'un même élément (sans écriture, sans interprétation médicale)."""

from __future__ import annotations

from typing import Any


def _index(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(item[key]): item for item in items}


def keyed_diff(old: list[dict[str, Any]], new: list[dict[str, Any]], key: str) -> dict[str, Any]:
    a, b = _index(old, key), _index(new, key)
    changed = []
    for k in sorted(a.keys() & b.keys()):
        fields = sorted(f for f in (a[k].keys() | b[k].keys()) if a[k].get(f) != b[k].get(f))
        if fields:
            changed.append({key: k, "fields": fields})
    return {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()), "changed": changed}


def lesson_version_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_blocks = list((old.get("body") or {}).get("blocks") or [])
    new_blocks = list((new.get("body") or {}).get("blocks") or [])
    old_order = [str(b.get("id")) for b in old_blocks]
    new_order = [str(b.get("id")) for b in new_blocks]
    common = [i for i in new_order if i in old_order]
    return {
        "title_changed": old.get("title") != new.get("title"),
        "change_summary_changed": old.get("change_summary") != new.get("change_summary"),
        "blocks": keyed_diff(old_blocks, new_blocks, "id"),
        "blocks_reordered": common != [i for i in old_order if i in new_order],
        "sources": keyed_diff(old.get("sources") or [], new.get("sources") or [], "reference_id"),
    }


def module_version_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_summary_changed": old.get("change_summary") != new.get("change_summary"),
        "lessons": keyed_diff(old.get("lessons") or [], new.get("lessons") or [], "lesson_id"),
    }


def release_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    def edges(x: dict[str, Any]) -> set[str]:
        return {f"{p['module_id']}>{p['requires']}" for p in x.get("prerequisites") or []}

    return {
        "change_summary_changed": old.get("change_summary") != new.get("change_summary"),
        "modules": keyed_diff(old.get("modules") or [], new.get("modules") or [], "module_id"),
        "prerequisites": {"added": sorted(edges(new) - edges(old)), "removed": sorted(edges(old) - edges(new))},
    }
