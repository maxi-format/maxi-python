"""
Streaming parse API – ``stream_maxi``.

Parses the schema eagerly, then yields records one at a time via an async
iterator.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from maxi.core.errors import MaxiError, MaxiErrorCode
from maxi.core.types import MaxiParseResult, MaxiRecord
from maxi.internal.schema_parser import SchemaParser
from maxi.internal.record_parser import RecordParser
from maxi.api.parse import _split_sections

if TYPE_CHECKING:
    from maxi.core.types import MaxiSchema


class MaxiStreamResult:
    """Result of :func:`stream_maxi`.

    ``schema`` and ``warnings`` are available immediately.
    Records are yielded lazily via ``async for record in result: ...``
    """

    def __init__(
        self,
        schema: MaxiSchema,
        record_iterator: AsyncIterator[MaxiRecord],
        result: MaxiParseResult,
    ) -> None:
        self.schema = schema
        self.warnings = result.warnings
        self._iterator = record_iterator

    async def records(self) -> AsyncIterator[MaxiRecord]:
        async for record in self._iterator:
            yield record

    def __aiter__(self) -> AsyncIterator[MaxiRecord]:
        return self.records()


async def stream_maxi(
    input: str,
    *,
    mode: str = "lax",
    filename: str | None = None,
    load_schema: Callable[[str], str | Awaitable[str]] | None = None,
) -> MaxiStreamResult:
    """Parse schema eagerly, yield records lazily.

    Usage::

        result = await stream_maxi(text)
        print(result.schema)
        async for record in result:
            process(record)
    """
    result = MaxiParseResult()
    result.schema.mode = mode  # type: ignore[assignment]

    options: dict[str, Any] = {"filename": filename}
    if load_schema is not None:
        options["load_schema"] = load_schema

    schema_section, records_section = _split_sections(input)

    parser = SchemaParser(schema_section, result, options)
    await parser.parse()

    record_iter = _generate_records(records_section, result, options)
    return MaxiStreamResult(result.schema, record_iter, result)


async def _generate_records(
    records_text: str | None,
    result: MaxiParseResult,
    options: dict[str, Any],
) -> AsyncIterator[MaxiRecord]:
    if not records_text or not records_text.strip():
        return

    parser = RecordParser(records_text, result, options)
    text = records_text
    length = len(text)
    i = 0
    line_number = 1
    filename = options.get("filename")

    while i < length:
        ch = text[i]
        if ch == "\n":
            line_number += 1
            i += 1
            continue
        if ch in (" ", "\t", "\r"):
            i += 1
            continue
        if not (ch.isalpha() or ch == "_"):
            i += 1
            continue

        alias_start = i
        i += 1
        while i < length:
            c = text[i]
            if c.isalnum() or c in ("-", "_"):
                i += 1
            else:
                break
        alias = text[alias_start:i]

        while i < length and text[i] in (" ", "\t", "\r"):
            i += 1
        if i >= length or text[i] != "(":
            continue

        record_line = line_number
        i += 1
        values_start = i

        paren_depth = 1
        bracket_depth = 0
        brace_depth = 0
        in_string = False
        escape_next = False

        while i < length:
            c = text[i]
            if c == "\n":
                line_number += 1
            if escape_next:
                escape_next = False
                i += 1
                continue
            if in_string:
                if c == "\\":
                    escape_next = True
                elif c == '"':
                    in_string = False
                i += 1
                continue
            if c == '"':
                in_string = True
                i += 1
                continue
            if c == "(":
                paren_depth += 1
            elif c == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    break
            elif c == "[":
                bracket_depth += 1
            elif c == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif c == "{":
                brace_depth += 1
            elif c == "}":
                brace_depth = max(0, brace_depth - 1)
            i += 1

        if i >= length or text[i] != ")" or paren_depth != 0 or bracket_depth != 0 or brace_depth != 0:
            if bracket_depth != 0:
                raise MaxiError(
                    f"Malformed array: unmatched bracket in record '{alias}'",
                    MaxiErrorCode.ArraySyntaxError,
                    line=record_line,
                    filename=filename,
                )
            raise MaxiError(
                f"Unclosed record parentheses for '{alias}'",
                MaxiErrorCode.InvalidSyntaxError,
                line=record_line,
                filename=filename,
            )

        values_str = text[values_start:i]
        i += 1

        yield parser._parse_single_record(alias, values_str, record_line)
