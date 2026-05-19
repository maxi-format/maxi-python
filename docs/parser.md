# MAXI Parser

This document covers everything about parsing MAXI text into structured data —
from raw records, through streaming, all the way to typed class instances
(object hydration).

---

## Table of Contents

1. [Overview](#overview)
2. [MAXI File Structure (Quick Recap)](#maxi-file-structure-quick-recap)
3. [`parse_maxi` — Full In-Memory Parse](#parse_maxi--full-in-memory-parse)
4. [`stream_maxi` — Streaming Parse](#stream_maxi--streaming-parse)
5. [Parse Result Shape](#parse-result-shape)
6. [Schema-Annotated Classes](#schema-annotated-classes)
7. [`parse_maxi_as` — Parse into Class Instances](#parse_maxi_as--parse-into-class-instances)
8. [`parse_maxi_auto_as` — Auto-Resolve Classes](#parse_maxi_auto_as--auto-resolve-classes)
9. [Reference Resolution during Hydration](#reference-resolution-during-hydration)
10. [Construction Strategies](#construction-strategies)
11. [`MaxiModel` — Declarative Schema Classes](#maximodel--declarative-schema-classes)
12. [Options Reference](#options-reference)
13. [Examples](#examples)

---

## Overview

The parser converts MAXI text into one of two output shapes:

| Function | Output |
|---|---|
| `parse_maxi` | `MaxiParseResult` — schema + raw records (positional values) |
| `stream_maxi` | `MaxiStreamResult` — schema immediately, then an async record iterator |
| `parse_maxi_as` | `MaxiHydrateResult` — records hydrated into class instances |
| `parse_maxi_auto_as` | Same as `parse_maxi_as`, but class → alias map inferred automatically |

---

## MAXI File Structure (Quick Recap)

```
U:User(id:int|name|email=unknown)    ← type definitions (schema section)
O:Order(id:int|user:U|total:decimal)
###                                   ← separator
U(1|Julie|julie@example.com)          ← records (data section)
O(100|1|49.99)
```

- Everything **above** `###` is the schema section (type defs, directives like `@maxi`, `@version`, `@schema`).
- Everything **below** `###` is the records section.
- If no `###` is present, the parser auto-detects whether the input is schema-only or records-only.

---

## `parse_maxi` — Full In-Memory Parse

```python
from maxi import parse_maxi

result = await parse_maxi(input)
```

Parses the full input at once. Returns a `MaxiParseResult` containing:
- `result.schema` — types, directives, imports
- `result.records` — list of `MaxiRecord` objects (positional values, schema-typed)
- `result.warnings` — recoverable issues found during parsing (type coercions, unknown types, constraint violations, etc.)

### What the parser does internally

1. **Split sections** at `###`
2. **Parse schema section** — type definitions, `@maxi`, `@version`, `@schema` imports (loaded via `load_schema`)
3. **Parse records section** — each record is matched to its type def; values are coerced to the declared type (`int`, `bool`, `decimal`, etc.)
4. **Build object registry** — if any field references another type, an internal object registry (alias → id → object) is built for reference validation
5. **Validate references** — unresolved references emit a warning or raise (depending on `allow_forward_references`)

---

## `stream_maxi` — Streaming Parse

For large files where you don't want to hold all records in memory at once.

```python
from maxi import stream_maxi

stream = await stream_maxi(input)

# Schema is fully available before iterating
fields = [f.name for f in stream.schema.get_type("U").fields]

# Iterate over records one by one
async for record in stream:
    print(record.alias, record.values)

# Or use the .records() async generator method explicitly
async for record in stream.records():
    ...
```

- The schema section is parsed **eagerly** and available immediately on the returned `MaxiStreamResult`.
- Records are yielded **lazily** one at a time as you iterate.
- `stream.warnings` accumulates warnings for the full session.

---

## Parse Result Shape

### `MaxiParseResult`

```python
result.schema    # MaxiSchema — parsed type definitions and directives
result.records   # list[MaxiRecord] — all records
result.warnings  # list[Warning] — { message, code, line }
```

### `MaxiRecord`

```python
record.alias       # 'U' — type alias
record.values      # [1, 'Julie', None] — positional values, schema-coerced
record.line_number # source line number
```

### `MaxiSchema`

```python
schema.getType('U')    # → MaxiTypeDef | None
schema.has_type('U')   # → bool
schema.types           # → dict[str, MaxiTypeDef]
schema.maxi_version    # → str (from @maxi directive)
schema.user_version    # → str | None (from @version directive)
schema.imports         # → list[str]
```

### `MaxiTypeDef`

```python
type_def.alias     # 'U'
type_def.name      # 'User'
type_def.parents   # ['P']
type_def.fields    # list[MaxiFieldDef]
```

### `MaxiFieldDef`

```python
field.name          # 'email'
field.type_expr     # 'str', 'int', 'U', 'O[]', etc.
field.annotation    # 'hex', 'base64', 'email', etc.
field.constraints   # list[ParsedConstraint]
field.default_value # 'unknown', 0, etc.
```

---

## Schema-Annotated Classes

Before using `parse_maxi_as` / `parse_maxi_auto_as`, your classes need a schema attached.

### Option A: `__maxi_schema__` class attribute (recommended for plain classes)

```python
class User:
    __maxi_schema__ = {
        "alias": "U",           # must match the alias in the MAXI file
        "name": "User",         # optional long name
        "fields": [
            {"name": "id",    "typeExpr": "int"},
            {"name": "name"},
            {"name": "email", "defaultValue": "unknown"},
        ],
    }

    def __init__(self, id=None, name=None, email=None):
        self.id    = id
        self.name  = name
        self.email = email
```

### Option B: `define_maxi_schema` for external / third-party classes

```python
from maxi import define_maxi_schema

define_maxi_schema(SomeExternalClass, {
    "alias": "E",
    "fields": [{"name": "id", "typeExpr": "int"}, {"name": "label"}],
})
```

Uses a `WeakMap`-style registry internally — does not modify the class.

### Option C: `MaxiModel` declarative base class

```python
from maxi.models import MaxiModel, IntField, StrField

class User(MaxiModel, alias="U", name="User"):
    id    = IntField(required=True, id=True)
    name  = StrField(min_length=1, max_length=50)
    email = StrField(annotation="email", default="unknown")
```

See [MaxiModel — Declarative Schema Classes](#maximodel--declarative-schema-classes) for full details.

### Schema descriptor fields

| Field | Type | Description |
|---|---|---|
| `alias` | `str` | **Required.** Short alias used in records, e.g. `U(...)` |
| `name` | `str` | Optional long name emitted in the type definition header |
| `parents` | `list[str]` | Optional parent aliases for inheritance |
| `fields` | `list[dict]` | Field list — order defines serialization / deserialization order |

Each field descriptor:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | **Required.** Field name |
| `typeExpr` | `str` | Type: `int`, `str`, `bool`, `decimal`, `float`, `bytes`, or another alias, `OtherAlias[]` |
| `annotation` | `str` | e.g. `hex`, `base64`, `email` |
| `constraints` | `list` | e.g. `[{"type": "required"}]`, `[{"type": "id"}]` |
| `defaultValue` | `any` | Applied when field is omitted from record |

---

## `parse_maxi_as` — Parse into Class Instances

```python
from maxi import parse_maxi_as

result = await parse_maxi_as(input, class_map)
```

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `input` | `str` | MAXI text to parse |
| `class_map` | `dict[str, type]` | Maps each alias to the constructor to instantiate |
| `allow_additional_fields` | `str` | `'ignore'` (default), `'warning'`, or `'error'` |
| `allow_missing_fields` | `str` | `'null'` (default), `'warning'`, or `'error'` |
| `allow_type_coercion` | `str` | `'coerce'` (default), `'warning'`, or `'error'` |
| `allow_constraint_violations` | `str` | `'warning'` (default) or `'error'` |
| `allow_forward_references` | `bool` | `True` (default) |
| `allow_unknown_types` | `str` | `'warning'` (default), `'ignore'`, or `'error'` |
| `filename` | `str \| None` | Used in error messages for diagnostics |
| `load_schema` | `callable \| None` | Resolver for `@schema:` import directives |

### Return value — `MaxiHydrateResult`

```python
result.data      # dict[str, list[Any]] — { alias: [instances] }
result.schema    # MaxiSchema — parsed schema (same as parse_maxi)
result.warnings  # list[Warning]
```

Only aliases present in `class_map` are hydrated. Records with other aliases are silently skipped.

---

## `parse_maxi_auto_as` — Auto-Resolve Classes

Convenience variant — pass a list of classes instead of an alias map. Each class must have `__maxi_schema__` or be registered via `define_maxi_schema`.

```python
from maxi import parse_maxi_auto_as

result = await parse_maxi_auto_as(input, [User, Order])
```

Internally builds `{"U": User, "O": Order}` from each class's `schema["alias"]`, then calls `parse_maxi_as`.

---

## Reference Resolution during Hydration

After all records are hydrated into instances, the hydrator performs a **second pass** to resolve cross-reference fields.

A field is a cross-reference when its `typeExpr` points to another alias in the schema (not a primitive like `int`, `str`, etc.).

**Example:**

```
U:User(id:int|name)
O:Order(id:int|user:U|total:decimal)
###
U(1|Julie)
O(100|1|49.99)
```

After hydration, `order.user` will be the actual `User` instance for `id=1`, not the scalar `1`.

### What happens step by step

1. All `U` records are hydrated into `User` instances and indexed by their id.
2. All `O` records are hydrated into `Order` instances.
3. The hydrator walks each `Order`'s `user` field — its `typeExpr` is `U`, a known alias.
4. The scalar value `1` is looked up in the `User` instance registry → the `User` instance is found.
5. `order.user` is replaced with the actual `User` instance.

### Forward references

Forward references work naturally because reference resolution is a **second pass** over all already-parsed records. An `Order` that appears before the `User` it references will still resolve correctly.

### Unresolved references

If a referenced id is not found among the hydrated instances, the field **stays as the original scalar value**. A warning is also emitted by the underlying `parse_maxi` call.

---

## Construction Strategies

`parse_maxi_as` tries three strategies in order to construct each instance:

| Strategy | When it applies |
|---|---|
| `cls(**field_map)` | Constructor accepts keyword arguments — **most common** |
| `cls()` + `setattr` for each field | Zero-arg constructor or constructor that ignores kwargs |
| `object.__new__(cls)` + `setattr` | Constructor raises even with no args |

The first strategy is verified by checking that the first expected field actually landed on the instance. If the constructor accepted the call but ignored the kwargs (positional-args pattern), the fallback kicks in automatically.

---

## `MaxiModel` — Declarative Schema Classes

`MaxiModel` is a declarative base class that auto-builds `__maxi_schema__` from field descriptors at class definition time — similar to Django models or dataclasses.

```python
from maxi.models import MaxiModel, IntField, StrField, RefField

class User(MaxiModel, alias="U", name="User"):
    id    = IntField(required=True, id=True)
    name  = StrField(min_length=1, max_length=50)
    email = StrField(annotation="email", default="unknown")

class Order(MaxiModel, alias="O", name="Order"):
    id    = IntField(required=True, id=True)
    user  = RefField(User)
    total = DecimalField(min=0)
```

### `MaxiModel` class parameters

| Parameter | Type | Description |
|---|---|---|
| `alias` | `str` | **Required.** Short alias, e.g. `"U"` |
| `name` | `str` | Optional long name for the type definition header |
| `parents` | `list[str]` | Optional parent aliases for inheritance |

### Available field types

| Class | MAXI type | Key options |
|---|---|---|
| `StrField` | `str` | `min_length`, `max_length`, `pattern`, `annotation`, `default` |
| `IntField` | `int` | `min`, `max`, `default` |
| `FloatField` | `float` | `min`, `max`, `default` |
| `DecimalField` | `decimal` | `min`, `max`, `precision`, `default` |
| `BoolField` | `bool` | `default` |
| `BytesField` | `bytes` | `mime`, `min_length`, `max_length`, `annotation`, `default` |
| `ArrayField` | `T[]` | `item_type`, `min_items`, `max_items`, `element_constraints`, `default` |
| `MapField` | `map<K,V>` | `key_type`, `value_type`, `min_keys`, `max_keys`, `default` |
| `EnumField` | `enum(...)` | `values`, `base_type`, `default` |
| `RefField` | alias | `ref_class_or_alias`, `required`, `default` |

All field types support `required=True` (adds `!` constraint) and `id=True` (marks as identifier field).

---

## Options Reference

| Option | Type | Default | Description |
|---|---|---|---|
| `allow_additional_fields` | `str` | `"ignore"` | Extra fields beyond schema definition: `"ignore"`, `"warning"`, `"error"` |
| `allow_missing_fields` | `str` | `"null"` | Missing required fields — fill with null or reject: `"null"`, `"warning"`, `"error"` |
| `allow_type_coercion` | `str` | `"coerce"` | Type mismatches — coerce or reject: `"coerce"`, `"warning"`, `"error"` |
| `allow_constraint_violations` | `str` | `"warning"` | Constraint violations: `"warning"`, `"error"` |
| `allow_forward_references` | `bool` | `True` | Allow references to records not yet seen |
| `allow_unknown_types` | `str` | `"warning"` | Records with an unrecognised type alias: `"ignore"`, `"warning"`, `"error"` |
| `filename` | `str \| None` | `None` | Used in error/warning messages for better diagnostics |
| `load_schema` | `callable \| None` | `None` | Resolver for `@schema:` import directives — called with the path string, returns the schema text (sync or async) |

---

## Examples

### 1. Basic `parse_maxi` — raw records

```python
import asyncio
from maxi import parse_maxi

input_text = """
U:User(id:int|name|email=unknown)
###
U(1|Julie|julie@example.com)
U(2|Matt)
""".strip()

result = asyncio.run(parse_maxi(input_text))

print(result.records[0].alias)   # 'U'
print(result.records[0].values)  # [1, 'Julie', 'julie@example.com']
print(result.records[1].values)  # [2, 'Matt', 'unknown']  ← default filled in
```

---

### 2. `stream_maxi` — large files

```python
from maxi import stream_maxi

async def main():
    stream = await stream_maxi(input_text)

    # Schema is available immediately — no need to wait for records
    fields = [f.name for f in stream.schema.get_type("U").fields]
    print(fields)  # ['id', 'name', 'email']

    # Stream records one at a time
    async for record in stream:
        print(record.values)
```

---

### 3. `parse_maxi_as` — hydrate into class instances

```python
from maxi import parse_maxi_as

class User:
    __maxi_schema__ = {
        "alias": "U",
        "name": "User",
        "fields": [
            {"name": "id",    "typeExpr": "int"},
            {"name": "name"},
            {"name": "email"},
        ],
    }

    def __init__(self, id=None, name=None, email=None):
        self.id    = id
        self.name  = name
        self.email = email

input_text = """
U:User(id:int|name|email)
###
U(1|Julie|julie@example.com)
U(2|Matt|matt@example.com)
""".strip()

result = await parse_maxi_as(input_text, {"U": User})

print(isinstance(result.data["U"][0], User))  # True
print(result.data["U"][0].name)               # 'Julie'
```

---

### 4. `parse_maxi_auto_as` — zero-config with `__maxi_schema__`

```python
from maxi import parse_maxi_auto_as

# Alias is read from User.__maxi_schema__["alias"] automatically
result = await parse_maxi_auto_as(input_text, [User, Order])

print(isinstance(result.data["U"][0], User))   # True
print(isinstance(result.data["O"][0], Order))  # True
```

---

### 5. Cross-reference fields resolved to instances

```python
class User:
    __maxi_schema__ = {
        "alias": "U",
        "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}],
    }
    def __init__(self, id=None, name=None):
        self.id = id; self.name = name

class Order:
    __maxi_schema__ = {
        "alias": "O",
        "fields": [
            {"name": "id",    "typeExpr": "int"},
            {"name": "user",  "typeExpr": "U"},        # ← reference to User
            {"name": "total", "typeExpr": "decimal"},
        ],
    }
    def __init__(self, id=None, user=None, total=None):
        self.id = id; self.user = user; self.total = total

input_text = """
U:User(id:int|name)
O:Order(id:int|user:U|total:decimal)
###
U(1|Julie)
O(100|1|49.99)
""".strip()

result = await parse_maxi_auto_as(input_text, [User, Order])

order = result.data["O"][0]
print(isinstance(order.user, User))  # True  ← not just the scalar 1
print(order.user.name)               # 'Julie'
```

---

### 6. Forward references

```python
input_text = """
U:User(id:int|name)
O:Order(id:int|user:U|total:decimal)
###
O(100|1|49.99)   # Order before the User it references
U(1|Julie)
""".strip()

result = await parse_maxi_auto_as(input_text, [User, Order])

# Forward reference resolves correctly because resolution is a second pass
print(isinstance(result.data["O"][0].user, User))  # True
```

---

### 7. External schema via `@schema` import

```python
from maxi import parse_maxi_as

input_text = """
@schema:schemas/users.maxi
###
U(1|Julie)
""".strip()

def load_schema(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

result = await parse_maxi_as(input_text, {"U": User}, load_schema=load_schema)
```

---

### 8. Strict-style validation — raises on schema violations

Use `allow_additional_fields="error"` to reject records with extra fields:

```python
input_text = """
U:User(id:int|name)
###
U(1|Julie|extra-field-not-in-schema)
""".strip()

# Raises MaxiError with code SchemaMismatchError
await parse_maxi(input_text, allow_additional_fields="error")
```

---

### 9. `define_maxi_schema` for classes you don't own

```python
from maxi import define_maxi_schema, parse_maxi_auto_as
from some_library import ExternalProduct

# Third-party class — can't add __maxi_schema__
define_maxi_schema(ExternalProduct, {
    "alias": "P",
    "fields": [
        {"name": "id",    "typeExpr": "int"},
        {"name": "title"},
        {"name": "price", "typeExpr": "decimal"},
    ],
})

result = await parse_maxi_auto_as(maxi_text, [ExternalProduct])
print(isinstance(result.data["P"][0], ExternalProduct))  # True
```

---

### 10. `MaxiModel` declarative classes

```python
from maxi.models import MaxiModel, IntField, StrField, DecimalField, RefField
from maxi import parse_maxi_auto_as

class User(MaxiModel, alias="U", name="User"):
    id    = IntField(required=True, id=True)
    name  = StrField(min_length=1)
    email = StrField(annotation="email", default="unknown")

class Order(MaxiModel, alias="O", name="Order"):
    id    = IntField(required=True, id=True)
    user  = RefField(User)
    total = DecimalField(min=0)

input_text = """
U:User(id:int|name|email=unknown)
O:Order(id:int|user:U|total:decimal)
###
U(1|Julie)
O(100|1|49.99)
""".strip()

result = await parse_maxi_auto_as(input_text, [User, Order])

user = result.data["U"][0]
print(user.name)                   # 'Julie'
print(user.email)                  # 'unknown'  ← default applied

order = result.data["O"][0]
print(isinstance(order.user, User))  # True
print(order.total)                   # 49.99
```

---

### 10. Enum value aliases

Enum fields may use short aliases as wire tokens. The parser always returns the full semantic value.

```python
from maxi import parse_maxi

input_text = """
U:User(id:int|name|role:enum[a:admin,e:editor,v:viewer])
###
U(1|Alice|a)
U(2|Bob|v)
""".strip()

result = await parse_maxi(input_text)

print(result.records[0].values[2])  # 'admin': alias 'a' expanded
print(result.records[1].values[2])  # 'viewer': alias 'v' expanded
```

`enum<int>` aliases work the same way — the parsed value is always the integer:

```python
input_text = """
D:Device(id:int|name|state:enum<int>[O:900,I:910,R:1000,E:999])
###
D(1|sensor-A|R)
""".strip()

result = await parse_maxi(input_text)

print(result.records[0].values[2])  # 1000: alias 'R' expanded to int
```

Wire tokens that are neither a declared alias nor the full value trigger a constraint violation (E303).
