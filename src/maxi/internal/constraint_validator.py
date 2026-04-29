"""
Constraint validation – schema-level and record-level.
"""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from maxi.core.errors import MaxiError, MaxiErrorCode

if TYPE_CHECKING:
    from maxi.core.types import MaxiFieldDef, MaxiParseResult, MaxiSchema, MaxiTypeDef

_ANNOTATION_TYPE_MAP: dict[str, list[str]] = {
    "base64": ["bytes"],
    "hex": ["bytes"],
    "timestamp": ["int"],
    "date": ["str"],
    "datetime": ["str"],
    "time": ["str"],
    "email": ["str"],
    "url": ["str"],
    "uuid": ["str"],
}

_PRIMITIVES = frozenset({"int", "decimal", "float", "str", "bool", "bytes"})


def _get_base_type_name(type_expr: str | None) -> str | None:
    if not type_expr:
        return "str"
    t = type_expr.strip()
    no_arr = re.sub(r"(\[\])+$", "", t)
    if no_arr in _PRIMITIVES:
        return no_arr
    if no_arr == "map" or no_arr.startswith("map<"):
        return "map"
    if no_arr.startswith("enum"):
        return "enum"
    return None


def _parse_enum_type_expr(type_expr: str | None) -> dict[str, Any] | None:
    if not type_expr:
        return None
    t = type_expr.strip()
    if not t.startswith("enum"):
        return None
    m = re.match(r"^enum(?:<(\w+)>)?\[([^\]]*)\]$", t)
    if not m:
        return None
    base_type = m.group(1) or "str"
    values = [v.strip() for v in m.group(2).split(",") if v.strip()]
    return {"base_type": base_type, "values": values}


def validate_schema_constraints(schema: MaxiSchema, filename: str | None = None) -> None:
    """Validate annotation compatibility and constraint conflicts for all types."""
    for type_def in schema.types.values():
        for field in type_def.fields:
            _validate_annotation_type_compat(field, type_def.alias, filename)
            _validate_constraint_conflicts(field, type_def.alias, filename)


def _validate_annotation_type_compat(
    field: MaxiFieldDef, type_alias: str, filename: str | None
) -> None:
    if not field.annotation:
        return
    allowed = _ANNOTATION_TYPE_MAP.get(field.annotation)
    if allowed is None:
        return
    base = _get_base_type_name(field.type_expr)
    if base is None:
        return
    if base not in allowed:
        raise MaxiError(
            f"Type annotation '@{field.annotation}' cannot be applied to "
            f"'{base}' field '{field.name}' in type '{type_alias}'",
            MaxiErrorCode.InvalidConstraintValueError,
            filename=filename,
        )


def _validate_constraint_conflicts(
    field: MaxiFieldDef, type_alias: str, filename: str | None
) -> None:
    constraints = field.constraints
    if not constraints or len(constraints) < 2:
        return

    min_ge: float | None = None
    min_gt: float | None = None
    max_le: float | None = None
    max_lt: float | None = None

    for c in constraints:
        if c.type != "comparison":
            continue
        v = c.value
        if not isinstance(v, (int, float)):
            continue
        op = getattr(c, "operator", None)
        if op == ">=":
            min_ge = max(min_ge, v) if min_ge is not None else v
        elif op == ">":
            min_gt = max(min_gt, v) if min_gt is not None else v
        elif op == "<=":
            max_le = min(max_le, v) if max_le is not None else v
        elif op == "<":
            max_lt = min(max_lt, v) if max_lt is not None else v

    eff_min = min_ge if min_ge is not None else (min_gt + 1 if min_gt is not None else None)
    eff_max = max_le if max_le is not None else (max_lt - 1 if max_lt is not None else None)

    conflict = False
    if eff_min is not None and eff_max is not None and eff_min > eff_max:
        conflict = True
    if min_ge is not None and max_lt is not None and min_ge >= max_lt:
        conflict = True
    if min_gt is not None and max_le is not None and min_gt >= max_le:
        conflict = True

    if conflict:
        raise MaxiError(
            f"Conflicting constraints in field '{field.name}' of type "
            f"'{type_alias}': lower bound exceeds upper bound",
            MaxiErrorCode.InvalidConstraintValueError,
            filename=filename,
        )



def validate_record_constraints(
    values: list[Any],
    type_def: MaxiTypeDef,
    is_strict: bool,
    result: MaxiParseResult,
    line_number: int,
    filename: str | None = None,
) -> None:
    """Validate a single record's values against field constraints."""
    for i, field in enumerate(type_def.fields):
        value = values[i] if i < len(values) else None
        constraints = field.constraints
        if not constraints:
            continue
        if value is None:
            continue
        for c in constraints:
            violation = _check_constraint(c, value, field)
            if violation:
                if is_strict:
                    raise MaxiError(
                        violation,
                        MaxiErrorCode.ConstraintViolationError,
                        line=line_number,
                        filename=filename,
                    )
                result.add_warning(
                    violation,
                    code=MaxiErrorCode.ConstraintViolationError,
                    line=line_number,
                )


def _check_constraint(constraint: Any, value: Any, field: MaxiFieldDef) -> str | None:
    t = constraint.type
    if t in ("required", "id", "mime", "decimal-precision"):
        return None
    if t == "comparison":
        return _check_comparison(constraint, value, field)
    if t == "pattern":
        return _check_pattern(constraint, value, field)
    if t == "exact-length":
        return _check_exact_length(constraint, value, field)
    return None


def _check_comparison(constraint: Any, value: Any, field: MaxiFieldDef) -> str | None:
    op = constraint.operator
    limit = constraint.value
    if not isinstance(limit, (int, float)):
        return None

    base = _get_base_type_name(field.type_expr)

    if base in ("str", "bytes") or (base is None and isinstance(value, str)):
        if not isinstance(value, str):
            return None
        actual: int | float = len(value)
    elif isinstance(value, (int, float)):
        actual = value
    else:
        return None

    if op == ">=" and actual < limit:
        return f"Field '{field.name}': value {actual} violates constraint >={limit}"
    if op == ">" and actual <= limit:
        return f"Field '{field.name}': value {actual} violates constraint >{limit}"
    if op == "<=" and actual > limit:
        return f"Field '{field.name}': value {actual} violates constraint <={limit}"
    if op == "<" and actual >= limit:
        return f"Field '{field.name}': value {actual} violates constraint <{limit}"
    return None


def _check_pattern(constraint: Any, value: Any, field: MaxiFieldDef) -> str | None:
    if not isinstance(value, str):
        return None
    if not re.search(constraint.value, value):
        return f"Field '{field.name}': value '{value}' does not match pattern '{constraint.value}'"
    return None


def _check_exact_length(constraint: Any, value: Any, field: MaxiFieldDef) -> str | None:
    length: int | None = None
    if isinstance(value, list):
        length = len(value)
    elif isinstance(value, dict):
        length = len(value)
    if length is not None and length != constraint.value:
        return f"Field '{field.name}': expected exactly {constraint.value} elements, got {length}"
    return None


def validate_enum_value(
    type_expr: str | None,
    value: Any,
    field_name: str,
    is_strict: bool,
    result: MaxiParseResult,
    line_number: int,
    filename: str | None = None,
) -> None:
    """Validate an enum field value against the allowed set."""
    if value is None:
        return
    info = _parse_enum_type_expr(type_expr)
    if info is None:
        return
    str_value = str(value)
    if str_value not in info["values"]:
        msg = f"Value '{str_value}' not in enum [{','.join(info['values'])}] for field '{field_name}'"
        if is_strict:
            raise MaxiError(msg, MaxiErrorCode.ConstraintViolationError, line=line_number, filename=filename)
        result.add_warning(msg, code=MaxiErrorCode.ConstraintViolationError, line=line_number)
