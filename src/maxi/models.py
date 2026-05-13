"""
Declarative model layer – field descriptors for MAXI schemas.

Usage::

    from maxi.models import MaxiModel, IntField, StrField, RefField

    class User(MaxiModel, alias="U", name="User"):
        id = IntField(required=True, id=True)
        name = StrField(min_length=3, max_length=50)
        email = StrField(annotation="email", required=True)
        role = StrField(default="guest")
"""

from __future__ import annotations

from typing import Any

_MISSING = object()


class _FieldBase:
    """Common base for all MAXI field descriptors."""

    _type_expr: str  # subclasses set this

    def __init__(
        self,
        *,
        required: bool = False,
        id: bool = False,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        self.required = required
        self.id = id
        self.annotation = annotation
        self.default = default
        # Set by __set_name__
        self.attr_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.attr_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        return obj.__dict__.get(self.attr_name, self.default if self.default is not _MISSING else None)

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__[self.attr_name] = value

    def _build_constraints(self) -> list[dict[str, Any]]:
        """Build constraint list for the schema descriptor."""
        cs: list[dict[str, Any]] = []
        if self.required:
            cs.append({"type": "required"})
        if self.id:
            cs.append({"type": "id"})
        return cs

    def _to_field_descriptor(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.attr_name, "typeExpr": self._type_expr}
        if self.annotation:
            d["annotation"] = self.annotation
        cs = self._build_constraints()
        if cs:
            d["constraints"] = cs
        if self.default is not _MISSING:
            d["defaultValue"] = self.default
        return d


class StrField(_FieldBase):
    _type_expr = "str"

    def __init__(
        self,
        *,
        required: bool = False,
        id: bool = False,
        min_length: int | None = None,
        max_length: int | None = None,
        pattern: str | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, id=id, annotation=annotation, default=default)
        self.min_length = min_length
        self.max_length = max_length
        self.pattern = pattern

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min_length is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min_length})
        if self.max_length is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max_length})
        if self.pattern is not None:
            cs.append({"type": "pattern", "value": self.pattern})
        return cs


class IntField(_FieldBase):
    _type_expr = "int"

    def __init__(
        self,
        *,
        required: bool = False,
        id: bool = False,
        min: int | None = None,
        max: int | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, id=id, annotation=annotation, default=default)
        self.min = min
        self.max = max

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min})
        if self.max is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max})
        return cs


class FloatField(_FieldBase):
    _type_expr = "float"

    def __init__(
        self,
        *,
        required: bool = False,
        min: float | None = None,
        max: float | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, annotation=annotation, default=default)
        self.min = min
        self.max = max

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min})
        if self.max is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max})
        return cs


class DecimalField(_FieldBase):
    _type_expr = "decimal"

    def __init__(
        self,
        *,
        required: bool = False,
        min: float | int | None = None,
        max: float | int | None = None,
        precision: str | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, annotation=annotation, default=default)
        self.min = min
        self.max = max
        self.precision = precision

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min})
        if self.max is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max})
        if self.precision is not None:
            cs.append({"type": "decimal-precision", "value": self.precision})
        return cs


class BoolField(_FieldBase):
    _type_expr = "bool"

    def __init__(
        self,
        *,
        required: bool = False,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, default=default)


class BytesField(_FieldBase):
    _type_expr = "bytes"

    def __init__(
        self,
        *,
        required: bool = False,
        mime: str | list[str] | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, annotation=annotation, default=default)
        self.mime = mime
        self.min_length = min_length
        self.max_length = max_length

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.mime is not None:
            cs.append({"type": "mime", "value": self.mime})
        if self.min_length is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min_length})
        if self.max_length is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max_length})
        return cs


class ArrayField(_FieldBase):
    def __init__(
        self,
        item_type: str = "str",
        *,
        required: bool = False,
        min_items: int | None = None,
        max_items: int | None = None,
        element_constraints: list[dict[str, Any]] | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, annotation=annotation, default=default)
        self.item_type = item_type
        self._type_expr = f"{item_type}[]"
        self.min_items = min_items
        self.max_items = max_items
        self.element_constraints = element_constraints

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min_items is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min_items})
        if self.max_items is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max_items})
        return cs

    def _to_field_descriptor(self) -> dict[str, Any]:
        d = super()._to_field_descriptor()
        if self.element_constraints:
            d["elementConstraints"] = self.element_constraints
        return d


class MapField(_FieldBase):
    def __init__(
        self,
        key_type: str = "str",
        value_type: str = "str",
        *,
        required: bool = False,
        min_keys: int | None = None,
        max_keys: int | None = None,
        annotation: str | None = None,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, annotation=annotation, default=default)
        self.key_type = key_type
        self.value_type = value_type
        if key_type == "str" and value_type == "str":
            self._type_expr = "map"
        elif key_type == "str":
            self._type_expr = f"map<{value_type}>"
        else:
            self._type_expr = f"map<{key_type},{value_type}>"
        self.min_keys = min_keys
        self.max_keys = max_keys

    def _build_constraints(self) -> list[dict[str, Any]]:
        cs = super()._build_constraints()
        if self.min_keys is not None:
            cs.append({"type": "comparison", "operator": ">=", "value": self.min_keys})
        if self.max_keys is not None:
            cs.append({"type": "comparison", "operator": "<=", "value": self.max_keys})
        return cs


class EnumField(_FieldBase):
    def __init__(
        self,
        values: list[str],
        *,
        base_type: str = "str",
        required: bool = False,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, default=default)
        self.values = values
        self.base_type = base_type
        if base_type == "str":
            self._type_expr = f"enum({','.join(values)})"
        else:
            self._type_expr = f"enum<{base_type}>({','.join(values)})"


class RefField(_FieldBase):
    """Reference to another MaxiModel type."""

    def __init__(
        self,
        ref_class_or_alias: type | str,
        *,
        required: bool = False,
        default: Any = _MISSING,
    ) -> None:
        super().__init__(required=required, default=default)
        self._ref = ref_class_or_alias
        # _type_expr resolved lazily in _to_field_descriptor

    @property
    def _type_expr(self) -> str:
        if isinstance(self._ref, str):
            return self._ref
        schema = getattr(self._ref, "__maxi_schema__", None)
        if schema and isinstance(schema, dict):
            return schema["alias"]
        return self._ref.__name__

    @_type_expr.setter
    def _type_expr(self, value: str) -> None:
        pass  # ignore base class init


class MaxiModel:
    """Declarative base class for MAXI schema definitions."""

    __maxi_schema__: dict[str, Any]

    def __init_subclass__(
        cls,
        *,
        alias: str | None = None,
        name: str | None = None,
        parents: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)

        if alias is None:
            # Abstract intermediate — skip schema generation
            return

        # Collect field descriptors in definition order
        field_descriptors: list[tuple[str, _FieldBase]] = []
        for attr_name in list(cls.__dict__):
            val = cls.__dict__[attr_name]
            if isinstance(val, _FieldBase):
                field_descriptors.append((attr_name, val))

        # Auto-detect parent MaxiModel classes
        if parents is None:
            parents = []
            for base in cls.__mro__[1:]:
                if base is MaxiModel or base is cls:
                    continue
                if hasattr(base, "__maxi_schema__") and isinstance(base.__maxi_schema__, dict):
                    parents.append(base.__maxi_schema__["alias"])

        # Build schema descriptor
        fields = [fd._to_field_descriptor() for _, fd in field_descriptors]
        schema: dict[str, Any] = {
            "alias": alias,
            "fields": fields,
        }
        if name:
            schema["name"] = name
        if parents:
            schema["parents"] = list(parents)

        cls.__maxi_schema__ = schema

    def __init__(self, **kwargs: Any) -> None:
        # Set field values from kwargs, falling back to field defaults
        for attr_name in list(type(self).__dict__):
            val = type(self).__dict__[attr_name]
            if isinstance(val, _FieldBase):
                if attr_name in kwargs:
                    self.__dict__[attr_name] = kwargs[attr_name]
                elif val.default is not _MISSING:
                    self.__dict__[attr_name] = val.default
                # else: leave unset (descriptor __get__ returns None)

        # Also set any extra kwargs not matching declared fields
        for k, v in kwargs.items():
            if k not in self.__dict__:
                self.__dict__[k] = v

    def __repr__(self) -> str:
        schema = getattr(type(self), "__maxi_schema__", None)
        alias = schema["alias"] if schema else type(self).__name__
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{alias}({fields})"
