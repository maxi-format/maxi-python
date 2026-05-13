"""
Schema registry – associate MAXI schema descriptors with Python classes.
"""

from __future__ import annotations

import weakref
from typing import Any

_registry: dict[int, tuple[weakref.ref, dict[str, Any]]] = {}


def define_maxi_schema(cls: type, schema: dict[str, Any]) -> None:
    """Register a MAXI schema descriptor for *cls*.

    Use this when you cannot add ``__maxi_schema__`` directly – e.g. for
    third-party classes.

    Args:
        cls: The class to register.
        schema: A dict with at least ``"alias"`` (str) and optionally
                ``"name"``, ``"fields"``, ``"parents"``.
    """
    if not isinstance(cls, type):
        raise TypeError("define_maxi_schema: first argument must be a class.")
    if not isinstance(schema, dict):
        raise TypeError("define_maxi_schema: second argument must be a schema dict.")
    if not schema.get("alias") or not isinstance(schema["alias"], str):
        raise TypeError("define_maxi_schema: schema['alias'] is required and must be a string.")

    def _remove(ref: weakref.ref) -> None:
        _registry.pop(id(cls), None)

    _registry[id(cls)] = (weakref.ref(cls, _remove), schema)


def get_maxi_schema(cls_or_instance: Any) -> dict[str, Any] | None:
    """Look up the MAXI schema descriptor for a class or instance."""
    if cls_or_instance is None:
        return None

    cls = cls_or_instance if isinstance(cls_or_instance, type) else type(cls_or_instance)
    if cls is object or cls is type:
        return None

    schema = getattr(cls, "__maxi_schema__", None)
    if isinstance(schema, dict):
        return schema

    entry = _registry.get(id(cls))
    if entry is not None:
        ref, schema = entry
        if ref() is cls:
            return schema
        _registry.pop(id(cls), None)

    return None


def undefine_maxi_schema(cls: type) -> None:
    """Remove a previously registered schema."""
    _registry.pop(id(cls), None)
