"""
Record phase parser – parse data records after the ``###`` separator.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, TYPE_CHECKING

from maxi.core.errors import MaxiError, MaxiErrorCode
from maxi.core.types import MaxiRecord, _MISSING
from maxi.internal.constraint_validator import validate_record_constraints

if TYPE_CHECKING:
    from maxi.core.types import MaxiFieldDef, MaxiParseResult, MaxiTypeDef

# Pre-compiled regex for fast record boundary detection
_RECORD_START_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*\(')
# Pre-compiled regex for integer detection (most common numeric type)
_INT_RE = re.compile(r'^-?\d+$')
_DECIMAL_RE = re.compile(r'^-?\d+\.\d+$')
_TRAILING_DOT_RE = re.compile(r'^-?\d+\.$')
# Set of chars that force complex parsing in records
_COMPLEX_CHARS_SET = frozenset('"()[]{}~')


class RecordParser:
    """Parse the records section of a MAXI document."""

    _PRIMITIVES = frozenset({"str", "int", "decimal", "bool", "bytes", "map", "float"})
    _EXPLICIT_NULL = object()  # sentinel: explicit ~ in the data

    def __init__(
        self,
        records_text: str,
        result: MaxiParseResult,
        options: dict[str, Any],
    ) -> None:
        self.records_text = records_text
        self.result = result
        self.options = options
        self.seen_ids: dict[str, set[str]] = {}
        self._is_strict: bool = result.schema.mode == "strict"
        self._filename: str | None = options.get("filename")
        # Pre-computed per-type field info for fast-path parsing
        self._type_field_cache: dict[str, _TypeFieldInfo | None] = {}

    # ── public entry point ───────────────────────────────────────────────

    async def parse(self) -> None:
        text = self.records_text
        if not text or not text.strip():
            return

        # Pre-build field info cache for all known types
        for alias, td in self.result.schema.types.items():
            td._ensure_cache()
            self._type_field_cache[alias] = _TypeFieldInfo(td)

        length = len(text)
        i = 0
        line_number = 1
        at_line_start = True
        result_records = self.result.records  # local ref
        _parse_single = self._parse_single_record

        # Use str.find to skip whitespace-only / newline regions faster
        while i < length:
            ch = text[i]

            if ch == "\n":
                line_number += 1
                i += 1
                at_line_start = True
                continue

            if ch <= " ":  # space, tab, \r, and other control chars
                i += 1
                continue

            # Comment line: skip to end of line
            if ch == "#":
                at_line_start = False
                while i < length and text[i] != "\n":
                    i += 1
                continue

            # Record must start with identifier char: A-Z, a-z, _
            if not (ch.isalpha() or ch == "_"):
                if at_line_start:
                    raise MaxiError(
                        f"Invalid syntax in data section: unexpected character '{ch}' at line {line_number}",
                        MaxiErrorCode.InvalidSyntaxError,
                        line=line_number,
                        filename=self._filename,
                    )
                i += 1
                continue

            at_line_start = False

            # Parse alias using find for the opening paren
            alias_start = i
            i += 1
            while i < length:
                c = text[i]
                if c.isalnum() or c == "_" or c == "-":
                    i += 1
                else:
                    break
            alias = text[alias_start:i]

            # Skip whitespace before '(' or ':'
            while i < length and text[i] <= " " and text[i] != "\n":
                i += 1

            # Type definition after ### is a structural error (StreamError)
            if i < length and text[i] == ":":
                raise MaxiError(
                    f"Type definition '{alias}:...' found in data section (after ###). "
                    f"Type definitions must appear before ###.",
                    MaxiErrorCode.StreamError,
                    line=line_number,
                    filename=self._filename,
                )

            if i >= length or text[i] != "(":
                continue

            record_line = line_number
            i += 1  # past '('
            values_start = i

            # Fast path: use str.index to find ')' — works when no nesting
            try:
                close_pos = text.index(")", i)
            except ValueError:
                raise MaxiError(
                    f"Unclosed record parentheses for '{alias}'",
                    MaxiErrorCode.InvalidSyntaxError,
                    line=record_line,
                    filename=self._filename,
                )

            # Check if content is simple (no nesting chars between i and close_pos)
            segment = text[values_start:close_pos]
            needs_complex = False
            for ch in segment:
                if ch == '"' or ch == '(' or ch == '[' or ch == '{' or ch == '\n':
                    needs_complex = True
                    break

            if not needs_complex:
                values_str = segment
                i = close_pos + 1
            else:
                # Slow path: handle nesting & strings
                paren_depth = 1
                bracket_depth = 0
                brace_depth = 0
                in_string = False
                escape_next = False

                while i < length:
                    c = text[i]
                    if c == "\n":
                        line_number += 1
                    if escape_next:
                        escape_next = False
                        i += 1
                        continue
                    if in_string:
                        if c == "\\":
                            escape_next = True
                        elif c == '"':
                            in_string = False
                        i += 1
                        continue
                    if c == '"':
                        in_string = True
                        i += 1
                        continue
                    if c == "(":
                        paren_depth += 1
                    elif c == ")":
                        paren_depth -= 1
                        if paren_depth == 0:
                            break
                    elif c == "[":
                        bracket_depth += 1
                    elif c == "]":
                        bracket_depth = max(0, bracket_depth - 1)
                    elif c == "{":
                        brace_depth += 1
                    elif c == "}":
                        brace_depth = max(0, brace_depth - 1)
                    i += 1

                if i >= length or text[i] != ")" or paren_depth != 0 or bracket_depth != 0 or brace_depth != 0:
                    if bracket_depth != 0:
                        raise MaxiError(
                            f"Malformed array: unmatched bracket in record '{alias}'",
                            MaxiErrorCode.ArraySyntaxError,
                            line=record_line,
                            filename=self._filename,
                        )
                    raise MaxiError(
                        f"Unclosed record parentheses for '{alias}'",
                        MaxiErrorCode.InvalidSyntaxError,
                        line=record_line,
                        filename=self._filename,
                    )

                values_str = text[values_start:i]
                i += 1  # past ')'

            record = _parse_single(alias, values_str, record_line)
            result_records.append(record)

    # ── single record ────────────────────────────────────────────────────

    def _parse_single_record(self, alias: str, values_str: str, line_number: int) -> MaxiRecord:
        type_def = self.result.schema.get_type(alias)

        if type_def is None:
            error = MaxiError(
                f"Unknown type alias '{alias}'",
                MaxiErrorCode.UnknownTypeError,
                line=line_number,
                filename=self._filename,
            )
            if self._is_strict:
                raise error
            self.result.add_warning(error.args[0], code=error.code, line=line_number)
            values = self._parse_field_values(values_str, None, line_number)
            return MaxiRecord(alias=alias, values=values, line_number=line_number)

        tfi = self._type_field_cache.get(alias)

        # Try fast path for simple records (no special chars)
        if tfi and tfi.can_fast_parse:
            fast_result = self._try_fast_parse(values_str, type_def, tfi, line_number, alias)
            if fast_result is not None:
                return fast_result

        values = self._parse_field_values(values_str, type_def, line_number)

        # LAX heuristic for inherited 'type' field
        if not self._is_strict:
            type_field_idx = next(
                (i for i, f in enumerate(type_def.fields) if f.name == "type"), -1
            )
            if type_field_idx != -1 and len(values) == len(type_def.fields) - 1:
                tf = type_def.fields[type_field_idx]
                if tf.default_value is not _MISSING and tf.default_value is not None:
                    inferred = tf.default_value
                elif type_def.name:
                    inferred = str(type_def.name).lower()
                else:
                    inferred = str(type_def.alias).lower()
                values = values[:type_field_idx] + [inferred] + values[type_field_idx:]

        # Strict field count validation
        if self._is_strict:
            if len(values) < len(type_def.fields):
                flags = type_def.get_required_flags()
                for idx in range(len(values), len(type_def.fields)):
                    field = type_def.fields[idx]
                    if flags[idx] and field.default_value is _MISSING:
                        raise MaxiError(
                            f"Record '{alias}' missing required field '{field.name}'",
                            MaxiErrorCode.MissingRequiredFieldError,
                            line=line_number,
                            filename=self._filename,
                        )
            elif len(values) > len(type_def.fields):
                raise MaxiError(
                    f"Record '{alias}' has {len(values)} values but type defines {len(type_def.fields)} fields",
                    MaxiErrorCode.SchemaMismatchError,
                    line=line_number,
                    filename=self._filename,
                )

        # Fill defaults, validate required
        field_count = len(type_def.fields)
        final_values: list[Any] = [None] * field_count
        req_flags = type_def.get_required_flags()

        for idx in range(field_count):
            field = type_def.fields[idx]
            value = values[idx] if idx < len(values) else None

            # Explicit null (~) → None, do NOT apply default
            if value is self._EXPLICIT_NULL:
                if req_flags[idx] and field.default_value is not _MISSING:
                    error = MaxiError(
                        f"Field '{field.name}' is required with a default; explicit null (~) is not allowed",
                        MaxiErrorCode.MissingRequiredFieldError,
                        line=line_number,
                        filename=self._filename,
                    )
                    if self._is_strict:
                        raise error
                    self.result.add_warning(error.args[0], code=error.code, line=line_number)
                value = None
            elif value is None or value == "":
                if field.default_value is not _MISSING:
                    value = field.default_value
                else:
                    value = None

            if req_flags[idx] and value is None:
                error = MaxiError(
                    f"Required field '{field.name}' is null in record '{alias}'",
                    MaxiErrorCode.MissingRequiredFieldError,
                    line=line_number,
                    filename=self._filename,
                )
                if self._is_strict:
                    raise error
                self.result.add_warning(error.args[0], code=error.code, line=line_number)

            final_values[idx] = value

        # Enum validation — only check fields that actually have enums
        if tfi and tfi.enum_field_indices:
            enum_values_cache = tfi.enum_values_list
            for idx in tfi.enum_field_indices:
                enum_vals = enum_values_cache[idx]
                val = final_values[idx]
                if val is not None:
                    sv = str(val)
                    if sv not in enum_vals:
                        msg = f"Value '{sv}' not in enum [{','.join(enum_vals)}] for field '{type_def.fields[idx].name}'"
                        if self._is_strict:
                            raise MaxiError(msg, MaxiErrorCode.ConstraintViolationError, line=line_number, filename=self._filename)
                        self.result.add_warning(msg, code=MaxiErrorCode.ConstraintViolationError, line=line_number)
        else:
            # Fallback for uncached
            for idx in range(field_count):
                enum_vals = type_def.get_enum_values(idx)
                if enum_vals:
                    val = final_values[idx]
                    if val is not None:
                        sv = str(val)
                        if sv not in enum_vals:
                            msg = f"Value '{sv}' not in enum [{','.join(enum_vals)}] for field '{type_def.fields[idx].name}'"
                            if self._is_strict:
                                raise MaxiError(msg, MaxiErrorCode.ConstraintViolationError, line=line_number, filename=self._filename)
                            self.result.add_warning(msg, code=MaxiErrorCode.ConstraintViolationError, line=line_number)

        # Runtime constraint validation
        if type_def.has_runtime_constraints:
            validate_record_constraints(final_values, type_def, self._is_strict, self.result, line_number, self._filename)

        # Duplicate ID detection
        id_idx = tfi.id_field_index if tfi else type_def.get_id_field_index()
        if 0 <= id_idx < len(final_values):
            id_val = final_values[id_idx]
            if id_val is not None:
                seen = self.seen_ids.get(alias)
                if seen is None:
                    seen = set()
                    self.seen_ids[alias] = seen
                id_key = str(id_val)
                if id_key in seen:
                    msg = f"Duplicate identifier '{id_val}' for type '{alias}'"
                    if self._is_strict:
                        raise MaxiError(msg, MaxiErrorCode.DuplicateIdentifierError, line=line_number, filename=self._filename)
                    self.result.add_warning(msg, code=MaxiErrorCode.DuplicateIdentifierError, line=line_number)
                seen.add(id_key)

        return MaxiRecord(alias=alias, values=final_values, line_number=line_number)

    # ── fast-path record parsing ─────────────────────────────────────────

    def _try_fast_parse(
        self, values_str: str, type_def: MaxiTypeDef, tfi: _TypeFieldInfo,
        line_number: int, alias: str,
    ) -> MaxiRecord | None:
        """Attempt fast-path parsing for simple records with no special chars."""
        # Quick reject: any special characters that need complex parsing
        # Use a set intersection check instead of per-char loop
        if _COMPLEX_CHARS_SET.intersection(values_str):
            return None

        parts = values_str.split("|")
        parts = [p.strip() for p in parts]
        field_count = len(type_def.fields)
        field_kinds = tfi.field_kinds
        req_flags = tfi.required_flags
        id_idx = tfi.id_field_index
        enum_field_indices = tfi.enum_field_indices
        enum_sets = tfi.enum_sets
        defaults = tfi.defaults
        has_type_field = tfi.type_field_index

        n_parts = len(parts)

        # LAX 'type' field heuristic
        if not self._is_strict and has_type_field >= 0 and n_parts == field_count - 1:
            tf = type_def.fields[has_type_field]
            if tf.default_value is not _MISSING and tf.default_value is not None:
                inferred = tf.default_value
            elif type_def.name:
                inferred = str(type_def.name).lower()
            else:
                inferred = str(type_def.alias).lower()
            parts = parts[:has_type_field] + [inferred] + parts[has_type_field:]
            n_parts = len(parts)

        # Strict count check
        if self._is_strict:
            if n_parts < field_count:
                for idx in range(n_parts, field_count):
                    if req_flags[idx] and defaults[idx] is _MISSING:
                        raise MaxiError(
                            f"Record '{alias}' missing required field '{type_def.fields[idx].name}'",
                            MaxiErrorCode.MissingRequiredFieldError,
                            line=line_number,
                            filename=self._filename,
                        )
            elif n_parts > field_count:
                raise MaxiError(
                    f"Record '{alias}' has {n_parts} values but type defines {field_count} fields",
                    MaxiErrorCode.SchemaMismatchError,
                    line=line_number,
                    filename=self._filename,
                )

        final_values: list[Any] = [None] * field_count
        is_strict = self._is_strict
        add_warning = self.result.add_warning
        fname = self._filename
        _nk = _detect_number_kind_fast
        _fk = RecordParser._detect_float_kind

        for idx in range(field_count):
            raw = parts[idx] if idx < n_parts else ""
            fk = field_kinds[idx]

            if raw == "":
                dv = defaults[idx]
                value = dv if dv is not _MISSING else None
            elif fk == _FK_INT:
                if _INT_RE.match(raw):
                    value = int(raw)
                elif is_strict:
                    raise MaxiError(
                        f"Type mismatch: field expects int, got '{raw}'",
                        MaxiErrorCode.TypeMismatchError,
                        line=line_number,
                        filename=fname,
                    )
                else:
                    nk = _nk(raw)
                    if nk in (2, 3):
                        add_warning(
                            f"Type coercion: value '{raw}' coerced to int, fractional part lost",
                            code=MaxiErrorCode.TypeMismatchError,
                            line=line_number,
                        )
                        value = int(float(raw.rstrip(".")))
                    else:
                        add_warning(
                            f"Type mismatch: field expects int, got '{raw}'",
                            code=MaxiErrorCode.TypeMismatchError,
                            line=line_number,
                        )
                        value = raw
            elif fk == _FK_BOOL:
                if raw == "true" or raw == "1":
                    value = True
                elif raw == "false" or raw == "0":
                    value = False
                elif is_strict:
                    raise MaxiError(
                        f"Type mismatch: field expects bool, got '{raw}'",
                        MaxiErrorCode.TypeMismatchError,
                        line=line_number,
                        filename=fname,
                    )
                else:
                    add_warning(
                        f"Type coercion: value '{raw}' is not a valid bool",
                        code=MaxiErrorCode.TypeMismatchError,
                        line=line_number,
                    )
                    value = raw
            elif fk == _FK_STR:
                value = raw
            elif fk == _FK_ENUM_STR:
                value = raw
            elif fk == _FK_ENUM_INT_LAX:
                if not is_strict and _INT_RE.match(raw):
                    value = int(raw)
                elif not is_strict:
                    nk = _nk(raw)
                    value = float(raw) if nk == 2 else raw
                else:
                    value = raw
            elif fk == _FK_DECIMAL:
                nk = _nk(raw)
                if nk == 3:
                    value = int(raw[:-1])
                elif nk in (1, 2):
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw
                elif is_strict:
                    raise MaxiError(
                        f"Type mismatch: field expects decimal, got '{raw}'",
                        MaxiErrorCode.TypeMismatchError,
                        line=line_number,
                        filename=fname,
                    )
                else:
                    value = raw
            elif fk == _FK_FLOAT:
                nk = _nk(raw)
                if nk in (1, 2, 3):
                    value = float(raw.rstrip("."))
                elif _fk(raw):
                    value = float(raw)
                elif is_strict:
                    raise MaxiError(
                        f"Type mismatch: field expects float, got '{raw}'",
                        MaxiErrorCode.TypeMismatchError,
                        line=line_number,
                        filename=fname,
                    )
                else:
                    value = raw
            elif fk == _FK_UNTYPED:
                if not is_strict:
                    if _INT_RE.match(raw):
                        value = int(raw)
                    else:
                        nk = _nk(raw)
                        if nk == 2:
                            value = float(raw)
                        elif nk == 3:
                            value = int(raw[:-1])
                        elif _fk(raw):
                            value = float(raw)
                        else:
                            value = raw
                else:
                    value = raw
            else:
                # _FK_COMPLEX — fall back to slow path
                return None

            if req_flags[idx] and value is None:
                error = MaxiError(
                    f"Required field '{type_def.fields[idx].name}' is null in record '{alias}'",
                    MaxiErrorCode.MissingRequiredFieldError,
                    line=line_number,
                    filename=fname,
                )
                if is_strict:
                    raise error
                add_warning(error.args[0], code=error.code, line=line_number)

            final_values[idx] = value

        # Enum validation — only for fields that have enums (use frozenset for O(1))
        if enum_field_indices:
            for idx in enum_field_indices:
                val = final_values[idx]
                if val is not None:
                    sv = str(val)
                    if sv not in enum_sets[idx]:
                        enum_vals = tfi.enum_values_list[idx]
                        msg = f"Value '{sv}' not in enum [{','.join(enum_vals)}] for field '{type_def.fields[idx].name}'"
                        if is_strict:
                            raise MaxiError(msg, MaxiErrorCode.ConstraintViolationError, line=line_number, filename=fname)
                        add_warning(msg, code=MaxiErrorCode.ConstraintViolationError, line=line_number)

        # Runtime constraint validation
        if tfi.has_runtime_constraints:
            validate_record_constraints(final_values, type_def, is_strict, self.result, line_number, fname)

        # Duplicate ID detection
        if 0 <= id_idx < len(final_values):
            id_val = final_values[id_idx]
            if id_val is not None:
                seen = self.seen_ids.get(alias)
                if seen is None:
                    seen = set()
                    self.seen_ids[alias] = seen
                id_key = str(id_val)
                if id_key in seen:
                    msg = f"Duplicate identifier '{id_val}' for type '{alias}'"
                    if is_strict:
                        raise MaxiError(msg, MaxiErrorCode.DuplicateIdentifierError, line=line_number, filename=fname)
                    add_warning(msg, code=MaxiErrorCode.DuplicateIdentifierError, line=line_number)
                seen.add(id_key)

        return MaxiRecord(alias=alias, values=final_values, line_number=line_number)

    # ── field value parsing ──────────────────────────────────────────────

    def _parse_field_values(
        self, values_str: str, type_def: MaxiTypeDef | None, line_number: int
    ) -> list[Any]:
        # Fast path check: no special chars
        is_simple = True
        for ch in values_str:
            if ch in ('"', "(", ")", "[", "]", "{", "}"):
                is_simple = False
                break

        if is_simple:
            fields = type_def.fields if type_def else None
            values: list[Any] = []
            parts = values_str.split("|")
            for fi, part in enumerate(parts):
                fd = fields[fi] if fields and fi < len(fields) else None
                values.append(self._parse_field_value(part.strip(), fd, line_number))
            return values

        value_strings = self._split_top_level(values_str, "|")
        values = []
        fields = type_def.fields if type_def else None
        for fi, vs in enumerate(value_strings):
            fd = fields[fi] if fields and fi < len(fields) else None
            values.append(self._parse_field_value(vs.strip(), fd, line_number))
        return values

    def _parse_field_value(
        self, value_str: str, field_def: MaxiFieldDef | dict[str, Any] | None, line_number: int
    ) -> Any:
        if value_str == "":
            if field_def is not None:
                dv = getattr(field_def, "default_value", _MISSING)
                if isinstance(field_def, dict):
                    dv = field_def.get("defaultValue", _MISSING)
                return dv if dv is not _MISSING else None
            return None

        if value_str == "~":
            return self._EXPLICIT_NULL

        c0 = value_str[0]
        c_last = value_str[-1]

        if c0 == "[":
            if c_last != "]":
                raise MaxiError(
                    "Malformed array: unmatched opening bracket",
                    MaxiErrorCode.ArraySyntaxError,
                    line=line_number,
                    filename=self._filename,
                )
            return self._parse_array(value_str, field_def, line_number)
        if c0 == "{" and c_last == "}":
            return self._parse_map(value_str, field_def, line_number)
        if c0 == "(" and c_last == ")":
            return self._parse_inline_object(value_str, field_def, line_number)
        if c0 == '"' and c_last == '"':
            return self._parse_quoted_string(value_str)

        type_expr = _get_type_expr(field_def) or "str"

        # Extract base type from expressions like int(>=0), str(=10), etc.
        _base_type_m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", type_expr)
        base_type = _base_type_m.group(1) if _base_type_m else type_expr

        # int
        if base_type == "int":
            nk = self._detect_number_kind(value_str)
            if nk == 1:
                return int(value_str)
            if self._is_strict:
                raise MaxiError(
                    f"Type mismatch: field expects int, got '{value_str}'",
                    MaxiErrorCode.TypeMismatchError,
                    line=line_number,
                    filename=self._filename,
                )
            if nk in (2, 3):
                self.result.add_warning(
                    f"Type coercion: value '{value_str}' coerced to int, fractional part lost",
                    code=MaxiErrorCode.TypeMismatchError,
                    line=line_number,
                )
                return int(float(value_str.rstrip(".")))
            self.result.add_warning(
                f"Type mismatch: field expects int, got '{value_str}'",
                code=MaxiErrorCode.TypeMismatchError,
                line=line_number,
            )
            return value_str

        # bool
        if base_type == "bool":
            if value_str in ("1", "true"):
                return True
            if value_str in ("0", "false"):
                return False
            if self._is_strict:
                raise MaxiError(
                    f"Type mismatch: field expects bool, got '{value_str}'",
                    MaxiErrorCode.TypeMismatchError,
                    line=line_number,
                    filename=self._filename,
                )
            self.result.add_warning(
                f"Type coercion: value '{value_str}' is not a valid bool",
                code=MaxiErrorCode.TypeMismatchError,
                line=line_number,
            )
            return value_str

        # Explicit str type
        if base_type == "str" and _get_type_expr_raw(field_def) is not None:
            return value_str

        # enum with string base
        if type_expr.startswith("enum"):
            m = re.match(r"^enum<(\w+)>", type_expr)
            if not m or m.group(1) == "str":
                return value_str
            # enum<int> etc. – fall through

        # bytes with base64 annotation (lax auto-pad)
        annotation = _get_annotation(field_def)
        if not self._is_strict and type_expr == "bytes" and annotation == "base64":
            s = value_str
            if self._looks_like_base64(s):
                mod = len(s) % 4
                if mod != 0:
                    return s + ("===" if mod == 1 else "==" if mod == 2 else "=")
            return s

        # float
        if base_type == "float":
            nk = self._detect_number_kind(value_str)
            fk = self._detect_float_kind(value_str)
            if fk or nk in (1, 2, 3):
                return float(value_str.rstrip("."))
            if self._is_strict:
                raise MaxiError(
                    f"Type mismatch: field expects float, got '{value_str}'",
                    MaxiErrorCode.TypeMismatchError,
                    line=line_number,
                    filename=self._filename,
                )
            self.result.add_warning(
                f"Type coercion: value '{value_str}' is not a valid float",
                code=MaxiErrorCode.TypeMismatchError,
                line=line_number,
            )
            return value_str

        # decimal → Python Decimal
        if base_type == "decimal":
            nk = self._detect_number_kind(value_str)
            if nk == 3:
                return int(value_str[:-1])
            if nk in (1, 2):
                try:
                    return float(value_str)
                except (ValueError, InvalidOperation):
                    pass
            if self._is_strict:
                raise MaxiError(
                    f"Type mismatch: field expects decimal, got '{value_str}'",
                    MaxiErrorCode.TypeMismatchError,
                    line=line_number,
                    filename=self._filename,
                )
            self.result.add_warning(
                f"Type coercion: value '{value_str}' is not a valid decimal",
                code=MaxiErrorCode.TypeMismatchError,
                line=line_number,
            )
            return value_str

        # Untyped / str-with-annotation → lax numeric coercion
        if not self._is_strict:
            fk = self._detect_float_kind(value_str)
            if fk:
                return float(value_str)
            nk = self._detect_number_kind(value_str)
            if nk == 1:
                return int(value_str)
            if nk == 2:
                return float(value_str)
            if nk == 3:
                return int(value_str[:-1])

        return value_str

    # ── array ────────────────────────────────────────────────────────────

    def _parse_array(self, array_str: str, field_def: Any, line_number: int) -> list[Any]:
        content = array_str[1:-1].strip()
        if not content:
            return []

        elem_type = self._get_array_element_type(_get_type_expr(field_def))
        elem_fd: dict[str, Any] | None = {"typeExpr": elem_type} if elem_type else None

        elements: list[Any] = []
        current: list[str] = []
        depth = 0
        in_string = False
        escape_next = False

        for ch in content:
            if escape_next:
                current.append(ch)
                escape_next = False
                continue
            if ch == "\\" and in_string:
                current.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                current.append(ch)
                continue
            if not in_string:
                if ch in ("(", "[", "{"):
                    depth += 1
                elif ch in (")", "]", "}"):
                    depth -= 1
                if ch == "," and depth == 0:
                    elements.append(
                        self._parse_field_value("".join(current).strip(), elem_fd, line_number)
                    )
                    current = []
                    continue
            current.append(ch)

        remainder = "".join(current).strip()
        if remainder:
            elements.append(self._parse_field_value(remainder, elem_fd, line_number))
        return elements

    # ── map ──────────────────────────────────────────────────────────────

    def _parse_map(self, map_str: str, field_def: Any, line_number: int) -> dict[str, Any]:
        content = map_str[1:-1].strip()
        if not content:
            return {}

        _raw_type_expr = _get_type_expr(field_def)
        map_val_type = self._get_map_value_type(_raw_type_expr)
        if map_val_type is None and _raw_type_expr is not None:
            # bare 'map' or 'map' without generics defaults to str values
            map_val_type = "str"
        val_fd: dict[str, Any] | None = {"typeExpr": map_val_type} if map_val_type else None

        result: dict[str, Any] = {}
        current: list[str] = []
        depth = 0
        in_string = False
        escape_next = False

        for ch in content:
            if escape_next:
                current.append(ch)
                escape_next = False
                continue
            if ch == "\\" and in_string:
                current.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                current.append(ch)
                continue
            if not in_string:
                if ch in ("(", "[", "{"):
                    depth += 1
                elif ch in (")", "]", "}"):
                    depth -= 1
                if ch == "," and depth == 0:
                    self._parse_map_entry("".join(current).strip(), result, line_number, val_fd)
                    current = []
                    continue
            current.append(ch)

        remainder = "".join(current).strip()
        if remainder:
            self._parse_map_entry(remainder, result, line_number, val_fd)
        return result

    def _parse_map_entry(
        self,
        entry_str: str,
        target: dict[str, Any],
        line_number: int,
        value_fd: dict[str, Any] | None = None,
    ) -> None:
        # Find colon at top level
        colon_idx = -1
        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(entry_str):
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch in ("(", "[", "{"):
                depth += 1
            elif ch in (")", "]", "}"):
                depth = max(0, depth - 1)
            if ch == ":" and depth == 0:
                colon_idx = i
                break

        if colon_idx == -1:
            raise MaxiError(
                f"Invalid map entry format: {entry_str}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self._filename,
            )

        key_str = entry_str[:colon_idx].strip()
        val_str = entry_str[colon_idx + 1 :].strip()
        key = self._parse_field_value(key_str, {"typeExpr": "str"}, line_number)
        value = self._parse_field_value(val_str, value_fd, line_number)
        if value_fd:
            self._validate_inline_type_constraints(value, value_fd.get("typeExpr"), "map value", line_number)
        target[str(key)] = value

    # ── inline object ────────────────────────────────────────────────────

    def _parse_inline_object(self, obj_str: str, field_def: Any, line_number: int) -> Any:
        inner = obj_str[1:-1]
        type_alias = self._get_inline_object_type_alias(_get_type_expr(field_def))
        if not type_alias:
            return {"values": self._parse_field_values(inner, None, line_number)}

        td = self.result.schema.get_type(type_alias)
        if not td:
            if self._is_strict:
                raise MaxiError(
                    f"Unknown type alias '{type_alias}' for inline object",
                    MaxiErrorCode.UnknownTypeError,
                    line=line_number,
                    filename=self._filename,
                )
            self.result.add_warning(
                f"Unknown type alias '{type_alias}' for inline object",
                code=MaxiErrorCode.UnknownTypeError,
                line=line_number,
            )
            return {"values": self._parse_field_values(inner, None, line_number)}

        values = self._parse_field_values(inner, td, line_number)
        obj: dict[str, Any] = {}
        for i, field in enumerate(td.fields):
            v = values[i] if i < len(values) else None
            if v is self._EXPLICIT_NULL:
                v = None
            elif v is None or v == "":
                v = field.default_value if field.default_value is not _MISSING else None
            obj[field.name] = v
        return obj

    def _validate_inline_type_constraints(self, value: Any, type_expr: str | None, field_name: str, line_number: int) -> None:
        """Validate inline constraints like int(>=0) embedded in type expressions."""
        if not type_expr:
            return
        m = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\((.+)\)\s*$", type_expr)
        if not m:
            return
        constraint_str = m.group(1)
        parts = [p.strip() for p in constraint_str.split(",") if p.strip()]
        for part in parts:
            cmp = re.match(r"^(>=|>|<=|<)\s*(.+)$", part)
            if not cmp:
                continue
            operator, limit_str = cmp.group(1), cmp.group(2)
            try:
                limit = float(limit_str)
            except ValueError:
                continue
            if isinstance(value, str):
                actual = len(value)
            elif isinstance(value, (int, float)):
                actual = value
            else:
                continue
            violated = False
            if operator == ">=" and actual < limit:
                violated = True
            elif operator == ">" and actual <= limit:
                violated = True
            elif operator == "<=" and actual > limit:
                violated = True
            elif operator == "<" and actual >= limit:
                violated = True
            if violated:
                msg = f"{field_name}: value {actual} violates constraint {operator}{limit}"
                if self._is_strict:
                    raise MaxiError(msg, MaxiErrorCode.ConstraintViolationError, line=line_number, filename=self._filename)
                self.result.add_warning(msg, code=MaxiErrorCode.ConstraintViolationError, line=line_number)

    def _get_inline_object_type_alias(self, type_expr: str | None) -> str | None:
        if not type_expr:
            return None
        t = type_expr.strip()
        m = re.match(r"^(.+)\[\]\s*$", t)
        base = m.group(1).strip() if m else t

        if re.match(r"^map\s*<", base):
            mvt = self._get_map_value_type(base)
            resolved = mvt.strip() if mvt else None
            resolver = getattr(self.result.schema, "resolve_type_alias", None)
            return resolver(resolved) if resolver else resolved

        if base in self._PRIMITIVES:
            return None
        resolver = getattr(self.result.schema, "resolve_type_alias", None)
        return resolver(base) if resolver else base

    # ── type helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_array_element_type(type_expr: str | None) -> str | None:
        if not type_expr:
            return None
        m = re.match(r"^(.+)\[\]\s*$", type_expr.strip())
        return m.group(1).strip() if m else None

    @staticmethod
    def _get_map_value_type(type_expr: str | None) -> str | None:
        if not type_expr:
            return None
        t = type_expr.strip()
        if t == "map":
            return None
        m = re.match(r"^map\s*<\s*(.+)\s*>\s*$", t)
        if not m:
            return None

        inside = m.group(1)
        depth = 0
        in_string = False
        parts: list[str] = []
        cur: list[str] = []

        for i, ch in enumerate(inside):
            if in_string:
                if ch == '"' and (i == 0 or inside[i - 1] != "\\"):
                    in_string = False
                cur.append(ch)
                continue
            if ch == '"':
                in_string = True
                cur.append(ch)
                continue
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
            cur.append(ch)

        remainder = "".join(cur).strip()
        if remainder:
            parts.append(remainder)

        if len(parts) == 1:
            return parts[0] or None
        return parts[-1] or None

    # ── string parsing ───────────────────────────────────────────────────

    @staticmethod
    def _parse_quoted_string(s: str) -> str:
        inner = s[1:-1]
        return (
            inner.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )

    # ── splitting utility ────────────────────────────────────────────────

    @staticmethod
    def _split_top_level(s: str, delim: str) -> list[str]:
        parts: list[str] = []
        part_start = 0
        paren = bracket = brace = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(s):
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if ch == "\\":
                    escape_next = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket -= 1
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
            if ch == delim and paren == 0 and bracket == 0 and brace == 0:
                parts.append(s[part_start:i])
                part_start = i + 1

        parts.append(s[part_start:])
        return parts

    # ── numeric detection ────────────────────────────────────────────────

    @staticmethod
    def _detect_number_kind(s: str) -> int:
        """0=not numeric, 1=int, 2=decimal, 3=trailing-dot."""
        return _detect_number_kind_fast(s)

    @staticmethod
    def _detect_float_kind(s: str) -> bool:
        """True if s looks like scientific notation (e.g. 1e10, 3.14e-2)."""
        n = len(s)
        if n == 0:
            return False
        i = 0
        if s[0] == "-":
            if n == 1:
                return False
            i = 1
        if not s[i].isdigit():
            return False
        while i < n and s[i].isdigit():
            i += 1
        if i >= n:
            return False
        if s[i] == ".":
            i += 1
            while i < n and s[i].isdigit():
                i += 1
        if i >= n:
            return False
        if s[i] not in ("e", "E"):
            return False
        i += 1
        if i >= n:
            return False
        if s[i] in ("+", "-"):
            i += 1
            if i >= n:
                return False
        if not s[i].isdigit():
            return False
        while i < n:
            if not s[i].isdigit():
                return False
            i += 1
        return True

    @staticmethod
    def _looks_like_base64(s: str) -> bool:
        if not s:
            return False
        pad = 0
        for ch in s:
            if ch == "=":
                pad += 1
                continue
            if pad > 0:
                return False
            if not (ch.isalnum() or ch in ("+", "/")):
                return False
        return pad <= 2


# ── Pre-computed field info for fast-path parsing ────────────────────────

# Field kind constants for fast dispatch
_FK_STR = 0
_FK_INT = 1
_FK_BOOL = 2
_FK_ENUM_STR = 3
_FK_ENUM_INT_LAX = 4
_FK_DECIMAL = 5
_FK_FLOAT = 6
_FK_UNTYPED = 7
_FK_COMPLEX = 8  # needs slow path


class _TypeFieldInfo:
    """Pre-computed per-type metadata to avoid repeated attribute lookups."""

    __slots__ = (
        "type_exprs", "required_flags", "id_field_index",
        "enum_field_indices", "enum_values_list", "defaults",
        "has_runtime_constraints", "can_fast_parse", "type_field_index",
        "field_kinds", "enum_sets",
    )

    _FAST_TYPES = frozenset({"int", "str", "bool", "decimal", "float", None})

    def __init__(self, td: MaxiTypeDef) -> None:
        fields = td.fields
        n = len(fields)
        self.type_exprs: list[str | None] = [f.type_expr for f in fields]
        self.required_flags: list[bool] = td.get_required_flags()
        self.id_field_index: int = td.get_id_field_index()
        self.defaults: list[Any] = [f.default_value for f in fields]
        self.has_runtime_constraints: bool = td.has_runtime_constraints

        # Enum info
        self.enum_values_list: list[list[str] | None] = [td.get_enum_values(i) for i in range(n)]
        self.enum_field_indices: list[int] = [i for i in range(n) if self.enum_values_list[i] is not None]

        # Pre-compute enum sets for O(1) lookup
        self.enum_sets: list[frozenset[str] | None] = [
            frozenset(ev) if ev else None for ev in self.enum_values_list
        ]

        # Type field index for LAX heuristic
        self.type_field_index: int = next((i for i, f in enumerate(fields) if f.name == "type"), -1)

        # Compute field_kinds for integer-based dispatch
        self.field_kinds: list[int] = []
        for te in self.type_exprs:
            if te == "int":
                self.field_kinds.append(_FK_INT)
            elif te == "bool":
                self.field_kinds.append(_FK_BOOL)
            elif te == "str":
                self.field_kinds.append(_FK_STR)
            elif te == "decimal":
                self.field_kinds.append(_FK_DECIMAL)
            elif te == "float":
                self.field_kinds.append(_FK_FLOAT)
            elif te is not None and te.startswith("enum"):
                if "<int>" in te:
                    self.field_kinds.append(_FK_ENUM_INT_LAX)
                else:
                    self.field_kinds.append(_FK_ENUM_STR)
            elif te is None:
                self.field_kinds.append(_FK_UNTYPED)
            else:
                self.field_kinds.append(_FK_COMPLEX)

        # Determine if fast-path is possible
        self.can_fast_parse = _FK_COMPLEX not in self.field_kinds


# ── Module-level fast number detection ───────────────────────────────────

def _detect_number_kind_fast(s: str) -> int:
    """0=not numeric, 1=int, 2=decimal, 3=trailing-dot."""
    if not s:
        return 0
    if _INT_RE.match(s):
        return 1
    if _DECIMAL_RE.match(s):
        return 2
    if _TRAILING_DOT_RE.match(s):
        return 3
    return 0


# ── helpers to extract field_def attributes from MaxiFieldDef or dict ────

def _get_type_expr(field_def: Any) -> str | None:
    if field_def is None:
        return None
    if isinstance(field_def, dict):
        return field_def.get("typeExpr")
    return getattr(field_def, "type_expr", None)


def _get_type_expr_raw(field_def: Any) -> str | None:
    """Return typeExpr only if explicitly set on field_def (not a default)."""
    if field_def is None:
        return None
    if isinstance(field_def, dict):
        return field_def.get("typeExpr")
    te = getattr(field_def, "type_expr", None)
    return te


def _get_annotation(field_def: Any) -> str | None:
    if field_def is None:
        return None
    if isinstance(field_def, dict):
        return field_def.get("annotation")
    return getattr(field_def, "annotation", None)

