"""Auto-dump tests — dump_maxi_auto with MaxiModel and registry classes."""

import pytest

from maxi.models import MaxiModel, IntField, StrField
from maxi.api.dump import dump_maxi_auto
from maxi.api.parse import parse_maxi
from maxi.core.registry import define_maxi_schema


class DumpUser(MaxiModel, alias="DU", name="DumpUser"):
    id = IntField(required=True, id=True)
    name = StrField()


def test_dump_auto_from_list():
    users = [DumpUser(id=1, name="Alice"), DumpUser(id=2, name="Bob")]
    output = dump_maxi_auto(users)
    assert "DU(" in output or "DU:" in output
    assert "Alice" in output
    assert "Bob" in output


def test_dump_auto_from_dict():
    users = [DumpUser(id=1, name="Alice")]
    output = dump_maxi_auto({"DU": users})
    assert "Alice" in output


@pytest.mark.asyncio
async def test_dump_auto_round_trip():
    users = [DumpUser(id=1, name="Alice"), DumpUser(id=2, name="Bob")]
    output = dump_maxi_auto(users)
    result = await parse_maxi(output)
    assert len(result.records) == 2



class ThirdParty:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_dump_auto_with_registry():
    define_maxi_schema(ThirdParty, {
        "alias": "TP",
        "name": "ThirdParty",
        "fields": [
            {"name": "id", "typeExpr": "int"},
            {"name": "value"},
        ],
    })
    items = [ThirdParty(id=1, value="hello")]
    output = dump_maxi_auto(items, default_alias="TP")
    assert "hello" in output
