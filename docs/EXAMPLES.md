# foreshock by example

Real output from the shipped engine on **public** repositories — nothing here is mocked.
Every command below is reproducible: clone the repo, point foreshock at it, see the same
thing.

---

## 1. Repo map — where changes ripple widest

The map ranks files by **transitive dependents** (afferent coupling): edit a file near the
top and the blast radius is large.

```bash
git clone --depth 1 https://github.com/colinhacks/zod
FS_ROOT=$(pwd)/zod python3 engine/impact_engine.py
```

```
233 source files ({'ts': 233}), 460 import edges

blast-radius hot spots (a change here ripples widest):
   122 ← packages/zod/src/v4/core/versions.ts
   121 ← packages/zod/src/v4/core/json-schema.ts
   121 ← packages/zod/src/v4/core/standard-schema.ts
   121 ← packages/zod/src/v4/core/doc.ts
   120 ← packages/zod/src/v4/locales/mk.ts
    …
```

Same thing, Python:

```bash
git clone --depth 1 https://github.com/pallets/flask
FS_ROOT=$(pwd)/flask python3 engine/impact_engine.py
```

```
38 source files ({'python': 38}), 139 import edges

blast-radius hot spots (a change here ripples widest):
    34 ← src/flask/signals.py
    34 ← src/flask/typing.py
    33 ← src/flask/app.py
    33 ← src/flask/helpers.py
    …
```

---

## 2. The context packet — what an agent sees mid-edit

This is the payload the `PostToolUse` hook injects into the agent's next turn. Here an agent
adds a keyword parameter to Flask's `url_for` — a function imported across the framework.

**The edit** (in `src/flask/helpers.py`):

```python
 def url_for(
     endpoint: str,
     *,
     _anchor: str | None = None,
     _method: str | None = None,
     _scheme: str | None = None,
     _external: bool | None = None,
+    _trailing_slash: bool = False,
     **values: t.Any,
 ) -> str:
```

**The packet foreshock injects:**

```
foreshock — you edited src/flask/helpers.py
  • API change: ~url_for (declaration)
  • blast radius: 33 file(s) import this [SHARED-CORE]
  • who imports this:
      → src/flask/__init__.py (abort, flash, get_flashed_messages, get_template_attribute)
        src/flask/app.py (_CollectErrors, get_debug_flag, get_flashed_messages, get_load_dotenv)
        src/flask/blueprints.py (send_from_directory)
        src/flask/cli.py (get_debug_flag, get_load_dotenv)
        src/flask/ctx.py (_CollectErrors)
        src/flask/sansio/app.py (_split_blueprint_path, get_debug_flag)
        src/flask/sansio/scaffold.py (get_root_path)
        src/flask/templating.py (stream_with_context)
      …
  • → = imports a CHANGED symbol — re-check those call sites
  • covered by tests: tests/test_helpers.py
```

What the agent now knows that it didn't a second ago:

- **`API change: ~url_for (declaration)`** — this touched the *public signature*, not just a body.
- **`blast radius: 33 file(s) [SHARED-CORE]`** — a local-feeling edit is actually a top-tier
  hub change. Slow down.
- **`who imports this`** with **`→`** markers — the exact files pulling the changed symbol,
  so call sites get re-checked by name instead of guessing across 33 files.
- **`covered by tests: tests/test_helpers.py`** — the test to run before moving on.

If the same edit had only changed the *body* of `url_for` (signature intact), the packet
would say `content-only: … import contract intact` instead — telling the agent its
dependents are safe and only behavior changed.

And if you edit a **leaf** file (nothing imports it) without changing its API, foreshock
stays **silent** — no packet, no noise.

---

## 3. Variant / completeness (the case the compiler misses)

When you add a member to a string-literal union / enum, foreshock points at the dispatch
sites that silently don't handle it yet. This one is a **real latent runtime break in zod**,
found live.

**The edit** — add `sha224` to zod's `HashAlgorithm` union (`src/v4/core/util.ts`):

```ts
-export type HashAlgorithm = "md5" | "sha1" | "sha256" | "sha384" | "sha512";
+export type HashAlgorithm = "md5" | "sha1" | "sha224" | "sha256" | "sha384" | "sha512";
```

**The packet:**

```
foreshock — you edited src/v4/core/util.ts
  • API change: ~HashAlgorithm (declaration); HashAlgorithm+{sha224}
  • blast radius: 88 file(s) import this [SHARED-CORE]
  • who imports this:
        src/v4/classic/errors.ts (*, default)
        src/v4/core/api.ts (*, default)
        …
  • covered by tests: src/v4/core/tests/locales/en.test.ts, …
  • ADDED to the `HashAlgorithm` set (sha224) — handle the new case at: src/v4/classic/schemas.ts, src/v4/mini/schemas.ts
```

**Why this matters — `tsc` stays silent, the code breaks at runtime.** Both flagged sites
are `hash<Alg extends util.HashAlgorithm>(...)` functions that resolve the algorithm through
a *runtime* lookup:

```ts
const regex = core.regexes[format as keyof typeof core.regexes] as RegExp;
if (!regex) throw new Error(`Unrecognized hash format: ${format}`);
```

`regexes.ts` has `md5_hex`, `sha1_hex`, `sha256_hex`, … but **no `sha224_*`**. So the new
member compiles clean (the generic accepts it) and `z.hash("sha224")` throws at runtime.
`tsc` can't catch it — the lookup is a `keyof typeof` cast over a dynamic index. foreshock
pointed straight at the two sites that need the matching regex added.

> Reproduce: `git clone --depth 1 https://github.com/colinhacks/zod`, install foreshock,
> then add `"sha224"` to `HashAlgorithm` in `packages/zod/src/v4/core/util.ts`.

---

## 4. Same thing in Java — an enum across a `switch`

The plugin model means this works identically for Java `enum`s. Here an agent adds a
`TIMESTAMP` constant to gson's `JsonToken` (`stream/JsonToken.java`):

```java
   /** A JSON {@code null}. */
   NULL,
+
+  /** A JSON timestamp literal. */
+  TIMESTAMP,
```

**The packet:**

```
foreshock — you edited src/main/java/com/google/gson/stream/JsonToken.java
  • API change: JsonToken+{TIMESTAMP}
  • blast radius: 42 file(s) import this [SHARED-CORE]
  • who imports this:
      → src/main/java/com/google/gson/Gson.java (JsonToken)
      → src/main/java/com/google/gson/JsonParser.java (JsonToken)
      → src/main/java/com/google/gson/TypeAdapter.java (JsonToken)
      …
  • → = imports a CHANGED symbol — re-check those call sites
  • covered by tests: …/stream/JsonReaderTest.java, …/bind/JsonTreeReaderTest.java
  • ADDED to the `JsonToken` set (TIMESTAMP) — handle the new case at: Gson.java, JsonParser.java, JsonStreamParser.java, TypeAdapter.java, TypeAdapterFactory.java, Streams.java
```

**Why it matters.** gson dispatches on this enum with `switch` statements — e.g.
`internal/bind/ObjectTypeAdapter.java` and `JsonElementTypeAdapter.java` have
`case STRING / NUMBER / BOOLEAN / NULL`. In Java a **switch *statement* with a missing enum
case compiles clean and silently falls through** — `javac` doesn't force exhaustiveness. The
`ADDED to the set` line is foreshock's candidate list of files that reference the enum, so
the agent goes and checks those dispatch sites instead of assuming the new constant is wired
up everywhere.

> Reproduce: `git clone --depth 1 https://github.com/google/gson`, install foreshock,
> then add a `TIMESTAMP` constant to `JsonToken` in `gson/src/main/java/.../stream/JsonToken.java`.
