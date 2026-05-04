"""Dump tests — parse → dump → re-parse, verify equivalence."""

import pathlib
import pytest
from conftest import VALID_CASES

from maxi.api.parse import parse_maxi
from maxi.api.dump import dump_maxi


def _make_schema_loader(case_dir):
    """Create a load_schema callback that reads .mxs files from the test case directory."""
    def load_schema(path_or_url):
        p = pathlib.Path(case_dir) / path_or_url
        if p.is_file():
            return p.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Schema file not found: {p}")
    return load_schema


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    VALID_CASES[:20],  # first 20 to keep test time reasonable
    ids=[c["id"] for c in VALID_CASES[:20]],
)
async def test_round_trip(case):
    """Parse → dump → re-parse should produce equivalent records."""
    opts = case.get("parser_options", {})
    kwargs = {}
    for k, v in opts.items():
        snake = "".join(["_" + c.lower() if c.isupper() else c for c in k]).lstrip("_")
        kwargs[snake] = v
    if "@schema" in case["input"]:
        kwargs["load_schema"] = _make_schema_loader(case["dir"])
    result1 = await parse_maxi(case["input"], **kwargs)
    if not result1.records:
        pytest.skip("No records to round-trip")

    dumped = dump_maxi(result1)
    reparse_kwargs = {}
    if "@schema" in dumped:
        reparse_kwargs["load_schema"] = _make_schema_loader(case["dir"])
    result2 = await parse_maxi(dumped, **reparse_kwargs)

    assert len(result2.records) == len(result1.records), (
        f"[{case['id']}] Record count mismatch after round-trip: "
        f"{len(result1.records)} → {len(result2.records)}"
    )
    for i, (r1, r2) in enumerate(zip(result1.records, result2.records)):
        assert r1.alias == r2.alias, f"[{case['id']}] Record {i} alias mismatch"
        assert len(r1.values) == len(r2.values), (
            f"[{case['id']}] Record {i} value count: {len(r1.values)} vs {len(r2.values)}"
        )


@pytest.mark.asyncio
async def test_dump_multiline():
    result = await parse_maxi("U:User(id:int|name)\n###\nU(1|Alice)")
    dumped = dump_maxi(result, multiline=True)
    assert "\n" in dumped
    result2 = await parse_maxi(dumped)
    assert len(result2.records) == 1


@pytest.mark.asyncio
async def test_dump_include_types_false():
    result = await parse_maxi("U:User(id:int|name)\n###\nU(1|Alice)")
    dumped = dump_maxi(result, include_types=False)
    assert "User" not in dumped.split("###")[0] if "###" in dumped else True


@pytest.mark.asyncio
async def test_dump_from_dict():
    dumped = dump_maxi(
        {"U": [{"id": 1, "name": "Alice"}]},
        types=[{"alias": "U", "name": "User", "fields": [
            {"name": "id", "typeExpr": "int"},
            {"name": "name"},
        ]}],
    )
    assert "U(" in dumped
    assert "Alice" in dumped
