"""Model tests — declarative field definitions, inheritance, schema generation."""

import pytest

from maxi.models import (
    MaxiModel, IntField, StrField, FloatField, DecimalField,
    BoolField, ArrayField, MapField, EnumField, RefField,
)


class User(MaxiModel, alias="U", name="User"):
    id = IntField(required=True, id=True)
    name = StrField(min_length=3, max_length=50)
    email = StrField(annotation="email", required=True)
    role = StrField(default="guest")


class Order(MaxiModel, alias="O", name="Order"):
    id = IntField(required=True, id=True)
    user = RefField(User, required=True)
    total = DecimalField(min=0, precision="0:10.2")
    items = ArrayField("str", min_items=1)


def test_schema_generated():
    schema = User.__maxi_schema__
    assert schema["alias"] == "U"
    assert schema["name"] == "User"
    assert len(schema["fields"]) == 4


def test_field_names():
    fields = User.__maxi_schema__["fields"]
    names = [f["name"] for f in fields]
    assert names == ["id", "name", "email", "role"]


def test_field_type_expr():
    fields = User.__maxi_schema__["fields"]
    assert fields[0]["typeExpr"] == "int"
    assert fields[1]["typeExpr"] == "str"


def test_constraints_required_id():
    fields = User.__maxi_schema__["fields"]
    id_field = fields[0]
    types = [c["type"] for c in id_field["constraints"]]
    assert "required" in types
    assert "id" in types


def test_default_value():
    fields = User.__maxi_schema__["fields"]
    role_field = fields[3]
    assert role_field["defaultValue"] == "guest"


def test_ref_field_type_expr():
    fields = Order.__maxi_schema__["fields"]
    user_field = fields[1]
    assert user_field["typeExpr"] == "U"


def test_array_field():
    fields = Order.__maxi_schema__["fields"]
    items_field = fields[3]
    assert items_field["typeExpr"] == "str[]"



class Employee(User, alias="E", name="Employee"):
    department = StrField(required=True)
    salary = DecimalField(min=0)


def test_inheritance_parents():
    schema = Employee.__maxi_schema__
    assert schema["parents"] == ["U"]


def test_inheritance_own_fields_only():
    schema = Employee.__maxi_schema__
    names = [f["name"] for f in schema["fields"]]
    assert "department" in names
    assert "salary" in names
    assert "id" not in names




def test_instance_creation():
    u = User(id=1, name="Alice", email="alice@example.com")
    assert u.id == 1
    assert u.name == "Alice"
    assert u.role == "guest"  # default


def test_instance_repr():
    u = User(id=1, name="Alice", email="a@b.com")
    r = repr(u)
    assert "U(" in r
