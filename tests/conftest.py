"""Shared fixtures for maxi-python tests."""

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_LOCAL_TESTDATA = ROOT / "maxi-testdata" / "testdata"
_WORKSPACE_TESTDATA = ROOT.parent / "maxi-testdata" / "testdata"
TESTDATA_DIR = _LOCAL_TESTDATA if _LOCAL_TESTDATA.is_dir() else _WORKSPACE_TESTDATA


def _load_test_cases():
    """Discover all test cases from testdata directory."""
    if not TESTDATA_DIR.is_dir():
        return []
    cases = []
    for d in sorted(TESTDATA_DIR.iterdir()):
        if not d.is_dir():
            continue
        test_json = d / "test.json"
        in_maxi = d / "in.maxi"
        expected_json = d / "expected.json"
        if test_json.is_file() and in_maxi.is_file() and expected_json.is_file():
            meta = json.loads(test_json.read_text(encoding="utf-8"))
            # Skip parser_dependent error cases - these are optional for parsers
            if meta.get("category") == "error" and meta.get("parser_dependent"):
                continue
            cases.append({
                "id": meta.get("id", d.name),
                "title": meta.get("title", d.name),
                "category": meta.get("category", "valid"),
                "mode": meta.get("mode", "lax"),
                "input": in_maxi.read_text(encoding="utf-8"),
                "expected": json.loads(expected_json.read_text(encoding="utf-8")),
                "dir": d,
            })
    return cases


ALL_TEST_CASES = _load_test_cases()
VALID_CASES = [c for c in ALL_TEST_CASES if c["category"] == "valid"]
ERROR_CASES = [c for c in ALL_TEST_CASES if c["category"] == "error"]
WARNING_CASES = [c for c in ALL_TEST_CASES if c["category"] == "warning"]
