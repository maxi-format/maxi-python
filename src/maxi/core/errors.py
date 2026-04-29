"""
Core MAXI error types.
"""

from __future__ import annotations

import enum


class MaxiErrorCode(enum.StrEnum):
    """MAXI spec Appendix B error codes."""

    UnsupportedVersionError = "E001"
    DuplicateTypeError = "E002"
    UnknownTypeError = "E003"
    UnknownDirectiveError = "E004"
    InvalidSyntaxError = "E005"
    SchemaMismatchError = "E006"
    TypeMismatchError = "E007"
    ConstraintViolationError = "E008"
    UnresolvedReferenceError = "E009"
    CircularInheritanceError = "E010"
    MissingRequiredFieldError = "E011"
    InvalidConstraintValueError = "E012"
    UndefinedParentError = "E013"
    ConstraintSyntaxError = "E014"
    ArraySyntaxError = "E015"
    DuplicateIdentifierError = "E016"
    UnsupportedBinaryFormatError = "E017"
    InvalidDefaultValueError = "E018"
    StreamError = "E019"
    SchemaLoadError = "E020"


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
