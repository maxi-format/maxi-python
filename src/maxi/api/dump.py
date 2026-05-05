"""
Public dump API – ``dump_maxi`` and ``dump_maxi_auto``.
"""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from maxi.core.types import MaxiParseResult, MaxiRecord, _MISSING

if TYPE_CHECKING:
    from maxi.core.types import MaxiFieldDef, MaxiSchema, MaxiTypeDef

_NEEDS_QUOTING_RE = re.compile(r'[|()\[\]{}~,:\\"]|^\s|\s$')


def dump_maxi(
    data: Any,
    *,
    multiline: bool = False,
    include_types: bool = True,
    version: str | None = None,
    schema_file: str | None = None,
    types: dict[str, Any] | list[Any] | None = None,
    default_alias: str | None = None,
    collect_references: bool = True,
) -> str:
    """Serialize data into a MAXI string."""
    if isinstance(data, MaxiParseResult):
        return _dump_from_parse_result(data, multiline=multiline, include_types=include_types)

    data_map: dict[str, list[Any]]
    if isinstance(data, list):
        if not default_alias:
            raise ValueError("dump_maxi requires `default_alias` when dumping a list.")
        data_map = {default_alias: data}
    elif isinstance(data, dict):
        first_val = next(iter(data.values()), None) if data else None
        if not isinstance(first_val, list):
            if not default_alias:
                raise ValueError("dump_maxi requires `default_alias` when dumping a single object.")
            data_map = {default_alias: [data]}
        else:
            data_map = data
    else:
        data_map = {}

    input_obj = {
        "schema": {
            "version": version,
            "imports": [schema_file] if schema_file else [],
            "types": types,
        },
        "data": data_map,
    }

    return _dump_from_objects(
        input_obj,
        multiline=multiline,
        include_types=include_types,
        collect_references=collect_references,
    )


def dump_maxi_auto(
    objects: list[Any] | dict[str, list[Any]],
    *,
    multiline: bool = False,
    include_types: bool = True,
    version: str | None = None,
    schema_file: str | None = None,
    types: dict[str, Any] | list[Any] | None = None,
    default_alias: str | None = None,
    collect_references: bool = True,
) -> str:
    """Auto-detect schemas from class metadata, then dump."""
    from maxi.core.registry import get_maxi_schema

    if isinstance(objects, list):
        first_schema = get_maxi_schema(objects[0]) if objects else None
        alias = (first_schema or {}).get("alias") or default_alias
        if not alias:
            raise ValueError(
                "dump_maxi_auto: cannot determine alias. "
                "Attach '__maxi_schema__' to the class or pass `default_alias`."
            )
        data_map: dict[str, list[Any]] = {alias: objects}
    elif isinstance(objects, dict):
        data_map = objects
    else:
        raise TypeError("dump_maxi_auto: `objects` must be a list or {alias: list} dict.")

    collected: dict[str, Any] = {}
    for rows in data_map.values():
        for obj in (rows or []):
            if obj is not None and isinstance(obj, (dict,)) or hasattr(obj, "__dict__"):
                _collect_schemas_deep(obj, collected)

    if types:
        caller = _normalize_types(types)
        collected.update(caller)

    return dump_maxi(
        data_map,
        multiline=multiline,
        include_types=include_types,
        version=version,
        schema_file=schema_file,
        types=collected if collected else None,
        collect_references=collect_references,
    )


def _dump_from_parse_result(
    result: MaxiParseResult,
    *,
    multiline: bool,
    include_types: bool,
) -> str:
    out: list[str] = []

    schema = result.schema
    if schema.version and schema.version != "1.0.0":
        out.append(f"@version:{schema.version}")
    for imp in schema.imports:
        out.append(f"@schema:{imp}")

    if include_types and schema.types:
        if out:
            out.append("")
        for td in schema.types.values():
            out.append(_dump_type_def(td, multiline))

    if out:
        out.append("###")

    for record in result.records:
        out.append(_dump_record(record, multiline))

    return "\n".join(out)


def _dump_from_objects(
    input_obj: dict[str, Any],
    *,
    multiline: bool,
    include_types: bool,
    collect_references: bool,
) -> str:
    out: list[str] = []

    schema = input_obj.get("schema") or {}
    all_types = _normalize_types(schema.get("types"))

    _resolve_inheritance_for_dump(all_types)

    v = schema.get("version")
    if v and v != "1.0.0":
        out.append(f"@version:{v}")
    for imp in schema.get("imports") or []:
        out.append(f"@schema:{imp}")

    if include_types and all_types:
        if out:
            out.append("")
        for t in all_types.values():
            out.append(_dump_type_info(t, multiline))

    if out:
        out.append("###")

    records_to_dump: dict[str, list[Any]] = {}
    seen: set[int] = set()

    for alias, rows in (input_obj.get("data") or {}).items():
        records_to_dump.setdefault(alias, []).extend(rows or [])

    if collect_references:
        _collect_referenced_objects(all_types, records_to_dump, seen)

    for alias, rows in records_to_dump.items():
        t = all_types.get(alias)
        if not multiline and t and not collect_references and isinstance(t, dict):
            fields = t.get("fields") or []
            _nq_re = _NEEDS_QUOTING_RE
            _esc = _escape_string
            for obj in (rows or []):
                if not isinstance(obj, dict):
                    out.append(_dump_object_as_record(alias, obj, t, all_types, multiline, collect_references))
                    continue
                vals: list[str] = []
                for f in fields:
                    fn = f.get("name") if isinstance(f, dict) else getattr(f, "name", None)
                    if fn not in obj:
                        vals.append("")
                        continue
                    v = obj[fn]
                    if v is None:
                        vals.append("~")
                    elif isinstance(v, bool):
                        vals.append("1" if v else "0")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    elif isinstance(v, str):
                        if _nq_re.search(v):
                            vals.append(f'"{_esc(v)}"')
                        else:
                            vals.append(v)
                    elif isinstance(v, (list, dict)):
                        vals.append(_dump_value(v, f, all_types, collect_references))
                    else:
                        vals.append(str(v))
                li = len(vals) - 1
                while li >= 0 and vals[li] == "":
                    li -= 1
                out.append(f"{alias}({'|'.join(vals[:li + 1])})")
        else:
            for obj in (rows or []):
                out.append(_dump_object_as_record(alias, obj, t, all_types, multiline, collect_references))

    return "\n".join(out)


def _collect_referenced_objects(
    all_types: dict[str, Any],
    records_to_dump: dict[str, list[Any]],
    seen: set[int],
) -> None:
    work: list[tuple[str, Any]] = []

    for alias, rows in list(records_to_dump.items()):
        for obj in (rows or []):
            obj_id = id(obj)
            if obj is not None and isinstance(obj, dict) and obj_id not in seen:
                seen.add(obj_id)
                work.append((alias, obj))

    while work:
        alias, obj = work.pop()
        t = all_types.get(alias)
        if not t:
            continue
        for field in (t.get("fields") if isinstance(t, dict) else getattr(t, "fields", None)) or []:
            f_name = field.get("name") if isinstance(field, dict) else getattr(field, "name", None)
            f_type = field.get("typeExpr") if isinstance(field, dict) else getattr(field, "type_expr", None)
            if not f_name:
                continue
            v = obj.get(f_name) if isinstance(obj, dict) else getattr(obj, f_name, None)
            if v is None or not isinstance(v, (dict, list)):
                continue
            base_type = re.sub(r"\[\]$", "", f_type) if f_type else None
            nested = all_types.get(base_type) if base_type else None
            if not nested:
                continue
            nested_fields = (nested.get("fields") if isinstance(nested, dict) else getattr(nested, "fields", None)) or []
            id_field = next(
                (f for f in nested_fields if (f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) == "id"),
                None,
            )
            if not id_field:
                continue
            id_name = id_field.get("name") if isinstance(id_field, dict) else getattr(id_field, "name", None)
            nested_alias = nested.get("alias") if isinstance(nested, dict) else getattr(nested, "alias", None)
            if not nested_alias:
                continue

            items = v if isinstance(v, list) else [v]
            for item in items:
                if item is None or not isinstance(item, dict):
                    continue
                item_id = id(item)
                if item_id in seen:
                    continue
                if item.get(id_name) is not None:
                    records_to_dump.setdefault(nested_alias, []).append(item)
                    seen.add(item_id)
                    work.append((nested_alias, item))


def _normalize_types(types: Any) -> dict[str, Any]:
    if not types:
        return {}
    if isinstance(types, dict):
        return dict(types)
    if isinstance(types, list):
        return {t["alias"] if isinstance(t, dict) else t.alias: t for t in types}
    return {}


def _resolve_inheritance_for_dump(types: dict[str, Any]) -> None:
    resolved: set[str] = set()

    def resolve(alias: str) -> None:
        if alias in resolved:
            return
        t = types.get(alias)
        if not t:
            resolved.add(alias)
            return
        parents = (t.get("parents") if isinstance(t, dict) else getattr(t, "parents", None)) or []
        if not parents:
            resolved.add(alias)
            return

        own_fields = (t.get("fields") if isinstance(t, dict) else getattr(t, "fields", None)) or []
        own_names = {(f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) for f in own_fields}
        inherited: list[Any] = []

        for pa in parents:
            resolve(pa)
            parent = types.get(pa)
            if not parent:
                continue
            pfields = (parent.get("fields") if isinstance(parent, dict) else getattr(parent, "fields", None)) or []
            for pf in pfields:
                pn = pf.get("name") if isinstance(pf, dict) else getattr(pf, "name", None)
                if pn not in own_names:
                    inherited.append(pf)
                    own_names.add(pn)

        if inherited:
            if isinstance(t, dict):
                t["fields"] = inherited + list(own_fields)
            else:
                t.fields = inherited + list(own_fields)

        resolved.add(alias)

    for alias in list(types.keys()):
        resolve(alias)


def _dump_type_def(td: MaxiTypeDef, multiline: bool) -> str:
    header = f"{td.alias}:{td.name}" if td.name else td.alias
    parents = f"<{','.join(td.parents)}>" if td.parents else ""
    fields_str = "|".join(_dump_field(f) for f in td.fields)
    if not multiline:
        return f"{header}{parents}({fields_str})"
    body = "|\n".join(f"  {_dump_field(f)}" for f in td.fields)
    return f"{header}{parents}(\n{body}\n)"


def _dump_type_info(t: Any, multiline: bool) -> str:
    alias = t.get("alias") if isinstance(t, dict) else getattr(t, "alias", "")
    name = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
    parents = (t.get("parents") if isinstance(t, dict) else getattr(t, "parents", None)) or []
    fields = (t.get("fields") if isinstance(t, dict) else getattr(t, "fields", None)) or []

    header = f"{alias}:{name}" if name else alias
    parents_str = f"<{','.join(parents)}>" if parents else ""
    fields_strs = [_dump_field(f) for f in fields]
    if not multiline:
        return f"{header}{parents_str}({('|'.join(fields_strs))})"
    body = "|\n".join(f"  {s}" for s in fields_strs)
    return f"{header}{parents_str}(\n{body}\n)"


def _dump_field(field: Any) -> str:
    if isinstance(field, dict):
        name = field.get("name", "")
        type_expr = field.get("typeExpr") or field.get("type_expr")
        annotation = field.get("annotation")
        constraints = field.get("constraints") or []
        elem_constraints = field.get("elementConstraints") or field.get("element_constraints") or []
        default_value = field.get("defaultValue", field.get("default_value", _MISSING))
    else:
        name = getattr(field, "name", "")
        type_expr = getattr(field, "type_expr", None) or getattr(field, "typeExpr", None)
        annotation = getattr(field, "annotation", None)
        constraints = getattr(field, "constraints", None) or []
        elem_constraints = getattr(field, "element_constraints", None) or getattr(field, "elementConstraints", None) or []
        default_value = getattr(field, "default_value", getattr(field, "defaultValue", _MISSING))

    result = name

    if type_expr and elem_constraints and "[]" in type_expr:
        last_bracket = type_expr.rfind("[]")
        base = type_expr[:last_bracket]
        suffix = type_expr[last_bracket:]
        result += f":{base}"
        ec = [_dump_constraint(c) for c in elem_constraints]
        ec = [s for s in ec if s]
        if ec:
            result += f"({','.join(ec)})"
        result += suffix
        if constraints:
            cs = [_dump_constraint(c) for c in constraints]
            cs = [s for s in cs if s]
            if cs:
                result += f"({','.join(cs)})"
    else:
        if type_expr:
            result += f":{type_expr}"
        if annotation:
            result += f"@{annotation}"
        if constraints:
            cs = [_dump_constraint(c) for c in constraints]
            cs = [s for s in cs if s]
            if cs:
                result += f"({','.join(cs)})"

    if type_expr and elem_constraints and "[]" in type_expr and annotation:
        result += f"@{annotation}"

    if default_value is not _MISSING and default_value is not None:
        if isinstance(default_value, str) and _needs_quoting(default_value):
            def_str = f'"{_escape_string(default_value)}"'
        else:
            def_str = str(default_value)
        result += f"={def_str}"

    return result


def _dump_constraint(c: Any) -> str:
    if isinstance(c, dict):
        ct = c.get("type", "")
        cv = c.get("value")
        cop = c.get("operator", "")
    else:
        ct = getattr(c, "type", "")
        cv = getattr(c, "value", None)
        cop = getattr(c, "operator", "")

    if ct == "required":
        return "!"
    if ct == "id":
        return "id"
    if ct == "comparison":
        return f"{cop}{cv}"
    if ct == "pattern":
        return f"pattern:{cv}"
    if ct == "mime":
        if isinstance(cv, list) and len(cv) > 1:
            return f"mime:[{','.join(cv)}]"
        return f"mime:{cv[0] if isinstance(cv, list) else cv}"
    if ct == "decimal-precision":
        return str(cv) if cv is not None else ""
    if ct == "exact-length":
        return f"={cv}"
    return ""


def _dump_record(record: MaxiRecord, multiline: bool) -> str:
    values = [_dump_value(v, None, {}, True) for v in record.values]
    if not multiline:
        return f"{record.alias}({'|'.join(values)})"
    body = "|\n".join(f"  {v}" for v in values)
    return f"{record.alias}(\n{body}\n)"


def _dump_object_as_record(
    alias: str,
    obj: Any,
    t: Any,
    all_types: dict[str, Any],
    multiline: bool,
    collect_refs: bool,
) -> str:
    if t:
        fields = (t.get("fields") if isinstance(t, dict) else getattr(t, "fields", None)) or []
        vals: list[str] = []
        for f in fields:
            fn = f.get("name") if isinstance(f, dict) else getattr(f, "name", None)
            if isinstance(obj, dict):
                if fn not in obj:
                    vals.append("")
                    continue
                v = obj.get(fn)
            else:
                if not hasattr(obj, fn):
                    vals.append("")
                    continue
                v = getattr(obj, fn, None)
            if v is None:
                vals.append("~")
            else:
                vals.append(_dump_value(v, f, all_types, collect_refs))
    else:
        if isinstance(obj, dict):
            raw = list(obj.values())
        else:
            raw = list(vars(obj).values()) if hasattr(obj, "__dict__") else []
        vals = ["~" if v is None else _dump_value(v, None, all_types, collect_refs) for v in raw]

    last = len(vals) - 1
    while last >= 0 and vals[last] == "":
        last -= 1
    vals = vals[: last + 1]

    if not multiline:
        return f"{alias}({'|'.join(vals)})"
    body = "|\n".join(f"  {v}" for v in vals)
    return f"{alias}(\n{body}\n)"


def _dump_value(
    value: Any,
    field_info: Any,
    all_types: dict[str, Any],
    collect_refs: bool,
) -> str:
    if value is None:
        return "~"

    if isinstance(value, bytes):
        import base64
        ann = _field_attr(field_info, "annotation")
        if ann == "hex":
            return value.hex()
        return base64.b64encode(value).decode("ascii")

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, str):
        return f'"{_escape_string(value)}"' if _needs_quoting(value) else value

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, list):
        te = _field_attr(field_info, "typeExpr") or _field_attr(field_info, "type_expr")
        elem_te: str | None = None
        if isinstance(te, str) and te.endswith("[]"):
            elem_te = te[:-2]
        elem_fi = _with_type_expr(field_info, elem_te) if elem_te else field_info
        inner = ",".join(_dump_value(v, elem_fi, all_types, collect_refs) for v in value)
        return f"[{inner}]"

    if isinstance(value, dict):
        te = _field_attr(field_info, "typeExpr") or _field_attr(field_info, "type_expr")
        ref_type = re.sub(r"\[\]$", "", te) if te else None
        nested = all_types.get(ref_type) if ref_type else None
        if nested:
            nested_fields = (nested.get("fields") if isinstance(nested, dict) else getattr(nested, "fields", None)) or []
            id_field = next(
                (f for f in nested_fields if (f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) == "id"),
                None,
            )
            id_name = (id_field.get("name") if isinstance(id_field, dict) else getattr(id_field, "name", None)) if id_field else None
            if id_name and value.get(id_name) is not None:
                if not collect_refs:
                    return _dump_inline_object(value, nested, all_types, collect_refs)
                return _dump_value(value[id_name], None, all_types, collect_refs)
            return _dump_inline_object(value, nested, all_types, collect_refs)
        entries = ",".join(
            f"{_dump_map_key(k)}:{_dump_value(v, None, all_types, collect_refs)}"
            for k, v in value.items()
        )
        return f"{{{entries}}}"

    if hasattr(value, "__dict__"):
        te = _field_attr(field_info, "typeExpr") or _field_attr(field_info, "type_expr")
        ref_type = re.sub(r"\[\]$", "", te) if te else None
        nested = all_types.get(ref_type) if ref_type else None
        if nested:
            nested_fields = (nested.get("fields") if isinstance(nested, dict) else getattr(nested, "fields", None)) or []
            id_field = next(
                (f for f in nested_fields if (f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) == "id"),
                None,
            )
            id_name = (id_field.get("name") if isinstance(id_field, dict) else getattr(id_field, "name", None)) if id_field else None
            if id_name:
                id_val = getattr(value, id_name, None)
                if id_val is not None:
                    if not collect_refs:
                        obj_dict = {f_name: getattr(value, f_name, None) for f_name in
                                    ((f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) for f in nested_fields)}
                        return _dump_inline_object(obj_dict, nested, all_types, collect_refs)
                    return _dump_value(id_val, None, all_types, collect_refs)

    return str(value)


def _dump_inline_object(
    obj: dict[str, Any],
    type_def: Any,
    all_types: dict[str, Any],
    collect_refs: bool,
) -> str:
    fields = (type_def.get("fields") if isinstance(type_def, dict) else getattr(type_def, "fields", None)) or []
    vals: list[str] = []
    for f in fields:
        fn = f.get("name") if isinstance(f, dict) else getattr(f, "name", None)
        if fn not in obj:
            vals.append("")
            continue
        v = obj.get(fn)
        if v is None:
            vals.append("~")
        else:
            vals.append(_dump_value(v, f, all_types, collect_refs))

    last = len(vals) - 1
    while last >= 0 and vals[last] == "":
        last -= 1
    return f"({'|'.join(vals[:last + 1])})"


def _needs_quoting(s: str) -> bool:
    return bool(_NEEDS_QUOTING_RE.search(s))


def _escape_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _dump_map_key(k: str) -> str:
    return f'"{_escape_string(k)}"' if _needs_quoting(k) else k



def _field_attr(field_info: Any, attr: str) -> Any:
    if field_info is None:
        return None
    if isinstance(field_info, dict):
        return field_info.get(attr)
    return getattr(field_info, attr, None)


def _with_type_expr(field_info: Any, type_expr: str) -> dict[str, Any]:
    """Return a field-info-like dict with the given typeExpr."""
    if isinstance(field_info, dict):
        return {**field_info, "typeExpr": type_expr, "type_expr": type_expr}
    return {"typeExpr": type_expr, "type_expr": type_expr}


def _collect_schemas_deep(obj: Any, collected: dict[str, Any]) -> None:
    from maxi.core.registry import get_maxi_schema

    if obj is None:
        return
    schema = get_maxi_schema(obj)
    if not schema:
        return
    alias = schema.get("alias") if isinstance(schema, dict) else getattr(schema, "alias", None)
    if not alias or alias in collected:
        return
    collected[alias] = schema
    fields = (schema.get("fields") if isinstance(schema, dict) else getattr(schema, "fields", None)) or []
    for field in fields:
        fn = field.get("name") if isinstance(field, dict) else getattr(field, "name", None)
        if not fn:
            continue
        v = getattr(obj, fn, None) if hasattr(obj, fn) else (obj.get(fn) if isinstance(obj, dict) else None)
        if v is None:
            continue
        items = v if isinstance(v, list) else [v]
        for item in items:
            if item is not None and (isinstance(item, dict) or hasattr(item, "__dict__")):
                _collect_schemas_deep(item, collected)
