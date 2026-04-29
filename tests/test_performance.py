"""Performance comparison: MAXI vs JSON parsing and dumping.

Run with the default size (100 000 records) or override via env var:

    MAXI_BENCH_SIZE=1000000 pytest tests/test_performance.py -s -v
    MAXI_DUMP_BENCH_SIZE=1000000 pytest tests/test_performance.py -s -v
"""

import json
import os
import time

import pytest

from maxi.api.parse import parse_maxi
from maxi.api.dump import dump_maxi

DATA_SIZE = int(os.environ.get("MAXI_BENCH_SIZE", 100_000))
DUMP_SIZE = int(os.environ.get("MAXI_DUMP_BENCH_SIZE", 100_000))

def _build_maxi_string(count: int) -> str:
    parts = [
        "U:User(id:int|name|email:str@email|role:enum[admin,user]"
        "|createdAt:str@datetime|logins:int|active:bool)\n###\n"
    ]
    for i in range(1, count + 1):
        name = f"User {i}"
        email = f"user{i}@example.com"
        role = "admin" if i % 5 == 0 else "user"
        created_at = f"2023-10-27T10:00:{i % 60:02d}.000Z"
        logins = i % 10
        active = "true" if i % 2 == 0 else "false"
        parts.append(f"U({i}|{name}|{email}|{role}|{created_at}|{logins}|{active})\n")
    return "".join(parts)


def _build_json_string(count: int) -> str:
    parts = ["["]
    for i in range(1, count + 1):
        sep = "" if i == 1 else ","
        role = "admin" if i % 5 == 0 else "user"
        created_at = f"2023-10-27T10:00:{i % 60:02d}.000Z"
        active = "true" if i % 2 == 0 else "false"
        parts.append(
            f'{sep}{{"id":{i},"name":"User {i}","email":"user{i}@example.com",'
            f'"role":"{role}","createdAt":"{created_at}",'
            f'"logins":{i % 10},"active":{active}}}'
        )
    parts.append("]")
    return "".join(parts)


def _generate_users(count: int) -> list[dict]:
    return [
        {
            "id": i,
            "name": f"User {i}",
            "email": f"user{i}@example.com",
            "role": "admin" if i % 5 == 0 else "user",
            "createdAt": f"2023-10-27T10:00:{i % 60:02d}.000Z",
            "logins": i % 10,
            "active": i % 2 == 0,
        }
        for i in range(1, count + 1)
    ]


_MAXI_USER_TYPES = [
    {
        "alias": "U",
        "name": "User",
        "fields": [
            {"name": "id", "typeExpr": "int"},
            {"name": "name"},
            {"name": "email", "typeExpr": "str", "annotation": "email"},
            {"name": "role", "typeExpr": "enum[admin,user]"},
            {"name": "createdAt", "typeExpr": "str", "annotation": "datetime"},
            {"name": "logins", "typeExpr": "int"},
            {"name": "active", "typeExpr": "bool"},
        ],
    }
]


# ---------------------------------------------------------------------------
# Parse benchmark
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_performance_parse(capsys):
    """Parse benchmark: MAXI vs JSON for DATA_SIZE records."""
    print(f"\n--- Generating {DATA_SIZE:,} records for parse benchmark ---")

    maxi_string = _build_maxi_string(DATA_SIZE)
    json_string = _build_json_string(DATA_SIZE)

    print(f"MAXI size: {len(maxi_string) // 1024} KB")
    print(f"JSON size: {len(json_string) // 1024} KB")

    # Warmup
    await parse_maxi(_build_maxi_string(100))
    json.loads(_build_json_string(100))

    # MAXI parse
    t0 = time.perf_counter()
    maxi_result = await parse_maxi(maxi_string)
    maxi_ms = (time.perf_counter() - t0) * 1000
    maxi_rec_s = int(DATA_SIZE * 1000 / maxi_ms)
    print(f"MAXI parse time: {maxi_ms:.1f} ms  ({maxi_rec_s:,} rec/s)")

    # JSON parse
    t0 = time.perf_counter()
    json_result = json.loads(json_string)
    json_ms = (time.perf_counter() - t0) * 1000
    json_rec_s = int(DATA_SIZE * 1000 / json_ms)
    print(f"JSON parse time: {json_ms:.1f} ms  ({json_rec_s:,} rec/s)")

    ratio = maxi_ms / json_ms
    print(f"MAXI/JSON ratio: {ratio:.2f}x")

    assert len(maxi_result.records) == DATA_SIZE
    assert len(json_result) == DATA_SIZE


# ---------------------------------------------------------------------------
# Dump benchmark
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_performance_dump(capsys):
    """Dump benchmark: MAXI vs JSON for DUMP_SIZE records."""
    print(f"\n--- Generating {DUMP_SIZE:,} records for dump benchmark ---")
    users = _generate_users(DUMP_SIZE)

    # Warmup
    dump_maxi({"U": users[:10]}, types=_MAXI_USER_TYPES, collect_references=False)
    json.dumps(users[:10])

    # MAXI dump
    t0 = time.perf_counter()
    maxi_output = dump_maxi(
        {"U": users},
        types=_MAXI_USER_TYPES,
        collect_references=False,
    )
    maxi_ms = (time.perf_counter() - t0) * 1000
    maxi_rec_s = int(DUMP_SIZE * 1000 / maxi_ms)
    print(f"MAXI dump size: {len(maxi_output) // 1024} KB")
    print(f"MAXI dump time: {maxi_ms:.1f} ms  ({maxi_rec_s:,} rec/s)")

    # JSON dump
    t0 = time.perf_counter()
    json_output = json.dumps(users)
    json_ms = (time.perf_counter() - t0) * 1000
    json_rec_s = int(DUMP_SIZE * 1000 / json_ms)
    print(f"JSON dump size: {len(json_output) // 1024} KB")
    print(f"JSON dump time: {json_ms:.1f} ms  ({json_rec_s:,} rec/s)")

    ratio = maxi_ms / json_ms
    print(f"MAXI/JSON ratio: {ratio:.2f}x")

    assert "###" in maxi_output
    assert "U(" in maxi_output
    assert json_output.startswith("[")
    assert '"id"' in json_output
