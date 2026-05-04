"""
Reference resolution – build object registry and validate cross-record references.
"""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from maxi.core.errors import MaxiError, MaxiErrorCode

if TYPE_CHECKING:
    from maxi.core.types import MaxiParseResult, MaxiSchema

_NON_REF_TYPES = frozenset({"int", "decimal", "float", "str", "bool", "bytes"})


def get_referenced_type_alias(type_expr: str | None, schema: MaxiSchema) -> str | None:
    """Return the alias of the referenced object type, or ``None`` if primitive/enum/map."""
    if not type_expr:
        return None
    t = re.sub(r"(\[\])+$", "", type_expr.strip())
    if t in _NON_REF_TYPES:
        return None
    if t == "map" or t.startswith("map<"):
        return None
    if t.startswith("enum"):
        return None

    resolver = getattr(schema, "resolve_type_alias", None)
    if resolver:
        alias = resolver(t)
        if alias:
            return alias
    return t if schema.has_type(t) else None


def build_object_registry(result: MaxiParseResult) -> dict[str, dict[str, dict[str, Any]]]:
    """Build ``{alias: {id_str: field_dict}}`` from all parsed records and inline objects."""
    registry: dict[str, dict[str, dict[str, Any]]] = {}

    for record in result.records:
        td = result.schema.get_type(record.alias)
        if td is None:
            continue
        id_field = td.get_id_field()
        if id_field is None:
            continue
        id_idx = td.get_id_field_index()
        if id_idx < 0 or id_idx >= len(record.values):
            continue
        id_val = record.values[id_idx]
        if id_val is None:
            continue

        alias_map = registry.get(record.alias)
        if alias_map is None:
            alias_map = {}
            registry[record.alias] = alias_map

        obj: dict[str, Any] = {}
        for i, field in enumerate(td.fields):
            obj[field.name] = record.values[i] if i < len(record.values) else None

        alias_map[str(id_val)] = obj

    for record in result.records:
        td = result.schema.get_type(record.alias)
        if td is None:
            continue
        for i, field in enumerate(td.fields):
            if i >= len(record.values):
                break
            value = record.values[i]
            if value is None or not isinstance(value, dict) or isinstance(value, list):
                continue

            ref_alias = get_referenced_type_alias(field.type_expr, result.schema)
            if ref_alias is None:
                continue
            ref_td = result.schema.get_type(ref_alias)
            if ref_td is None:
                continue
            ref_id_field = ref_td.get_id_field()
            if ref_id_field is None:
                continue

            ref_id_val = value.get(ref_id_field.name)
            if ref_id_val is None:
                continue

            alias_map = registry.get(ref_alias)
            if alias_map is None:
                alias_map = {}
                registry[ref_alias] = alias_map

            id_key = str(ref_id_val)
            if id_key not in alias_map:
                alias_map[id_key] = value

    return registry


def validate_references(
    result: MaxiParseResult,
    registry: dict[str, dict[str, Any]],
    filename: str | None = None,
    options: dict[str, Any] | None = None,
) -> None:
    """Validate that all scalar reference values resolve to a known object."""
    allow_forward = (options or {}).get("allow_forward_references", True)

    for record in result.records:
        td = result.schema.get_type(record.alias)
        if td is None:
            continue
        for i, field in enumerate(td.fields):
            if i >= len(record.values):
                break
            value = record.values[i]
            if value is None:
                continue
            if isinstance(value, dict):
                continue

            ref_alias = get_referenced_type_alias(field.type_expr, result.schema)
            if ref_alias is None:
                continue

            type_registry = registry.get(ref_alias)
            id_key = str(value)
            if type_registry is None or id_key not in type_registry:
                msg = (
                    f"Unresolved reference: field '{field.name}' in '{record.alias}' "
                    f"references {ref_alias} id '{value}', but no such object found"
                )
                if not allow_forward:
                    raise MaxiError(
                        msg,
                        MaxiErrorCode.UnresolvedReferenceError,
                        line=record.line_number,
                        filename=filename,
                    )
                result.add_warning(
                    msg,
                    code=MaxiErrorCode.UnresolvedReferenceError,
                    line=record.line_number,
                )
