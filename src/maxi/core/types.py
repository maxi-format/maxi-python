"""
Core MAXI type definitions (IR – Intermediate Representation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MISSING: Any = object()


@dataclass
class ParsedConstraint:
    """A single parsed constraint from a field's ``{…}`` block."""

    type: str
    value: Any = None
    operator: str | None = None
    int_min: int | None = None
    int_max: int | None = None
    frac_min: int | None = None
    frac_max: int | None = None


@dataclass(slots=True)
class MaxiFieldDef:
    """One field inside a :class:`MaxiTypeDef` definition."""

    name: str
    type_expr: str | None = None
    annotation: str | None = None
    constraints: list[ParsedConstraint] | None = None
    element_constraints: list[ParsedConstraint] | None = None
    default_value: Any = field(default=_MISSING)

    def is_required(self) -> bool:
        """``True`` if the field has a ``required`` (``!``) constraint."""
        return self.constraints is not None and any(
            c.type == "required" for c in self.constraints
        )

    def is_id(self) -> bool:
        """``True`` if the field has an ``id`` (``#``) constraint."""
        return self.constraints is not None and any(
            c.type == "id" for c in self.constraints
        )

    def has_default(self) -> bool:
        """``True`` if a default value was explicitly provided."""
        return self.default_value is not _MISSING


_ENUM_RE = re.compile(r"^enum(?:<(\w+)>)?\[([^\]]*)\]$")


@dataclass
class MaxiTypeDef:
    """A named or alias-only type definition from the schema section."""

    alias: str
    name: str | None = None
    parents: list[str] = field(default_factory=list)
    fields: list[MaxiFieldDef] = field(default_factory=list)

    _inheritance_resolved: bool = field(default=False, repr=False, compare=False)
    _id_field_index: int = field(default=-2, repr=False, compare=False)
    _required_flags: list[bool] | None = field(default=None, repr=False, compare=False)
    _enum_values: list[list[str] | None] | None = field(default=None, repr=False, compare=False)
    _enum_alias_map: list[dict[str, Any] | None] | None = field(default=None, repr=False, compare=False)
    _has_runtime_constraints: bool = field(default=False, repr=False, compare=False)

    def add_field(self, f: MaxiFieldDef) -> None:
        self.fields.append(f)
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        self._id_field_index = -2
        self._required_flags = None
        self._enum_values = None
        self._enum_alias_map = None

    def _ensure_cache(self) -> None:
        if self._id_field_index != -2:
            return

        length = len(self.fields)

        self._id_field_index = -1
        for i, f in enumerate(self.fields):
            if f.constraints and any(c.type == "id" for c in f.constraints):
                self._id_field_index = i
                break
        if self._id_field_index == -1:
            for i, f in enumerate(self.fields):
                if f.name == "id":
                    self._id_field_index = i
                    break

        self._required_flags = [
            (f.constraints is not None and any(c.type == "required" for c in f.constraints))
            for f in self.fields
        ]

        self._enum_values = [None] * length
        self._enum_alias_map = [None] * length
        self._has_runtime_constraints = False
        for i, f in enumerate(self.fields):
            if f.type_expr and f.type_expr.startswith("enum"):
                m = _ENUM_RE.match(f.type_expr)
                if m:
                    is_int = (m.group(1) or "str") == "int"
                    tokens = [v.strip() for v in m.group(2).split(",") if v.strip()]
                    amap: dict[str, Any] = {}
                    full_values: list[str] = []
                    for token in tokens:
                        ci = token.find(":")
                        if ci != -1:
                            alias = token[:ci]
                            full_str = token[ci + 1:]
                        else:
                            alias = token
                            full_str = token
                        full_val: Any = int(full_str) if is_int else full_str
                        amap[alias] = full_val
                        if alias != full_str:
                            amap[full_str] = full_val
                        full_values.append(str(full_val))
                    self._enum_values[i] = full_values
                    self._enum_alias_map[i] = amap
            if f.constraints:
                for c in f.constraints:
                    if c.type in ("comparison", "pattern", "exact-length"):
                        self._has_runtime_constraints = True

    def get_id_field(self) -> MaxiFieldDef | None:
        """Return the ID field, or ``None`` if none is defined."""
        self._ensure_cache()
        return self.fields[self._id_field_index] if self._id_field_index >= 0 else None

    def get_id_field_index(self) -> int:
        """Return the index of the ID field, or ``-1``."""
        self._ensure_cache()
        return self._id_field_index

    def get_required_flags(self) -> list[bool]:
        """Return a list of per-field required booleans (cached)."""
        self._ensure_cache()
        assert self._required_flags is not None
        return self._required_flags

    def get_enum_values(self, field_index: int) -> list[str] | None:
        """Return parsed enum semantic values for *field_index*, or ``None``."""
        self._ensure_cache()
        assert self._enum_values is not None
        return self._enum_values[field_index]

    def get_enum_alias_map(self, field_index: int) -> dict[str, Any] | None:
        """Return the wire-token → semantic-value map for *field_index*, or ``None``."""
        self._ensure_cache()
        assert self._enum_alias_map is not None
        return self._enum_alias_map[field_index]

    @property
    def has_runtime_constraints(self) -> bool:
        self._ensure_cache()
        return self._has_runtime_constraints


@dataclass
class MaxiSchema:
    """Top-level schema object aggregating version, imports, and types."""

    version: str = "1.0.0"
    imports: list[str] = field(default_factory=list)
    types: dict[str, MaxiTypeDef] = field(default_factory=dict)
    _name_to_alias: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    resolve_type_alias: Any = field(default=None, repr=False, compare=False)

    def add_type(self, type_def: MaxiTypeDef) -> None:
        """Register a type definition keyed by its alias."""
        self.types[type_def.alias] = type_def

    def get_type(self, alias: str) -> MaxiTypeDef | None:
        """Look up a type definition by alias."""
        return self.types.get(alias)

    def has_type(self, alias: str) -> bool:
        """Check whether a type alias is registered."""
        return alias in self.types


@dataclass(slots=True)
class MaxiRecord:
    """A single data record from the records section."""

    alias: str
    values: list[Any] = field(default_factory=list)
    line_number: int | None = None



@dataclass
class MaxiWarning:
    """A non-fatal warning produced during parsing."""

    message: str
    code: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass
class MaxiParseResult:
    """Result of :func:`parse_maxi` – contains schema, records, and warnings."""

    schema: MaxiSchema = field(default_factory=MaxiSchema)
    records: list[MaxiRecord] = field(default_factory=list)
    warnings: list[MaxiWarning] = field(default_factory=list)

    def add_warning(
        self,
        message: str,
        *,
        code: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.warnings.append(MaxiWarning(message=message, code=code, line=line, column=column))


@dataclass
class MaxiHydrateResult:
    """Result of :func:`parse_maxi_as` / :func:`parse_maxi_auto_as`."""

    schema: MaxiSchema = field(default_factory=MaxiSchema)
    data: dict[str, list[Any]] = field(default_factory=dict)
    warnings: list[MaxiWarning] = field(default_factory=list)
