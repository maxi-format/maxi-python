"""
Core MAXI error types.
"""

from __future__ import annotations

import enum


class MaxiErrorCode(enum.StrEnum):
    """MAXI spec Appendix B error codes."""

    # E1xx — Schema definition errors
    InvalidSyntaxError = "E101"
    DuplicateTypeError = "E102"
    UnknownDirectiveError = "E103"
    # E2xx — Type system errors
    UnknownTypeError = "E201"
    UndefinedParentError = "E202"
    CircularInheritanceError = "E203"
    UnresolvedReferenceError = "E204"
    DuplicateIdentifierError = "E205"
    # E3xx — Constraint errors
    ConstraintSyntaxError = "E301"
    InvalidConstraintValueError = "E302"
    ConstraintViolationError = "E303"
    ArraySyntaxError = "E304"
    # E4xx — Data record errors
    SchemaMismatchError = "E401"
    TypeMismatchError = "E402"
    MissingRequiredFieldError = "E403"
    InvalidDefaultValueError = "E404"
    UnsupportedBinaryFormatError = "E405"
    # E5xx — Data type errors
    EnumAliasError = "E501"
    # E6xx — IO / runtime errors
    UnsupportedVersionError = "E601"
    SchemaLoadError = "E602"
    StreamError = "E603"


class MaxiError(Exception):
    """Base exception for all MAXI parsing/dumping errors."""

    def __init__(
        self,
        message: str,
        code: MaxiErrorCode | str,
        *,
        line: int | None = None,
        column: int | None = None,
        filename: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.line = line
        self.column = column
        self.filename = filename
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        loc = ""
        if self.line is not None:
            loc = f" at line {self.line}"
            if self.column is not None:
                loc += f", column {self.column}"
        file = f" in {self.filename}" if self.filename else ""
        return f"MaxiError [{self.code}]{file}{loc}: {super().__str__()}"
