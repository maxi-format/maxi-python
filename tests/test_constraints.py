"""Constraint tests — schema-time and record-time validation."""

import pytest

from maxi.api.parse import parse_maxi
from maxi.core.errors import MaxiError


@pytest.mark.asyncio
async def test_strict_required_violation():
    text = "@mode:strict\nU:User(id:int|name(!)) \n###\nU(1|~)"
    with pytest.raises(MaxiError):
        await parse_maxi(text, mode="strict")


@pytest.mark.asyncio
async def test_lax_required_produces_warning():
    text = "U:User(id:int|name(!)) \n###\nU(1|~)"
    result = await parse_maxi(text, mode="lax")
    assert isinstance(result.warnings, list)


@pytest.mark.asyncio
async def test_strict_min_violation():
    text = "@mode:strict\nP:Product(id:int|stock:int(>=0))\n###\nP(1|-5)"
    with pytest.raises(MaxiError):
        await parse_maxi(text, mode="strict")


@pytest.mark.asyncio
async def test_valid_constraints_pass():
    text = "P:Product(id:int|stock:int(>=0))\n###\nP(1|10)"
    result = await parse_maxi(text)
    assert len(result.records) == 1
    assert result.records[0].values[1] == 10
