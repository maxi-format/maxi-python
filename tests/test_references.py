"""Reference tests — cross-record references, missing refs in strict/lax."""

import pytest

from maxi.api.parse import parse_maxi
from maxi.core.errors import MaxiError


@pytest.mark.asyncio
async def test_valid_reference():
    text = (
        "U:User(id:int|name)\n"
        "O:Order(id:int|user:U|total:int)\n"
        "###\n"
        "U(1|Alice)\n"
        "O(100|1|50)"
    )
    result = await parse_maxi(text)
    assert len(result.records) == 2


@pytest.mark.asyncio
async def test_missing_reference_lax():
    text = (
        "U:User(id:int|name)\n"
        "O:Order(id:int|user:U|total:int)\n"
        "###\n"
        "O(100|999|50)"
    )
    result = await parse_maxi(text, mode="lax")
    assert isinstance(result.warnings, list)


@pytest.mark.asyncio
async def test_missing_reference_strict():
    text = (
        "@mode:strict\n"
        "U:User(id:int|name)\n"
        "O:Order(id:int|user:U|total:int)\n"
        "###\n"
        "O(100|999|50)"
    )
    with pytest.raises(MaxiError):
        await parse_maxi(text, mode="strict")


@pytest.mark.asyncio
async def test_forward_reference():
    """References to objects defined later in the file should work."""
    text = (
        "U:User(id:int|name)\n"
        "O:Order(id:int|user:U|total:int)\n"
        "###\n"
        "O(100|1|50)\n"
        "U(1|Alice)"
    )
    result = await parse_maxi(text)
    assert len(result.records) == 2
