"""Constraint tests — schema-time and record-time validation."""

import pytest

from maxi.api.parse import parse_maxi
from maxi.core.errors import MaxiError


@pytest.mark.asyncio
async def test_strict_required_violation():
    text = "U:User(id:int|name(!)) \n###\nU(1|~)"
    with pytest.raises(MaxiError):
        await parse_maxi(text, allow_missing_fields="error")


@pytest.mark.asyncio
async def test_lax_required_produces_warning():
    text = "U:User(id:int|name(!)) \n###\nU(1|~)"
    result = await parse_maxi(text)
    assert isinstance(result.warnings, list)


@pytest.mark.asyncio
async def test_strict_min_violation():
    text = "P:Product(id:int|stock:int(>=0))\n###\nP(1|-5)"
    with pytest.raises(MaxiError):
        await parse_maxi(text, allow_constraint_violations="error")


@pytest.mark.asyncio
async def test_valid_constraints_pass():
    text = "P:Product(id:int|stock:int(>=0))\n###\nP(1|10)"
    result = await parse_maxi(text)
    assert len(result.records) == 1
    assert result.records[0].values[1] == 10

@pytest.mark.asyncio
async def test_enum_alias_expands_to_full_value():
    text = "U:User(id:int|role:enum[a:admin,e:editor,v:viewer])\n###\nU(1|a)\nU(2|e)\nU(3|v)"
    result = await parse_maxi(text)
    assert result.records[0].values[1] == "admin"
    assert result.records[1].values[1] == "editor"
    assert result.records[2].values[1] == "viewer"


@pytest.mark.asyncio
async def test_enum_alias_mixed_mode():
    text = "U:User(id:int|role:enum[a:admin,user,guest])\n###\nU(1|a)\nU(2|user)\nU(3|guest)"
    result = await parse_maxi(text)
    assert result.records[0].values[1] == "admin"
    assert result.records[1].values[1] == "user"
    assert result.records[2].values[1] == "guest"


@pytest.mark.asyncio
async def test_enum_int_alias_expands_to_integer():
    text = "D:Device(id:int|state:enum<int>[O:900,R:1000,E:999])\n###\nD(1|R)\nD(2|O)"
    result = await parse_maxi(text)
    assert result.records[0].values[1] == 1000
    assert result.records[1].values[1] == 900
    assert isinstance(result.records[0].values[1], int)


@pytest.mark.asyncio
async def test_enum_full_value_accepted_as_wire_token():
    """Backward compat: full value also accepted as wire token."""
    text = "U:User(id:int|role:enum[a:admin,e:editor])\n###\nU(1|admin)"
    result = await parse_maxi(text)
    assert result.records[0].values[1] == "admin"


@pytest.mark.asyncio
async def test_enum_duplicate_alias_raises_e021():
    from maxi.core.errors import MaxiErrorCode
    text = "U:User(id:int|role:enum[a:admin,a:editor])\n###"
    with pytest.raises(MaxiError) as exc_info:
        await parse_maxi(text)
    assert exc_info.value.code == MaxiErrorCode.EnumAliasError


@pytest.mark.asyncio
async def test_enum_duplicate_full_value_raises_e021():
    from maxi.core.errors import MaxiErrorCode
    text = "U:User(id:int|role:enum[a:admin,b:admin])\n###"
    with pytest.raises(MaxiError) as exc_info:
        await parse_maxi(text)
    assert exc_info.value.code == MaxiErrorCode.EnumAliasError


@pytest.mark.asyncio
async def test_enum_alias_equals_other_full_value_raises_e021():
    from maxi.core.errors import MaxiErrorCode
    text = "U:User(id:int|role:enum[admin:superadmin,e:admin])\n###"
    with pytest.raises(MaxiError) as exc_info:
        await parse_maxi(text)
    assert exc_info.value.code == MaxiErrorCode.EnumAliasError


@pytest.mark.asyncio
async def test_enum_unknown_wire_token_raises_e008_in_strict_mode():
    from maxi.core.errors import MaxiErrorCode
    text = "U:User(id:int|role:enum[a:admin,e:editor])\n###\nU(1|superadmin)"
    with pytest.raises(MaxiError) as exc_info:
        await parse_maxi(text, allow_constraint_violations="error")
    assert exc_info.value.code == MaxiErrorCode.ConstraintViolationError

