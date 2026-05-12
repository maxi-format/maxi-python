# MAXI Dumper

The `dump_maxi` function serializes Python objects, lists, or parse results back into MAXI text format. This document explains how it works, what schema input is required, and how references and inline objects are handled.

---

## Table of Contents

1. [Overview](#overview)
2. [Input Modes](#input-modes)
3. [Schema Input](#schema-input)
4. [Schema-Annotated Classes](#schema-annotated-classes)
5. [Auto-Dump: `dump_maxi_auto`](#auto-dump-dump_maxi_auto)
6. [Reference Collection](#reference-collection)
7. [Inline Objects vs. References](#inline-objects-vs-references)
8. [Inheritance](#inheritance)
9. [Options Reference](#options-reference)
10. [Examples](#examples)

---

## Overview

```python
from maxi import dump_maxi

maxi = dump_maxi(data, default_alias="U", types=[...])
```

`dump_maxi` accepts data in several formats and optional configuration directly as keyword arguments. It emits a MAXI string that may contain:

- Directives (`@version`, `@schema`)
- Type definitions (schema section)
- A `###` separator
- Records (data section)

---

## Input Modes

`dump_maxi` detects the input shape and routes to the appropriate internal path:

| Input shape | Behavior |
|---|---|
| `MaxiParseResult` (parse result) | Round-trip path — re-emits schema and records exactly as parsed |
| `list` of objects | Requires `default_alias`; type info from `types` |
| Single `dict` | Requires `default_alias`; wrapped into a one-element list |
| `dict[str, list]` map | Each key is a record alias; type info from `types` |

### Round-trip (parse result)

If you pass the result of `parse_maxi(...)` directly, the dumper re-emits:
- The schema (types, directives, imports)
- All records in order, using the parsed values directly

```python
from maxi import parse_maxi, dump_maxi

result = await parse_maxi(input_text)
round_tripped = dump_maxi(result)
```

### Plain objects

For regular Python dicts or class instances, the dumper needs a schema from `types` to:
- Determine field order
- Emit type definitions
- Handle typed references and inline objects

---

## Schema Input

The dumper does **not** infer schema from object shapes. You must supply it explicitly through the `types` parameter.

`types` can be a **list** or a **`dict`** of type descriptors:

```python
{
    "alias":   "U",           # short alias used in records, e.g. U(...)
    "name":    "User",        # optional long name for type definition header
    "parents": ["P"],         # optional parent aliases for inheritance
    "fields": [
        {"name": "id",    "typeExpr": "int",     "constraints": [{"type": "id"}]},
        {"name": "name"},
        {"name": "email", "defaultValue": "unknown"},
    ]
}
```

Each field can have:
- `name` — field name (required)
- `typeExpr` — type string, e.g. `int`, `str`, `bool`, `decimal`, `bytes`, `OtherAlias`, `OtherAlias[]`
- `annotation` — e.g. `hex` for bytes fields
- `constraints` — e.g. `[{"type": "required"}]`, `[{"type": "id"}]`
- `elementConstraints` — constraints applied to individual array elements (for `T[]` fields)
- `defaultValue` — used in type definition and when trimming trailing empty fields

### External schema file

If you have an external `.maxi` schema file, you can reference it instead of embedding types:

```python
dump_maxi(data, default_alias="U", schema_file="schemas/users.maxi", include_types=False)
# Output:
# @schema:schemas/users.maxi
# ###
# U(1|Julie)
```

---

## Schema-Annotated Classes

Instead of passing `types` manually every time, you can attach schema metadata directly to your classes and let the dumper discover it automatically via `dump_maxi_auto`.

### Option A: `__maxi_schema__` class attribute (recommended)

```python
class User:
    __maxi_schema__ = {
        "alias": "U",        # short alias used in records
        "name": "User",      # optional long name for the type definition header
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

When you can't modify the class (e.g. it's from a library):

```python
from maxi import define_maxi_schema

define_maxi_schema(SomeExternalClass, {
    "alias": "E",
    "fields": [{"name": "id", "typeExpr": "int"}, {"name": "label"}],
})
```

### Option C: `MaxiModel` declarative base class

```python
from maxi.models import MaxiModel, IntField, StrField

class User(MaxiModel, alias="U", name="User"):
    id    = IntField(required=True, id=True)
    name  = StrField(min_length=1, max_length=50)
    email = StrField(default="unknown")
```

`MaxiModel` auto-builds `__maxi_schema__` at class definition time.

### Schema descriptor fields

| Field | Type | Description |
|---|---|---|
| `alias` | `str` | **Required.** Short alias, e.g. `"U"` |
| `name` | `str` | Optional long name emitted in the type def header |
| `parents` | `list[str]` | Optional parent aliases for inheritance |
| `fields` | `list[dict]` | Field list — order defines serialization order |

Each field descriptor:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | **Required.** |
| `typeExpr` | `str` | `int`, `str`, `bool`, `decimal`, `bytes`, `OtherAlias`, `OtherAlias[]` |
| `annotation` | `str` | e.g. `hex`, `base64` |
| `constraints` | `list` | e.g. `[{"type": "required"}]`, `[{"type": "id"}]` |
| `defaultValue` | `any` | Emitted in type def and used when trimming trailing empty fields |

---

## Auto-Dump: `dump_maxi_auto`

When your classes have `__maxi_schema__` (or are registered via `define_maxi_schema`),
use `dump_maxi_auto` instead of `dump_maxi` — no `types` or `default_alias` needed.

```python
from maxi import dump_maxi_auto

# List of instances — alias resolved from the class schema
maxi = dump_maxi_auto([User(id=1, name="Julie")])

# Multi-type dict
maxi = dump_maxi_auto({
    "U": [User(id=1, name="Julie")],
    "O": [Order(id=100, total=49.99)],
})
```

### How schema collection works

1. For each object in the input, `dump_maxi_auto` calls `get_maxi_schema(obj)` to retrieve the schema.
2. It then recurses into all typed nested fields to collect schemas for referenced types
   (e.g. an `Address` nested inside a `Customer` is picked up automatically).
3. All collected schemas are merged with any `types` you supply (caller wins on conflict).
4. The merged types are forwarded to the existing `dump_maxi` pipeline — no logic duplication.

### Mixing with manual `types`

You can override or extend the auto-collected types:

```python
dump_maxi_auto(users, types=[
    # Override the User schema with a customized one
    {"alias": "U", "name": "CustomUser", "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}]},
])
```

All `dump_maxi` options (`multiline`, `include_types`, `collect_references`, `schema_file`, etc.)
are supported and forwarded unchanged.

---

## Reference Collection

When `collect_references=True` (the default), the dumper automatically promotes nested objects into top-level records — if the nested type has an `id` field in its schema.

**How it works:**

1. For each object to dump, the dumper walks all fields that have a typed `typeExpr` pointing to another type.
2. If that nested type has an `id` field and the nested object has a value for it, the object is promoted to its own top-level record.
3. In the parent record, the field value is replaced with just the `id`.

This happens iteratively — deeply nested objects are also promoted.

**When `collect_references=False`** — nested typed objects are serialized inline as `(val1|val2|...)` regardless of whether they have an id.

---

## Inline Objects vs. References

Consider a `Customer` with a `shipping_address` field of type `Address`:

| Case | Output |
|---|---|
| `Address` has an `id` field, `collect_references=True` (default) | Customer record stores the address id; a separate `A(...)` record is emitted |
| `Address` has an `id` field, `collect_references=False` | Customer record stores the address inline: `(A1\|123 Main\|NYC)` |
| `Address` has **no** `id` field | Always inlined as `(val1\|val2)` |

---

## Inheritance

If a type has `parents`, the dumper resolves inherited fields before serializing. Parent fields are prepended to the type's own fields, in order of declaration, with duplicates skipped.

This resolution happens once at the start of `dump_maxi` via `_resolve_inheritance_for_dump`.

---

## Options Reference

| Option | Type | Default | Description |
|---|---|---|---|
| `default_alias` | `str` | — | Required when input is a list or single object |
| `types` | `list \| dict` | — | Type definitions used for field order, type defs, and references |
| `include_types` | `bool` | `True` | Whether to emit type definitions above `###` |
| `schema_file` | `str` | — | Emit `@schema:<path>` import directive |
| `version` | `str` | — | Emit `@version:<x>` if not `1.0.0` |
| `multiline` | `bool` | `False` | Pretty-print type defs and records across multiple lines |
| `collect_references` | `bool` | `True` | Promote nested typed objects with an `id` into top-level records |

---

## Examples

### 1. List of dicts with inline type definitions

```python
from maxi import dump_maxi

users = [
    {"id": 1, "name": "Julie"},
    {"id": 2, "name": "Matt", "email": None},
]

maxi = dump_maxi(users, default_alias="U", types=[
    {
        "alias": "U",
        "name": "User",
        "fields": [
            {"name": "id",    "typeExpr": "int"},
            {"name": "name"},
            {"name": "email", "defaultValue": "unknown"},
        ],
    },
])
```

Output:
```
U:User(id:int|name|email=unknown)
###
U(1|Julie)
U(2|Matt|~)
```

Note: `email` is omitted from the first record because it matches the trailing empty field (no `email` key on the object). The second record has `~` (explicit null).

---

### 2. Alias map — multiple types

```python
data = {
    "U": [{"id": 1, "name": "Julie"}],
    "O": [{"id": 100, "user_id": 1, "total": 49.99}],
}

maxi = dump_maxi(data, types=[
    {
        "alias": "U",
        "name": "User",
        "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}],
    },
    {
        "alias": "O",
        "name": "Order",
        "fields": [
            {"name": "id",      "typeExpr": "int"},
            {"name": "user_id", "typeExpr": "int"},
            {"name": "total",   "typeExpr": "decimal"},
        ],
    },
])
```

Output:
```
U:User(id:int|name)
O:Order(id:int|user_id:int|total:decimal)
###
U(1|Julie)
O(100|1|49.99)
```

---

### 3. Nested referenced objects (`collect_references=True`)

```python
address = {"id": "A1", "street": "123 Main St", "city": "NYC"}
customers = [{"id": "C1", "name": "John", "shipping_address": address}]

maxi = dump_maxi(customers, default_alias="C", types=[
    {
        "alias": "C",
        "name": "Customer",
        "fields": [
            {"name": "id"},
            {"name": "name"},
            {"name": "shipping_address", "typeExpr": "A"},
        ],
    },
    {
        "alias": "A",
        "name": "Address",
        "fields": [{"name": "id"}, {"name": "street"}, {"name": "city"}],
    },
])
```

Output:
```
C:Customer(id|name|shipping_address:A)
A:Address(id|street|city)
###
C(C1|John|A1)
A(A1|"123 Main St"|NYC)
```

The `shipping_address` field is replaced with just `A1` (the id), and a separate `A(...)` record is emitted.

---

### 4. Nested inline objects (`collect_references=False`)

Same data as above but with `collect_references=False`:

```python
maxi = dump_maxi(customers,
    default_alias="C",
    types=[...],  # same as above
    collect_references=False,
)
```

Output:
```
C:Customer(id|name|shipping_address:A)
A:Address(id|street|city)
###
C(C1|John|(A1|"123 Main St"|NYC))
```

The address is now inlined inside the customer record.

---

### 5. Inline arrays of typed objects

```python
customers = [{
    "id": "C1",
    "name": "John",
    "orders": [
        {"order_id": 101, "total": 49.99},
        {"order_id": 102, "total": 150.0},
    ],
}]

maxi = dump_maxi(customers, default_alias="C", types=[
    {
        "alias": "C",
        "name": "Customer",
        "fields": [
            {"name": "id"},
            {"name": "name"},
            {"name": "orders", "typeExpr": "O[]"},
        ],
    },
    {
        "alias": "O",
        "name": "Order",
        "fields": [
            {"name": "order_id", "typeExpr": "int"},
            {"name": "total",    "typeExpr": "decimal"},
        ],
    },
])
```

Output:
```
C:Customer(id|name|orders:O[])
O:Order(order_id:int|total:decimal)
###
C(C1|John|[(101|49.99),(102|150)])
```

Order objects have no `id` field, so they are always inlined as `(val|val)` tuples in a MAXI array `[...]`.

---

### 6. Inheritance

```python
data = {
    "E": [{"id": 1, "name": "Alice", "department": "Engineering"}],
}

maxi = dump_maxi(data, types=[
    {
        "alias": "P",
        "name": "Person",
        "fields": [{"name": "id", "typeExpr": "int"}, {"name": "name"}],
    },
    {
        "alias": "E",
        "name": "Employee",
        "parents": ["P"],
        "fields": [{"name": "department"}],
    },
])
```

Output:
```
P:Person(id:int|name)
E:Employee<P>(department)
###
E(1|Alice|Engineering)
```

The `Employee` record emits all three fields (`id`, `name` from `Person`; `department` own) in the correct inherited order.

---

### 7. Round-trip a parse result

```python
from maxi import parse_maxi, dump_maxi

input_text = """U:User(id:int|name|email=unknown)
###
U(1|Julie)
U(2|Matt|~)"""

result = await parse_maxi(input_text)
output = dump_maxi(result)

# output == input_text (modulo equivalent whitespace)
```

---

### 8. Multiline pretty-print

```python
maxi = dump_maxi(users, default_alias="U", types=user_types, multiline=True)
```

Output:
```
U:User(
  id:int|
  name|
  email=unknown
)
###
U(
  1|
  Julie
)
```

---

### 9. External schema reference (no inline types)

```python
maxi = dump_maxi({"id": 1, "name": "Julie"},
    default_alias="U",
    schema_file="schemas/users.maxi",
    include_types=False,
)
```

Output:
```
@schema:schemas/users.maxi
###
U(1|Julie)
```

---

### 10. `dump_maxi_auto` — zero-config dump from annotated classes

```python
from maxi import dump_maxi_auto
from maxi.models import MaxiModel, IntField, StrField

class User(MaxiModel, alias="U", name="User"):
    id    = IntField(required=True, id=True)
    name  = StrField()
    email = StrField(default="unknown")

maxi = dump_maxi_auto([
    User(id=1, name="Julie"),
    User(id=2, name="Matt", email=None),
])
```

Output:
```
U:User(id:int|name|email=unknown)
###
U(1|Julie)
U(2|Matt|~)
```

---

### 11. `dump_maxi_auto` — multi-type dict

```python
from maxi.models import MaxiModel, IntField, DecimalField

class Order(MaxiModel, alias="O", name="Order"):
    id    = IntField(required=True, id=True)
    total = DecimalField(min=0)

maxi = dump_maxi_auto({
    "U": [User(id=1, name="Julie")],
    "O": [Order(id=100, total=49.99)],
})
```

Output:
```
U:User(id:int|name|email=unknown)
O:Order(id:int|total:decimal)
###
U(1|Julie)
O(100|49.99)
```

---

### 12. `dump_maxi_auto` — nested referenced objects auto-collected

When a nested object's class also has a `__maxi_schema__`, its schema is discovered and
its instances are promoted to top-level records automatically — no extra config needed.

```python
from maxi.models import MaxiModel, StrField, RefField

class Address(MaxiModel, alias="A", name="Address"):
    id     = StrField()
    street = StrField()
    city   = StrField()

class Customer(MaxiModel, alias="C", name="Customer"):
    id      = StrField()
    name    = StrField()
    address = RefField(Address)

addr = Address(id="A1", street="123 Main St", city="NYC")
maxi = dump_maxi_auto([Customer(id="C1", name="John", address=addr)])
```

Output:
```
C:Customer(id|name|address:A)
A:Address(id|street|city)
###
C(C1|John|A1)
A(A1|"123 Main St"|NYC)
```

---

### 13. Enum value aliases — compact wire tokens

When a field uses `enum[alias:value, ...]`, the dumper always emits the alias. You can pass either the alias or the full value as input.

```python
from maxi import dump_maxi

users = [
    {"id": 1, "name": "Alice", "role": "admin"},   # full value
    {"id": 2, "name": "Bob",   "role": "e"},       # alias also accepted
]

maxi = dump_maxi(users, default_alias="U", types=[{
    "alias": "U",
    "name": "User",
    "fields": [
        {"name": "id",   "type_expr": "int"},
        {"name": "name"},
        {"name": "role", "type_expr": "enum[a:admin,e:editor,v:viewer]"},
    ],
}])
```

Output:
```
U:User(id:int|name|role:enum[a:admin,e:editor,v:viewer])
###
U(1|Alice|a)
U(2|Bob|e)
```
