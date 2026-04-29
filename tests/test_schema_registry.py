"""Schema registry tests."""

import pytest

from maxi.core.registry import define_maxi_schema, get_maxi_schema, undefine_maxi_schema


class RegTestClass:
    pass


def test_define_and_get():
    schema = {"alias": "RT", "name": "RegTest", "fields": []}
    define_maxi_schema(RegTestClass, schema)
    result = get_maxi_schema(RegTestClass)
    assert result is schema
    assert result["alias"] == "RT"


def test_get_from_instance():
    schema = {"alias": "RT2", "fields": []}
    define_maxi_schema(RegTestClass, schema)
    obj = RegTestClass()
    result = get_maxi_schema(obj)
    assert result["alias"] == "RT2"


def test_undefine():
    schema = {"alias": "RT3", "fields": []}
    define_maxi_schema(RegTestClass, schema)
    undefine_maxi_schema(RegTestClass)
    result = get_maxi_schema(RegTestClass)
    assert result is None


def test_class_attribute_takes_priority():
    class WithAttr:
        __maxi_schema__ = {"alias": "WA", "fields": []}

    define_maxi_schema(WithAttr, {"alias": "WA2", "fields": []})
    result = get_maxi_schema(WithAttr)
    assert result["alias"] == "WA"


def test_invalid_class_raises():
    with pytest.raises(TypeError):
        define_maxi_schema("not_a_class", {"alias": "X"})


def test_missing_alias_raises():
    with pytest.raises(TypeError):
        define_maxi_schema(RegTestClass, {"fields": []})
