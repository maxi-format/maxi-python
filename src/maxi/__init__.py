"""
maxi-schema — Python library for parsing and dumping MAXI schema + records.
"""

from maxi.api.dump import dump_maxi, dump_maxi_auto
from maxi.api.parse import parse_maxi, parse_maxi_as, parse_maxi_auto_as
from maxi.api.stream import MaxiStreamResult, stream_maxi
from maxi.core.errors import MaxiError, MaxiErrorCode
from maxi.core.registry import define_maxi_schema, get_maxi_schema, undefine_maxi_schema
from maxi.core.types import (
    MaxiFieldDef,
    MaxiHydrateResult,
    MaxiParseResult,
    MaxiRecord,
    MaxiSchema,
    MaxiTypeDef,
    ParsedConstraint,
)
from maxi.models import (
    ArrayField,
    BoolField,
    BytesField,
    DecimalField,
    EnumField,
    FloatField,
    IntField,
    MapField,
    MaxiModel,
    RefField,
    StrField,
)

__version__ = "1.0.0a1"

__all__ = [
    "MaxiSchema", "MaxiTypeDef", "MaxiFieldDef", "MaxiRecord",
    "MaxiParseResult", "MaxiHydrateResult", "MaxiStreamResult", "ParsedConstraint",
    "MaxiError", "MaxiErrorCode",
    "parse_maxi", "parse_maxi_as", "parse_maxi_auto_as",
    "dump_maxi", "dump_maxi_auto",
    "stream_maxi",
    "define_maxi_schema", "get_maxi_schema", "undefine_maxi_schema",
    "MaxiModel", "StrField", "IntField", "FloatField", "DecimalField",
    "BoolField", "BytesField", "ArrayField", "MapField", "EnumField", "RefField",
]
