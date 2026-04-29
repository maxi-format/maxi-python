"""Parser tests — run every testdata case through parse_maxi and validate."""

import json
import pytest
from conftest import ALL_TEST_CASES, VALID_CASES, ERROR_CASES, WARNING_CASES, TESTDATA_DIR

from maxi.api.parse import parse_maxi
from maxi.core.errors import MaxiError


def _make_schema_loader(case_dir):
    """Create a load_schema callback that reads .mxs files from the test case directory."""
    import pathlib

    def load_schema(path_or_url):
        p = pathlib.Path(case_dir) / path_or_url
        if p.is_file():
            return p.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Schema file not found: {p}")

    return load_schema


def _resolve_json_path(obj, path, follow_references=False, schema=None):
    """Resolve a JSON-pointer style path like '#/records/0/value/id'.

    When *follow_references* is True and the current node is a scalar (int/str)
    that matches an object ID in the registry, the resolver will transparently
    dereference it before continuing down the path.
    """
    parts = path.lstrip("#/").split("/")
    current = obj

    for i, p in enumerate(parts):
        if isinstance(current, list):
            current = current[int(p)]
        elif isinstance(current, dict):
            if p in current:
                current = current[p]
            else:
                raise KeyError(p)
        elif follow_references and isinstance(current, (int, float, str)):
            # Dereference: find the right object that has field `p`
            objects = obj.get("objects", {})
            key = str(current)
            resolved = None
            # Try all types, prefer the one that has the next path component
            for type_name, id_map in objects.items():
                candidate = id_map.get(key)
                if candidate is not None and isinstance(candidate, dict) and p in candidate:
                    resolved = candidate
                    break
            # Fallback: first match regardless
            if resolved is None:
                resolved = _deref(objects, current)
            if resolved is None:
                return None
            current = resolved
            if isinstance(current, dict) and p in current:
                current = current[p]
            elif isinstance(current, list):
                current = current[int(p)]
            else:
                return None
        else:
            return None
    return current


def _deref(objects, ref_value):
    """Look up a scalar reference value across all type registries."""
    key = str(ref_value)
    for type_name, id_map in objects.items():
        if key in id_map:
            return id_map[key]
    return None


def _normalize_value(val):
    """Normalize parsed values for comparison with expected JSON."""
    if isinstance(val, dict) and "id" in val:
        return val["id"]
    return val


def _field_output_name(field):
    """Compute the output field name, appending annotation for bytes fields (e.g. thumbnail:bytes@base64 → thumbnail_base64)."""
    if field.annotation and field.type_expr == "bytes":
        return f"{field.name}_{field.annotation}"
    return field.name


def _result_to_comparable(result):
    """Convert MaxiParseResult to a dict matching expected.json structure."""
    schema = result.schema
    records_out = []
    objects_out = {}

    for record in result.records:
        td = schema.get_type(record.alias)
        if td is None:
            # Schema-less record: use alias as type, values as positional list
            records_out.append({
                "type": record.alias,
                "value": {"values": list(record.values)},
            })
            continue
        type_name = td.name or td.alias
        value = {}
        for i, field in enumerate(td.fields):
            v = record.values[i] if i < len(record.values) else None
            out_name = _field_output_name(field)
            value[out_name] = v
        records_out.append({"type": type_name, "value": value})

        # Build objects map keyed by id
        id_idx = td.get_id_field_index()
        if id_idx >= 0 and id_idx < len(record.values):
            id_val = record.values[id_idx]
            if id_val is not None:
                objects_out.setdefault(type_name, {})[str(id_val)] = value

        # Also index inline objects found in reference fields
        for i, field in enumerate(td.fields):
            v = record.values[i] if i < len(record.values) else None
            if isinstance(v, dict) and field.type_expr:
                ref_td = schema.get_type(field.type_expr)
                if ref_td:
                    ref_type_name = ref_td.name or ref_td.alias
                    ref_id_idx = ref_td.get_id_field_index()
                    if ref_id_idx >= 0:
                        id_field_name = ref_td.fields[ref_id_idx].name
                        ref_id = v.get(id_field_name)
                        if ref_id is not None:
                            if ref_type_name not in objects_out:
                                objects_out[ref_type_name] = {}
                            if str(ref_id) not in objects_out[ref_type_name]:
                                objects_out[ref_type_name][str(ref_id)] = v

    return {"records": records_out, "objects": objects_out}



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    VALID_CASES,
    ids=[c["id"] for c in VALID_CASES],
)
async def test_valid_case(case):
    """Parse a valid MAXI document and validate record/object assertions."""
    expected = case["expected"]
    kwargs = {"mode": case["mode"]}
    if "@schema" in case["input"]:
        kwargs["load_schema"] = _make_schema_loader(case["dir"])
    result = await parse_maxi(case["input"], **kwargs)

    comparable = _result_to_comparable(result)

    for rv in expected.get("record_validations", []):
        path = rv["path"]
        expected_value = rv["expected_value"]
        follow = rv.get("follow_references", False)
        try:
            actual = _resolve_json_path(comparable, path, follow_references=follow)
        except (KeyError, IndexError, TypeError):
            pytest.fail(f"[{case['id']}] Cannot resolve path {path}")
        if isinstance(expected_value, (int, float, str)) and isinstance(actual, dict):
            actual = _normalize_value(actual)
        assert actual == expected_value, (
            f"[{case['id']}] {rv.get('description', path)}: "
            f"expected {expected_value!r}, got {actual!r}"
        )

    for ov in expected.get("object_validations", []):
        path = ov["path"]
        expected_value = ov["expected_value"]
        follow = ov.get("follow_references", False)
        try:
            actual = _resolve_json_path(comparable, path, follow_references=follow)
        except (KeyError, IndexError, TypeError):
            pytest.fail(f"[{case['id']}] Cannot resolve path {path}")
        if isinstance(expected_value, (int, float, str)) and isinstance(actual, dict):
            actual = _normalize_value(actual)
        assert actual == expected_value, (
            f"[{case['id']}] {ov.get('description', path)}: "
            f"expected {expected_value!r}, got {actual!r}"
        )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ERROR_CASES,
    ids=[c["id"] for c in ERROR_CASES],
)
async def test_error_case(case):
    """Parse a MAXI document that should raise MaxiError."""
    kwargs = {"mode": case["mode"]}
    if "@schema" in case["input"]:
        kwargs["load_schema"] = _make_schema_loader(case["dir"])
    with pytest.raises(MaxiError):
        await parse_maxi(case["input"], **kwargs)



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    WARNING_CASES,
    ids=[c["id"] for c in WARNING_CASES],
)
async def test_warning_case(case):
    """Parse a MAXI document and check warnings are produced (no exception)."""
    kwargs = {"mode": case["mode"]}
    if "@schema" in case["input"]:
        kwargs["load_schema"] = _make_schema_loader(case["dir"])
    result = await parse_maxi(case["input"], **kwargs)
    expected = case["expected"]
    if expected.get("record_validations"):
        comparable = _result_to_comparable(result)
        for rv in expected["record_validations"]:
            path = rv["path"]
            expected_value = rv["expected_value"]
            actual = _resolve_json_path(comparable, path)
            assert actual == expected_value, (
                f"[{case['id']}] {rv.get('description', path)}: "
                f"expected {expected_value!r}, got {actual!r}"
            )




@pytest.mark.asyncio
async def test_parse_schema_only():
    result = await parse_maxi("U:User(id:int|name|email)")
    assert len(result.schema.types) == 1
    assert len(result.records) == 0


@pytest.mark.asyncio
async def test_parse_schema_and_records():
    input_text = "U:User(id:int|name)\n###\nU(1|Alice)\nU(2|Bob)"
    result = await parse_maxi(input_text)
    assert len(result.schema.types) == 1
    assert len(result.records) == 2
    assert result.records[0].values[0] == 1
    assert result.records[0].values[1] == "Alice"


@pytest.mark.asyncio
async def test_parse_records_only():
    result = await parse_maxi("U(1|Alice)")


@pytest.mark.asyncio
async def test_parse_empty():
    result = await parse_maxi("")
    assert len(result.records) == 0
