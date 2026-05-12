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


@pytest.mark.asyncio
async def test_dump_enum_alias_emits_alias_from_full_value():
    """Full value input -> alias on wire."""
    from maxi.api.dump import dump_maxi
    users = [{"id": 1, "name": "Alice", "role": "admin"},
             {"id": 2, "name": "Bob",   "role": "editor"}]
    result = dump_maxi(users, default_alias="U", types=[{
        "alias": "U", "name": "User",
        "fields": [
            {"name": "id",   "type_expr": "int"},
            {"name": "name"},
            {"name": "role", "type_expr": "enum[a:admin,e:editor,v:viewer]"},
        ],
    }])
    assert "U(1|Alice|a)" in result
    assert "U(2|Bob|e)" in result


@pytest.mark.asyncio
async def test_dump_enum_alias_emits_alias_from_alias_input():
    """Alias as input -> same alias on wire."""
    from maxi.api.dump import dump_maxi
    users = [{"id": 1, "name": "Alice", "role": "a"}]
    result = dump_maxi(users, default_alias="U", types=[{
        "alias": "U",
        "fields": [
            {"name": "id",   "type_expr": "int"},
            {"name": "name"},
            {"name": "role", "type_expr": "enum[a:admin,e:editor]"},
        ],
    }])
    assert "U(1|Alice|a)" in result


@pytest.mark.asyncio
async def test_dump_enum_no_alias_unchanged():
    """Plain enum (no aliases) is written as-is."""
    from maxi.api.dump import dump_maxi
    users = [{"id": 1, "role": "admin"}]
    result = dump_maxi(users, default_alias="U", types=[{
        "alias": "U",
        "fields": [
            {"name": "id",   "type_expr": "int"},
            {"name": "role", "type_expr": "enum[admin,user,guest]"},
        ],
    }])
    assert "U(1|admin)" in result


@pytest.mark.asyncio
async def test_dump_enum_int_alias_emits_alias():
    """enum<int> with alias: integer value input -> alias on wire."""
    from maxi.api.dump import dump_maxi
    devices = [{"id": 1, "state": 1000}]
    result = dump_maxi(devices, default_alias="D", types=[{
        "alias": "D",
        "fields": [
            {"name": "id",    "type_expr": "int"},
            {"name": "state", "type_expr": "enum<int>[O:900,R:1000,E:999]"},
        ],
    }])
    assert "D(1|R)" in result
