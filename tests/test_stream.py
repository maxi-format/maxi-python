"""Stream tests — async iteration over records."""

import pytest

from maxi.api.stream import stream_maxi


@pytest.mark.asyncio
async def test_stream_schema_available_before_iteration():
    text = "U:User(id:int|name)\n###\nU(1|Alice)\nU(2|Bob)"
    result = await stream_maxi(text)
    assert result.schema is not None
    assert len(result.schema.types) == 1


@pytest.mark.asyncio
async def test_stream_yields_records():
    text = "U:User(id:int|name)\n###\nU(1|Alice)\nU(2|Bob)\nU(3|Charlie)"
    result = await stream_maxi(text)
    records = []
    async for record in result:
        records.append(record)
    assert len(records) == 3
    assert records[0].values[0] == 1
    assert records[2].values[1] == "Charlie"


@pytest.mark.asyncio
async def test_stream_empty_records():
    text = "U:User(id:int|name)\n###\n"
    result = await stream_maxi(text)
    records = []
    async for record in result:
        records.append(record)
    assert len(records) == 0


@pytest.mark.asyncio
async def test_stream_no_separator():
    text = "U:User(id:int|name)"
    result = await stream_maxi(text)
    records = []
    async for record in result:
        records.append(record)
    assert len(records) == 0
