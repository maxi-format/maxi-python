# maxi-schema for Python

Python library for parsing and dumping **MAXI schema + records**.

Version: `0.1.0`

## Install

```bash
pip install maxi-format
```

## API overview

| Function | Description |
|---|---|
| `parse_maxi(input, **options)` | Parse MAXI text → `MaxiParseResult` (schema + raw records) |
| `stream_maxi(input, **options)` | Parse schema eagerly, yield records lazily via async iterator |
| `parse_maxi_as(input, class_map, **options)` | Parse + hydrate records into class instances |
| `parse_maxi_auto_as(input, classes, **options)` | Same, with alias inferred from `__maxi_schema__` |
| `dump_maxi(data, **options)` | Serialize objects / parse results → MAXI text |
| `dump_maxi_auto(objects, **options)` | Same, with schema inferred from `__maxi_schema__` |
| `define_maxi_schema(Cls, schema)` | Register a schema descriptor for a class |
| `get_maxi_schema(ClsOrInstance)` | Look up a registered schema descriptor |
| `MaxiModel` | Declarative base class for schema-annotated models |

## Quick start

### Parse

```python
import asyncio
from maxi import parse_maxi

input_text = """
U:User(id:int|name|email)
###
U(1|Julie|julie@maxi.org)
""".strip()

res = asyncio.run(parse_maxi(input_text))
print(res.records[0].values)  # [1, 'Julie', 'julie@maxi.org']
```

### Parse into class instances

```python
import asyncio
from maxi import parse_maxi_auto_as

class User:
    __maxi_schema__ = {
        "alias": "U",
        "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}, {"name": "email"}],
    }
    def __init__(self, id=None, name=None, email=None):
        self.id = id
        self.name = name
        self.email = email

result = asyncio.run(parse_maxi_auto_as(input_text, [User]))
user_instance = result.data["U"][0]

print(isinstance(user_instance, User))  # True
print(user_instance.name)               # 'Julie'
```

### Dump

Using `MaxiModel` for zero-config dumping:

```python
from maxi import dump_maxi_auto
from maxi.models import MaxiModel, IntField, StrField

class User(MaxiModel, alias="U"):
    id = IntField(id=True)
    name = StrField()

maxi = dump_maxi_auto([User(id=1, name="Julie")])
```

Or with explicit types via `dump_maxi`:

```python
from maxi import dump_maxi

maxi = dump_maxi([{"id": 1, "name": "Julie"}],
    default_alias="U",
    types=[{"alias": "U", "name": "User", "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}]}],
)
```

## Documentation

- **[docs/parser.md](docs/parser.md)** — full parser guide: `parse_maxi`, `stream_maxi`, `parse_maxi_as`, `parse_maxi_auto_as`, hydration, `MaxiModel`, options
- **[docs/dumper.md](docs/dumper.md)** — full dumper guide: `dump_maxi`, `dump_maxi_auto`, schema-annotated classes, references, inheritance, options

## MAXI format (quick reference)

```
U:User(id:int|name|email=unknown)   ← type definition
###                                  ← section separator
U(1|Julie|~)                         ← record  (~ = explicit null)
```

- Omitted trailing fields use their declared default value.
- See the [MAXI spec](https://github.com/maxi-format/maxi/blob/main/SPEC.md) for the full format definition.

## Test

Requires `pytest` and `pytest-asyncio`.

```bash
pip install pytest pytest-asyncio
pytest
```

## License

Released under the [MIT License](./LICENSE).
