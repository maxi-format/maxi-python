"""
Schema phase parser – directives, type definitions, imports.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from maxi.core.errors import MaxiError, MaxiErrorCode
from maxi.core.types import MaxiFieldDef, MaxiTypeDef, ParsedConstraint
from maxi.internal.constraint_validator import validate_schema_constraints

if TYPE_CHECKING:
    from maxi.core.types import MaxiParseResult


class SchemaParser:
    """Parse the schema section of a MAXI document."""

    _PRIMITIVE_TYPES = frozenset({"str", "int", "decimal", "float", "bool", "bytes", "map"})

    def __init__(
        self,
        schema_text: str,
        result: MaxiParseResult,
        options: dict[str, Any],
    ) -> None:
        self.schema_text = schema_text
        self.result = result
        self.options = options
        self.loading_stack: set[str] = set()
        self.local_aliases: set[str] = set()
        self._is_imported: bool = False

    async def parse(self) -> None:
        if not self.schema_text.strip():
            return

        lines = self.schema_text.split("\n")
        lines = [l.rstrip("\r") for l in lines]
        line_number = 1

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line or line.startswith("#"):
                i += 1
                line_number += 1
                continue

            if line.startswith("@"):
                await self._parse_directive(line, line_number)
                i += 1
                line_number += 1
                continue

            td_result = self._parse_type_definition(lines, i, line_number)
            if td_result is not None:
                i = td_result["next_index"] + 1
                line_number = td_result["next_line"] + 1
            else:
                i += 1
                line_number += 1

        self._resolve_inheritance()
        validate_schema_constraints(self.result.schema, self.options.get("filename"))
        self._validate_default_values()
        self._build_name_index()

        if not self._is_imported:
            self._validate_field_type_references()

    async def _parse_directive(self, line: str, line_number: int) -> None:
        m = re.match(r"^@([a-zA-Z_][a-zA-Z0-9_-]*):(.+)$", line)
        if not m:
            raise MaxiError(
                f"Invalid directive syntax: {line}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        name = m.group(1)
        value = m.group(2).strip()

        if name == "version":
            self._parse_version_directive(value, line_number)
        elif name == "schema":
            await self._parse_schema_directive(value, line_number)
        else:
            self.result.add_warning(
                f"Unknown directive '@{name}' ignored",
                code=MaxiErrorCode.UnknownDirectiveError,
                line=line_number,
            )

    def _parse_version_directive(self, value: str, line_number: int) -> None:
        if not re.match(r"^\d+\.\d+\.\d+$", value):
            raise MaxiError(
                f"Invalid version format: {value}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )
        if value != "1.0.0":
            raise MaxiError(
                f"Unsupported version: {value}. Parser supports v1.0.0",
                MaxiErrorCode.UnsupportedVersionError,
                line=line_number,
                filename=self.options.get("filename"),
            )
        self.result.schema.version = value


    async def _parse_schema_directive(self, path_or_url: str, line_number: int) -> None:
        if path_or_url in self.loading_stack:
            return
        self.result.schema.imports.append(path_or_url)
        await self._load_external_schema(path_or_url, line_number)

    def _parse_type_definition(
        self, lines: list[str], start_index: int, start_line: int
    ) -> dict[str, int] | None:
        trimmed = lines[start_index].strip()
        if not trimmed or trimmed.startswith("#") or trimmed.startswith("@"):
            return None

        looks_alias_paren = bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*\(", trimmed))
        looks_explicit = bool(
            re.match(
                r"^[A-Za-z_][A-Za-z0-9_-]*\s*:\s*[A-Za-z_][A-Za-z0-9_-]*\s*(<[^>]+>)?\s*\(",
                trimmed,
            )
        )
        looks_inherit = bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*<[^>]+>\s*\(", trimmed))

        if not looks_explicit and not looks_inherit and looks_alias_paren:
            open_idx = trimmed.index("(")
            after = trimmed[open_idx + 1 :].lstrip()
            if re.match(r"^(\d|-\d|~)", after):
                return None
        elif not looks_explicit and not looks_inherit:
            return None

        full_def = ""
        i = start_index
        line_num = start_line

        in_string = False
        escape_next = False
        saw_open = False
        paren_depth = 0

        while i < len(lines):
            current = lines[i]
            full_def += current + "\n"

            for ch in current:
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
                if ch == "(":
                    saw_open = True
                    paren_depth += 1
                    continue
                if ch == ")":
                    if not saw_open:
                        raise MaxiError(
                            "Unmatched closing parenthesis in type definition",
                            MaxiErrorCode.InvalidSyntaxError,
                            line=line_num,
                            filename=self.options.get("filename"),
                        )
                    paren_depth -= 1
                    if paren_depth < 0:
                        raise MaxiError(
                            "Unmatched closing parenthesis in type definition",
                            MaxiErrorCode.InvalidSyntaxError,
                            line=line_num,
                            filename=self.options.get("filename"),
                        )
                    if paren_depth == 0:
                        break

            if saw_open and paren_depth == 0:
                break
            i += 1
            line_num += 1

        if not saw_open:
            return None

        if paren_depth != 0:
            raise MaxiError(
                "Unclosed parenthesis in type definition",
                MaxiErrorCode.InvalidSyntaxError,
                line=start_line,
                filename=self.options.get("filename"),
            )

        self._parse_complete_type_definition(full_def, start_line)
        return {"next_index": i, "next_line": line_num}

    def _parse_complete_type_definition(self, definition: str, line_number: int) -> None:
        trimmed = definition.strip()

        open_idx = trimmed.find("(")
        if open_idx == -1:
            raise MaxiError(
                f"Invalid type definition syntax: {trimmed}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        close_idx = self._find_matching_paren(trimmed, open_idx)
        if close_idx == -1:
            raise MaxiError(
                f"Invalid type definition syntax: {trimmed}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        tail = trimmed[close_idx + 1 :].strip()
        if tail:
            raise MaxiError(
                f"Invalid type definition syntax: {trimmed}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        header = trimmed[:open_idx].strip()
        fields_str = trimmed[open_idx + 1 : close_idx].strip()

        header_m = re.match(
            r"^([A-Za-z_][A-Za-z0-9_-]*)(?::([A-Za-z_][A-Za-z0-9_-]*))?(?:<\s*([^>]+?)\s*>)?\s*$",
            header,
        )
        if not header_m:
            raise MaxiError(
                f"Invalid type definition header: {header}",
                MaxiErrorCode.InvalidSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        alias = header_m.group(1)
        type_name = header_m.group(2) or None
        parents_str = header_m.group(3)

        if type_name and not type_name[0].isalpha():
            raise MaxiError(
                f"Invalid type name '{type_name}': type names must start with a letter [a-zA-Z]",
                MaxiErrorCode.UnknownTypeError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        if alias in self.local_aliases:
            raise MaxiError(
                f"Duplicate type alias '{alias}'",
                MaxiErrorCode.DuplicateTypeError,
                line=line_number,
                filename=self.options.get("filename"),
            )
        self.local_aliases.add(alias)

        parents = (
            [p.strip() for p in parents_str.split(",") if p.strip()]
            if parents_str
            else []
        )

        type_def = MaxiTypeDef(alias=alias, name=type_name, parents=parents)

        if fields_str:
            for f in self._parse_field_list(fields_str, line_number):
                type_def.add_field(f)

        self.result.schema.add_type(type_def)

    @staticmethod
    def _find_matching_paren(s: str, open_idx: int) -> int:
        if open_idx < 0 or s[open_idx] != "(":
            return -1
        depth = 0
        in_string = False
        escape_next = False
        for i in range(open_idx, len(s)):
            ch = s[i]
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
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _parse_field_list(self, fields_str: str, line_number: int) -> list[MaxiFieldDef]:
        normalized = re.sub(r"\s+", " ", fields_str.replace("\r", " ").replace("\n", " ").replace("\t", " ")).strip()
        parts = [p.strip() for p in self._split_top_level(normalized, "|") if p.strip()]
        return [self._parse_field(p, line_number) for p in parts]

    def _parse_field(self, field_str: str, line_number: int) -> MaxiFieldDef:
        remaining = field_str.strip()
        constraints: list[ParsedConstraint] = []
        element_constraints: list[ParsedConstraint] = []
        default_value: Any = None
        has_default = False

        colon_idx = self._find_top_level_char(remaining, ":")
        name_part = remaining
        rest_part = ""

        if colon_idx != -1:
            name_part = remaining[:colon_idx].strip()
            rest_part = remaining[colon_idx + 1 :].strip()

        if rest_part:
            trailing = self._extract_trailing_group(rest_part, "(", ")")
            if trailing:
                constraints = self._parse_constraints(trailing["inner"], line_number)
                rest_part = trailing["before"].strip()

                if re.search(r"\[\]\s*$", rest_part):
                    without_brackets = re.sub(r"\[\]\s*$", "", rest_part).strip()
                    inner_trailing = self._extract_trailing_group(without_brackets, "(", ")")
                    if inner_trailing:
                        element_constraints = self._parse_constraints(inner_trailing["inner"], line_number)
                        rest_part = inner_trailing["before"].strip() + "[]"

        if not constraints:
            trailing = self._extract_trailing_group(name_part, "(", ")")
            if trailing:
                constraints = self._parse_constraints(trailing["inner"], line_number)
                name_part = trailing["before"].strip()

        eq_idx = self._find_top_level_char(name_part, "=")
        if eq_idx != -1:
            default_value = name_part[eq_idx + 1 :].strip()
            has_default = True
            name_part = name_part[:eq_idx].strip()
            if not constraints:
                trailing2 = self._extract_trailing_group(name_part, "(", ")")
                if trailing2:
                    constraints = self._parse_constraints(trailing2["inner"], line_number)
                    name_part = trailing2["before"].strip()
        elif rest_part:
            eq_idx = self._find_top_level_char(rest_part, "=")
            if eq_idx != -1:
                default_value = rest_part[eq_idx + 1 :].strip()
                has_default = True
                rest_part = rest_part[:eq_idx].strip()

        if has_default and default_value is not None:
            dv = default_value
            if isinstance(dv, str) and dv.startswith('"') and dv.endswith('"'):
                default_value = self._unescape_string(dv[1:-1])

        type_expr: str | None = None
        annotation: str | None = None

        if rest_part:
            at_idx = self._find_top_level_char(rest_part, "@")
            if at_idx != -1:
                type_expr = rest_part[:at_idx].strip() or None
                annotation = rest_part[at_idx + 1 :].strip() or None
            else:
                type_expr = rest_part.strip() or None

        from maxi.core.types import _MISSING

        return MaxiFieldDef(
            name=name_part,
            type_expr=type_expr,
            annotation=annotation,
            constraints=constraints if constraints else None,
            element_constraints=element_constraints if element_constraints else None,
            default_value=default_value if has_default else _MISSING,
        )

    @staticmethod
    def _split_top_level(s: str, delim: str) -> list[str]:
        """Split *s* on *delim* only at top-level nesting."""
        out: list[str] = []
        cur: list[str] = []

        in_string = False
        escape_next = False
        paren = bracket = brace = 0

        for ch in s:
            if escape_next:
                cur.append(ch)
                escape_next = False
                continue
            if in_string:
                if ch == "\\":
                    cur.append(ch)
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = False
                cur.append(ch)
                continue
            if ch == '"':
                in_string = True
                cur.append(ch)
                continue

            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(0, bracket - 1)
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(0, brace - 1)

            if ch == delim and paren == 0 and bracket == 0 and brace == 0:
                out.append("".join(cur))
                cur = []
                continue

            cur.append(ch)

        out.append("".join(cur))
        return out

    @staticmethod
    def _find_top_level_char(s: str, ch: str) -> int:
        """Return index of *ch* at top-level nesting, or ``-1``."""
        in_string = False
        escape_next = False
        paren = bracket = brace = 0

        for i, c in enumerate(s):
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if c == "\\":
                    escape_next = True
                    continue
                if c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
                continue
            if c == "(":
                paren += 1
            elif c == ")":
                paren = max(0, paren - 1)
            elif c == "[":
                bracket += 1
            elif c == "]":
                bracket = max(0, bracket - 1)
            elif c == "{":
                brace += 1
            elif c == "}":
                brace = max(0, brace - 1)
            if c == ch and paren == 0 and bracket == 0 and brace == 0:
                return i
        return -1

    @staticmethod
    def _extract_trailing_group(
        s: str, open_ch: str, close_ch: str
    ) -> dict[str, str] | None:
        """If *s* ends with a balanced group, return ``{before, inner}``."""
        trimmed = s.rstrip()
        if not trimmed.endswith(close_ch):
            return None
        close_idx = len(trimmed) - 1

        in_string = False
        depth = 1
        start_idx = -1

        for i in range(close_idx - 1, -1, -1):
            ch = trimmed[i]
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == close_ch:
                depth += 1
            elif ch == open_ch:
                depth -= 1
                if depth == 0:
                    start_idx = i
                    break

        if start_idx == -1 or depth != 0:
            return None
        return {
            "before": trimmed[:start_idx],
            "inner": trimmed[start_idx + 1 : close_idx],
        }

    def _parse_constraints(self, constraint_str: str, line_number: int) -> list[ParsedConstraint]:
        constraints: list[ParsedConstraint] = []
        parts = [p.strip() for p in self._split_constraint_parts(constraint_str) if p.strip()]

        for part in parts:
            if part == "!":
                constraints.append(ParsedConstraint(type="required"))
                continue
            if part == "id":
                constraints.append(ParsedConstraint(type="id"))
                continue

            m = re.match(r"^=(\d+)$", part)
            if m:
                constraints.append(ParsedConstraint(type="exact-length", value=int(m.group(1))))
                continue

            m = re.match(r"^(>=|>|<=|<|=)\s*(.+)$", part)
            if m:
                op = m.group(1)
                val_str = m.group(2).strip()
                try:
                    num_val: int | float = int(val_str)
                except ValueError:
                    try:
                        num_val = float(val_str)
                    except ValueError:
                        num_val = val_str
                c = ParsedConstraint(type="comparison", value=num_val if isinstance(num_val, (int, float)) else val_str)
                c.operator = op
                constraints.append(c)
                continue

            if part.startswith("pattern:"):
                pattern = part[len("pattern:") :].strip()
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise MaxiError(
                        f"Invalid regex pattern: {pattern}",
                        MaxiErrorCode.ConstraintSyntaxError,
                        line=line_number,
                        filename=self.options.get("filename"),
                        cause=exc,
                    )
                constraints.append(ParsedConstraint(type="pattern", value=pattern))
                continue

            if part.startswith("mime:"):
                mime_spec = part[len("mime:") :].strip()
                mime_types = self._parse_mime_spec(mime_spec)
                constraints.append(ParsedConstraint(type="mime", value=mime_types))
                continue

            if re.match(r"^(\d+:)?(\d+)?\.(\d+(?::\d+)?)?$", part):
                constraints.append(self._parse_decimal_precision(part))
                continue

            raise MaxiError(
                f"Unknown constraint: {part}",
                MaxiErrorCode.ConstraintSyntaxError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        return constraints

    @staticmethod
    def _split_constraint_parts(s: str) -> list[str]:
        """Split constraint string on ``,`` respecting nesting."""
        parts: list[str] = []
        current: list[str] = []
        in_string = False
        escape_next = False
        bracket = paren = brace = 0

        for ch in s:
            if escape_next:
                current.append(ch)
                escape_next = False
                continue
            if in_string:
                if ch == "\\":
                    current.append(ch)
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = False
                current.append(ch)
                continue
            if ch == '"':
                in_string = True
                current.append(ch)
                continue
            if ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(0, bracket - 1)
            elif ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(0, brace - 1)
            if ch == "," and bracket == 0 and paren == 0 and brace == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(ch)

        parts.append("".join(current))
        return parts

    def _parse_mime_spec(self, mime_spec: str) -> list[str]:
        s = mime_spec.strip()
        if not s:
            return []
        if not s.startswith("["):
            if s.startswith('"') and s.endswith('"'):
                single = self._unescape_string(s[1:-1])
            else:
                single = s
            return [single.strip()] if single.strip() else []
        if not s.endswith("]"):
            raise MaxiError(
                f"Invalid mime constraint value: {mime_spec}",
                MaxiErrorCode.ConstraintSyntaxError,
            )
        content = s[1:-1].strip()
        if not content:
            return []

        items: list[str] = []
        cur: list[str] = []
        in_str = False
        esc = False
        for ch in content:
            if esc:
                cur.append(ch)
                esc = False
                continue
            if in_str:
                if ch == "\\":
                    cur.append(ch)
                    esc = True
                    continue
                if ch == '"':
                    in_str = False
                cur.append(ch)
                continue
            if ch == '"':
                in_str = True
                cur.append(ch)
                continue
            if ch == ",":
                item = "".join(cur).strip()
                if item:
                    items.append(item)
                cur = []
                continue
            cur.append(ch)
        last = "".join(cur).strip()
        if last:
            items.append(last)

        result: list[str] = []
        for t in items:
            tt = t.strip()
            if tt.startswith('"') and tt.endswith('"'):
                tt = self._unescape_string(tt[1:-1]).strip()
            if tt:
                result.append(tt)
        return result

    @staticmethod
    def _parse_decimal_precision(raw: str) -> ParsedConstraint:
        dot_idx = raw.index(".")
        int_part = raw[:dot_idx]
        frac_part = raw[dot_idx + 1 :]

        int_min = int_max = frac_min = frac_max = None

        if int_part:
            if ":" in int_part:
                a, b = int_part.split(":", 1)
                int_min = int(a) if a else None
                int_max = int(b) if b else None
            else:
                int_max = int(int_part)

        if frac_part:
            if ":" in frac_part:
                a, b = frac_part.split(":", 1)
                frac_min = int(a) if a else None
                frac_max = int(b) if b else None
            else:
                frac_max = int(frac_part)

        c = ParsedConstraint(type="decimal-precision", value=raw)
        c.int_min = int_min
        c.int_max = int_max
        c.frac_min = frac_min
        c.frac_max = frac_max
        return c

    @staticmethod
    def _unescape_string(s: str) -> str:
        return (
            s.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )

    def _extract_referenced_type(self, type_expr: str | None) -> str | None:
        if not type_expr:
            return None
        t = type_expr.strip()
        if t.startswith("enum"):
            return None

        m = re.match(r"^map\s*<\s*(.+)\s*>\s*$", t)
        if m:
            inside = m.group(1)
            depth = 0
            last_comma = -1
            for i, ch in enumerate(inside):
                if ch == "<":
                    depth += 1
                elif ch == ">":
                    depth -= 1
                elif ch == "," and depth == 0:
                    last_comma = i
            value_type = inside[last_comma + 1 :].strip() if last_comma >= 0 else inside.strip()
            return self._extract_referenced_type(value_type)
        if t == "map":
            return None

        t = re.sub(r"\([^)]*\)\s*$", "", t).strip()

        while t.endswith("[]"):
            t = t[:-2].strip()
            t = re.sub(r"\([^)]*\)\s*$", "", t).strip()

        if not t:
            return None
        if t in self._PRIMITIVE_TYPES:
            return None
        return t

    def _validate_field_type_references(self) -> None:
        schema = self.result.schema
        for alias, type_def in schema.types.items():
            for field in type_def.fields:
                ref = self._extract_referenced_type(field.type_expr)
                if ref and not schema.has_type(ref):
                    resolved = schema.resolve_type_alias(ref) if hasattr(schema, "resolve_type_alias") else None
                    if not resolved:
                        raise MaxiError(
                            f"Field '{field.name}' in type '{alias}' references unknown type '{ref}'",
                            MaxiErrorCode.UnknownTypeError,
                            filename=self.options.get("filename"),
                        )

    def _validate_default_values(self) -> None:
        from maxi.core.types import _MISSING

        for alias, type_def in self.result.schema.types.items():
            for field in type_def.fields:
                if field.default_value is _MISSING:
                    continue
                dv = str(field.default_value)
                te = field.type_expr
                if not te:
                    continue
                if te == "int":
                    if not re.match(r"^-?\d+$", dv):
                        raise MaxiError(
                            f"Invalid default value '{field.default_value}' for field '{field.name}' of type 'int' in '{alias}'",
                            MaxiErrorCode.InvalidDefaultValueError,
                            filename=self.options.get("filename"),
                        )
                elif te in ("float", "decimal"):
                    try:
                        float(dv)
                    except ValueError:
                        raise MaxiError(
                            f"Invalid default value '{field.default_value}' for field '{field.name}' of type '{te}' in '{alias}'",
                            MaxiErrorCode.InvalidDefaultValueError,
                            filename=self.options.get("filename"),
                        )
                elif te == "bool":
                    if dv not in ("true", "false", "1", "0"):
                        raise MaxiError(
                            f"Invalid default value '{field.default_value}' for field '{field.name}' of type 'bool' in '{alias}'",
                            MaxiErrorCode.InvalidDefaultValueError,
                            filename=self.options.get("filename"),
                        )

    def _resolve_inheritance(self) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()

        def resolve(alias: str) -> None:
            if alias in visited:
                return
            if alias in visiting:
                raise MaxiError(
                    f"Circular inheritance detected involving type '{alias}'",
                    MaxiErrorCode.CircularInheritanceError,
                )
            td = self.result.schema.get_type(alias)
            if td is None or td._inheritance_resolved:
                return

            visiting.add(alias)

            inherited: list[MaxiFieldDef] = []
            for parent_alias in td.parents:
                parent = self.result.schema.get_type(parent_alias)
                if parent is None:
                    raise MaxiError(
                        f"Type '{alias}' inherits from '{parent_alias}', but '{parent_alias}' is not defined",
                        MaxiErrorCode.UndefinedParentError,
                    )
                resolve(parent_alias)
                for pf in parent.fields:
                    if not any(f.name == pf.name for f in inherited):
                        inherited.append(pf)

            final = list(inherited)
            for own in td.fields:
                idx = next((i for i, f in enumerate(final) if f.name == own.name), -1)
                if idx >= 0:
                    final[idx] = own
                else:
                    final.append(own)

            td.fields = final
            td._inheritance_resolved = True
            td._invalidate_cache()

            visiting.discard(alias)
            visited.add(alias)

        for alias in list(self.result.schema.types.keys()):
            resolve(alias)

    def _build_name_index(self) -> None:
        name_to_alias: dict[str, str] = {}
        for alias, td in self.result.schema.types.items():
            if td.name and td.name not in name_to_alias:
                name_to_alias[td.name] = alias

        self.result.schema._name_to_alias = name_to_alias

        def _resolve(maybe: str | None) -> str | None:
            if not maybe:
                return None
            if maybe in self.result.schema.types:
                return maybe
            return name_to_alias.get(maybe)

        self.result.schema.resolve_type_alias = _resolve

    async def _load_external_schema(self, path_or_url: str, line_number: int) -> None:
        load_fn = self.options.get("load_schema")
        if not load_fn:
            raise MaxiError(
                f"Cannot load schema '{path_or_url}': no load_schema function provided",
                MaxiErrorCode.SchemaLoadError,
                line=line_number,
                filename=self.options.get("filename"),
            )

        self.loading_stack.add(path_or_url)
        try:
            result = load_fn(path_or_url)
            if inspect.isawaitable(result):
                schema_content = await result
            else:
                schema_content = result

            ext = SchemaParser(schema_content, self.result, {**self.options, "filename": path_or_url})
            ext._is_imported = True
            ext.loading_stack = self.loading_stack
            await ext.parse()
        except MaxiError:
            raise
        except Exception as exc:
            raise MaxiError(
                f"Failed to load schema '{path_or_url}': {exc}",
                MaxiErrorCode.SchemaLoadError,
                line=line_number,
                filename=self.options.get("filename"),
                cause=exc,
            )
        finally:
            self.loading_stack.discard(path_or_url)
