"""
Public parse API – ``parse_maxi``, ``parse_maxi_as``, ``parse_maxi_auto_as``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from maxi.core.types import MaxiHydrateResult, MaxiParseResult
from maxi.internal.record_parser import RecordParser
from maxi.internal.reference_resolver import build_object_registry, validate_references
from maxi.internal.schema_parser import SchemaParser

if TYPE_CHECKING:
    from maxi.core.types import MaxiSchema, MaxiTypeDef

_NON_REF_TYPES = frozenset({"str", "int", "decimal", "float", "bool", "bytes"})


async def parse_maxi(
    input: str,
    *,
    allow_additional_fields: str = "ignore",
    allow_missing_fields: str = "null",
    allow_type_coercion: str = "coerce",
    allow_constraint_violations: str = "warning",
    allow_forward_references: bool = True,
    allow_unknown_types: str = "warning",
    filename: str | None = None,
    load_schema: Callable[[str], str | Awaitable[str]] | None = None,
) -> MaxiParseResult:
    """Parse a MAXI document into schema + records."""
    result = MaxiParseResult()

    options: dict[str, Any] = {
        "filename": filename,
        "allow_additional_fields": allow_additional_fields,
        "allow_missing_fields": allow_missing_fields,
        "allow_type_coercion": allow_type_coercion,
        "allow_constraint_violations": allow_constraint_violations,
        "allow_forward_references": allow_forward_references,
        "allow_unknown_types": allow_unknown_types,
    }
    if load_schema is not None:
        options["load_schema"] = load_schema

    schema_section, records_section = _split_sections(input)

    schema_parser = SchemaParser(schema_section, result, options)
    await schema_parser.parse()

    if records_section:
        record_parser = RecordParser(records_section, result, options)
        await record_parser.parse()

    if result.records and result.schema.types:
        has_refs = False
        for td in result.schema.types.values():
            for field in td.fields:
                te = field.type_expr
                if (
                    te
                    and te not in _NON_REF_TYPES
                    and not te.startswith("enum")
                    and te != "map"
                    and not te.startswith("map<")
                ):
                    has_refs = True
                    break
            if has_refs:
                break

        if has_refs:
            registry = build_object_registry(result)
            result._object_registry = registry  # type: ignore[attr-defined]
            validate_references(result, registry, filename, options)

    return result


async def parse_maxi_as(
    input: str,
    class_map: dict[str, type],
    *,
    filename: str | None = None,
    load_schema: Callable[[str], str | Awaitable[str]] | None = None,
    **kwargs: Any,
) -> MaxiHydrateResult:
    """Parse and hydrate records into Python class instances."""
    if not isinstance(class_map, dict):
        raise TypeError("parse_maxi_as: class_map must be a {alias: Class} dict.")

    result = await parse_maxi(input, filename=filename, load_schema=load_schema, **kwargs)
    return _hydrate_result(result, class_map)


async def parse_maxi_auto_as(
    input: str,
    classes: list[type],
    *,
    filename: str | None = None,
    load_schema: Callable[[str], str | Awaitable[str]] | None = None,
    **kwargs: Any,
) -> MaxiHydrateResult:
    """Auto-detect alias→class mapping from class metadata, then hydrate."""
    from maxi.core.registry import get_maxi_schema

    if not isinstance(classes, list):
        raise TypeError("parse_maxi_auto_as: second argument must be a list of classes.")

    class_map: dict[str, type] = {}
    for cls in classes:
        schema = get_maxi_schema(cls)
        if schema is None:
            raise ValueError(
                f"parse_maxi_auto_as: no maxi schema found for class '{cls.__name__}'. "
                "Attach a '__maxi_schema__' attribute or use define_maxi_schema()."
            )
        class_map[schema["alias"]] = cls

    return await parse_maxi_as(input, class_map, filename=filename, load_schema=load_schema, **kwargs)


def _split_sections(input: str) -> tuple[str, str | None]:
    """Split a MAXI document at the ``###`` separator."""
    idx = input.find("###")
    while idx != -1:
        line_start = input.rfind("\n", 0, idx)
        line_start = line_start + 1 if line_start != -1 else 0
        before = input[line_start:idx]
        if not before.strip():
            end = idx + 3
            n = len(input)
            while end < n and input[end] in (' ', '\t'):
                end += 1
            if end >= n or input[end] in ('\n', '\r'):
                schema_section = input[:idx].strip()
                if end < n and input[end] == '\r':
                    end += 1
                if end < n and input[end] == '\n':
                    end += 1
                records_section = input[end:].strip()
                return (schema_section, records_section or None)
        idx = input.find("###", idx + 1)

    m = re.search(r"^[ \t]*###[ \t]*(?:\r?\n|$)", input, re.MULTILINE)

    if not m:
        has_directive = bool(re.search(r"^[ \t]*@", input, re.MULTILINE))
        has_explicit = bool(
            re.search(r"^[ \t]*[A-Za-z_][A-Za-z0-9_-]*[ \t]*:", input, re.MULTILINE)
        )
        has_inherit = bool(
            re.search(r"^[ \t]*[A-Za-z_][A-Za-z0-9_-]*[ \t]*<[^>]+>[ \t]*\(", input, re.MULTILINE)
        )
        if has_explicit or has_inherit:
            return (input, None)
        if has_directive:
            _record_start = re.compile(
                r"^[ \t]*([A-Za-z_][A-Za-z0-9_-]*)[ \t]*\([ \t]*"
                r'(?:[\d"~\[\{(]|-\d)',
                re.MULTILINE
            )
            rec_m = _record_start.search(input)
            if rec_m:
                split_pos = rec_m.start()
                schema_part = input[:split_pos].strip()
                records_part = input[split_pos:].strip()
                return (schema_part, records_part or None)
            return (input, None)
        return ("", input)

    schema_section = input[: m.start()].strip()
    records_section = input[m.end() :].strip()
    return (schema_section, records_section or None)


def _hydrate_result(result: MaxiParseResult, class_map: dict[str, type]) -> MaxiHydrateResult:
    from maxi.core.registry import get_maxi_schema

    schema_by_alias: dict[str, MaxiTypeDef] = {}
    for alias, cls in class_map.items():
        parsed = result.schema.get_type(alias)
        if parsed:
            schema_by_alias[alias] = parsed
        else:
            cs = get_maxi_schema(cls)
            if cs:
                schema_by_alias[alias] = cs  # type: ignore[assignment]

    objects: dict[str, list[Any]] = {}
    instance_registry: dict[str, dict[str, Any]] = {}

    for record in result.records:
        cls_val: type | None = class_map.get(record.alias)
        if cls_val is None:
            continue
        cls = cls_val

        td = schema_by_alias.get(record.alias)
        field_map = _record_to_field_map(record, td)
        instance = _construct(cls, field_map)

        objects.setdefault(record.alias, []).append(instance)

        id_field_name = _find_id_field(td)
        if id_field_name:
            id_val = field_map.get(id_field_name)
            if id_val is not None:
                instance_registry.setdefault(record.alias, {})[str(id_val)] = instance

    _resolve_references(objects, schema_by_alias, instance_registry, result.schema)

    return MaxiHydrateResult(
        schema=result.schema,
        data=objects,
        warnings=result.warnings,
    )


def _record_to_field_map(record: Any, schema: Any) -> dict[str, Any]:
    fields = getattr(schema, "fields", []) if schema else []
    result: dict[str, Any] = {}
    if fields:
        for i, f in enumerate(fields):
            result[f.name] = record.values[i] if i < len(record.values) else None
    else:
        for i, v in enumerate(record.values):
            result[str(i)] = v
    return result


def _construct(cls: type, field_map: dict[str, Any]) -> Any:
    """Try to instantiate *cls* with *field_map* values."""
    first_key = next(iter(field_map), None)

    try:
        inst = cls(**field_map)
        if first_key is None or getattr(inst, first_key, object()) == field_map.get(first_key):
            return inst
    except Exception:
        pass

    try:
        inst = cls()
        for k, v in field_map.items():
            setattr(inst, k, v)
        return inst
    except Exception:
        pass

    inst = object.__new__(cls)
    for k, v in field_map.items():
        try:
            setattr(inst, k, v)
        except Exception:
            pass
    return inst


def _find_id_field(schema: Any) -> str | None:
    fields = getattr(schema, "fields", None)
    if not fields:
        return None
    for f in fields:
        if f.constraints and any(c.type == "id" for c in f.constraints):
            return f.name
    for f in fields:
        if f.name == "id":
            return f.name
    return None


def _resolve_references(
    objects: dict[str, list[Any]],
    schema_by_alias: dict[str, Any],
    instance_registry: dict[str, dict[str, Any]],
    parsed_schema: MaxiSchema,
) -> None:
    for alias, instances in objects.items():
        td = schema_by_alias.get(alias)
        if not td or not getattr(td, "fields", None):
            continue
        for instance in instances:
            for field in td.fields:
                ref_alias = _get_ref_alias(field.type_expr, parsed_schema)
                if not ref_alias:
                    continue
                ref_registry = instance_registry.get(ref_alias)
                if not ref_registry:
                    continue
                current = getattr(instance, field.name, None)
                if current is None or isinstance(current, (dict, list)):
                    continue
                resolved = ref_registry.get(str(current))
                if resolved is not None:
                    setattr(instance, field.name, resolved)


def _get_ref_alias(type_expr: str | None, parsed_schema: MaxiSchema) -> str | None:
    if not type_expr:
        return None
    t = re.sub(r"(\[\])+$", "", type_expr.strip())
    if t in _NON_REF_TYPES:
        return None
    if t == "map" or t.startswith("map<"):
        return None
    if t.startswith("enum"):
        return None
    if parsed_schema.has_type(t):
        return t
    return None
