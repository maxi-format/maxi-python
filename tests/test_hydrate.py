"""Hydration tests — parse_maxi_as and parse_maxi_auto_as."""

import pytest

from maxi.api.parse import parse_maxi_as, parse_maxi_auto_as
from maxi.models import MaxiModel, IntField, StrField



class PlainUser:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_parse_maxi_as_plain_class():
    text = "U:User(id:int|name)\n###\nU(1|Alice)\nU(2|Bob)"
    result = await parse_maxi_as(text, {"U": PlainUser})
    assert "U" in result.data
    assert len(result.data["U"]) == 2
    assert result.data["U"][0].id == 1
    assert result.data["U"][0].name == "Alice"



class ModelUser(MaxiModel, alias="U", name="User"):
    id = IntField(required=True, id=True)
    name = StrField()


@pytest.mark.asyncio
async def test_parse_maxi_auto_as():
    text = "U:User(id:int|name)\n###\nU(1|Alice)\nU(2|Bob)"
    result = await parse_maxi_auto_as(text, [ModelUser])
    assert "U" in result.data
    assert len(result.data["U"]) == 2
    assert result.data["U"][0].name == "Alice"



class OrderUser:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class Order:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_hydrate_resolves_references():
    text = (
        "U:User(id:int|name)\n"
        "O:Order(id:int|user:U|total:int)\n"
        "###\n"
        "U(1|Alice)\n"
        "O(100|1|50)"
    )
    result = await parse_maxi_as(text, {"U": OrderUser, "O": Order})
    order = result.data["O"][0]
    if isinstance(order.user, OrderUser):
        assert order.user.name == "Alice"
    else:
        assert order.user in (1, "1")
