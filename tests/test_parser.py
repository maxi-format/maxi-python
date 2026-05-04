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
    in_data_tree = parts[0] in ("objects", "records")
    current = obj

    # Track schema context for follow_references
    current_type_name = None  # the type name we're currently within (in objects tree)
    prev_field_name = None    # the field name used to produce current value (for ref following)

    for i, p in enumerate(parts):
        if current is None:
            return None

        # Track type name when entering objects/<TypeName>
        if i == 1 and parts[0] == "objects":
            current_type_name = p

        if isinstance(current, list):
            try:
                current = current[int(p)]
            except (IndexError, ValueError):
                if in_data_tree:
                    return None
                raise IndexError(p)
        elif isinstance(current, dict):
            if p in current:
                current = current[p]
            elif in_data_tree:
                return None
            else:
                raise KeyError(p)
        elif follow_references and isinstance(current, (int, float, str)) and parts[0] == "objects":
            # Use schema to find target type for this reference field
            # prev_field_name is the field that produced the current scalar value
            objects = obj.get("objects", {})
            resolved = None

            if schema and current_type_name and prev_field_name:
                type_alias = _find_type_alias_by_name(schema, current_type_name)
                td = schema.get_type(type_alias) if type_alias else None
                if td:
                    field = next((f for f in td.fields if f.name == prev_field_name), None)
                    raw_field_type = field.type_expr if field else None
                    if raw_field_type and raw_field_type.endswith("[]"):
                        raw_field_type = raw_field_type[:-2].strip()
                    if raw_field_type:
                        target_alias = getattr(schema, "resolve_type_alias", lambda x: x)(raw_field_type) or raw_field_type
                        target_td = schema.get_type(target_alias)
                        target_type_name = (target_td.name or target_td.alias) if target_td else None
                        if target_type_name:
                            candidate = objects.get(target_type_name, {}).get(str(current))
                            if candidate is not None:
                                resolved = candidate
                                current_type_name = target_type_name

            if resolved is None:
                # Fallback: try all types, prefer the one with the next field
                for type_name, id_map in objects.items():
                    candidate = id_map.get(str(current))
                    if candidate is not None and isinstance(candidate, dict) and p in candidate:
                        resolved = candidate
                        current_type_name = type_name
                        break
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

        # Update prev_field_name AFTER processing this step (for next iteration)
        if not p.isdigit():
            prev_field_name = p
    return current


def _find_type_alias_by_name(schema, type_name):
    """Find type alias by type name (name or alias)."""
    for alias, td in schema.types.items():
        if (td.name or td.alias) == type_name:
            return alias
    return None


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

    def _get_inline_id(v):
        """Get the id from an inline object dict, or None."""
        if isinstance(v, dict) and "id" in v:
            return v["id"]
        return None

    def _project_value_for_registry(value, td_fields):
        """Convert a record value dict to the registry representation:
        - inline object fields become their id
        - array-of-inline-objects fields become arrays of ids
        """
        projected = {}
        for field in td_fields:
            out_name = _field_output_name(field)
            v = value.get(out_name)
            if isinstance(v, dict) and "id" in v:
                projected[out_name] = v["id"]
            elif isinstance(v, list):
                projected[out_name] = [
                    item["id"] if (isinstance(item, dict) and "id" in item) else item
                    for item in v
                ]
            else:
                projected[out_name] = v
        return projected

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

        # Build objects map - find id field index, fallback to 0 if none found
        id_idx = td.get_id_field_index()
        if id_idx < 0 and td.fields:
            id_idx = 0  # fallback: use first field as key (JS behavior)
        if id_idx >= 0 and id_idx < len(record.values):
            id_val = record.values[id_idx]
            # If the id value is itself an inline object, use its id
            if isinstance(id_val, dict) and "id" in id_val:
                id_val = id_val["id"]
            if id_val is not None:
                objects_out.setdefault(type_name, {})[str(id_val)] = _project_value_for_registry(value, td.fields)

        # Also index inline objects found in reference fields
        for i, field in enumerate(td.fields):
            v = record.values[i] if i < len(record.values) else None
            if isinstance(v, dict) and "id" in v and field.type_expr:
                ref_td = schema.get_type(field.type_expr)
                if ref_td:
                    ref_type_name = ref_td.name or ref_td.alias
                    ref_id = v.get("id")
                    if ref_id is not None:
                        if ref_type_name not in objects_out:
                            objects_out[ref_type_name] = {}
                        if str(ref_id) not in objects_out[ref_type_name]:
                            objects_out[ref_type_name][str(ref_id)] = v
            # Also index inline objects in arrays
            elif isinstance(v, list) and field.type_expr:
                elem_type = field.type_expr
                if elem_type.endswith("[]"):
                    elem_type = elem_type[:-2].strip()
                ref_td = schema.get_type(elem_type)
                if ref_td:
                    ref_type_name = ref_td.name or ref_td.alias
                    for item in v:
                        if isinstance(item, dict) and "id" in item:
                            item_id = item["id"]
                            if item_id is not None:
                                if ref_type_name not in objects_out:
                                    objects_out[ref_type_name] = {}
                                if str(item_id) not in objects_out[ref_type_name]:
                                    objects_out[ref_type_name][str(item_id)] = item

    return {"records": records_out, "objects": objects_out, "_schema": result.schema}



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    VALID_CASES,
    ids=[c["id"] for c in VALID_CASES],
)
async def test_valid_case(case):
    """Parse a valid MAXI document and validate record/object assertions."""
    expected = case["expected"]
    # Convert camelCase parserOptions to snake_case kwargs
    opts = case.get("parser_options", {})
    kwargs = {}
    for k, v in opts.items():
        snake = "".join(["_" + c.lower() if c.isupper() else c for c in k]).lstrip("_")
        kwargs[snake] = v
    if "@schema" in case["input"]:
        kwargs["load_schema"] = _make_schema_loader(case["dir"])
    result = await parse_maxi(case["input"], **kwargs)

    comparable = _result_to_comparable(result)

    for rv in expected.get("record_validations", []):
        path = rv["path"]
        expected_value = rv["expected_value"]
        follow = rv.get("follow_references", False)
        try:
            actual = _resolve_json_path(comparable, path, follow_references=follow, schema=comparable.get("_schema"))
        except (KeyError, IndexError, TypeError):
            if expected_value is None:
                actual = None
            else:
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
            actual = _resolve_json_path(comparable, path, follow_references=follow, schema=comparable.get("_schema"))
        except (KeyError, IndexError, TypeError):
            if expected_value is None:
                actual = None
            else:
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
    # Convert camelCase parserOptions to snake_case kwargs
    opts = case.get("parser_options", {})
    kwargs = {}
    for k, v in opts.items():
        snake = "".join(["_" + c.lower() if c.isupper() else c for c in k]).lstrip("_")
        kwargs[snake] = v
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
    # Convert camelCase parserOptions to snake_case kwargs
    opts = case.get("parser_options", {})
    kwargs = {}
    for k, v in opts.items():
        snake = "".join(["_" + c.lower() if c.isupper() else c for c in k]).lstrip("_")
        kwargs[snake] = v
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
