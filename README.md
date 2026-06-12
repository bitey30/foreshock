<p align="center">
  <img src="assets/logo.svg" alt="foreshock" width="620">
</p>

<p align="center">
  <em>Peripheral vision for coding agents — the tremor before the break.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3" src="https://img.shields.io/badge/python-3.x-3776AB.svg">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-0%20(stdlib)-success.svg">
  <img alt="Languages" src="https://img.shields.io/badge/languages-TS%20%2F%20JS%20%C2%B7%20Python%20%C2%B7%20Java-0B1120.svg">
  <img alt="Local only" src="https://img.shields.io/badge/data-local--only%20%C2%B7%20no%20network-555.svg">
</p>

---

When a coding agent edits a file, it thinks *locally* — one function, one signature — and then moves
on, blind to what it just put at risk. **foreshock gives it peripheral vision.** At the moment of the
edit, it hands the agent a short context packet — *what you touched → who depends on it → what to
check* — and then gets out of the way.

It rides **inside** the agent loop (Claude Code / Cursor / Codex) as a `PostToolUse` hook. Not a
linter, not a dashboard, not a post-commit report — a context layer the agent consumes *before* the
bug exists.

<p align="center">
  <img src="assets/blast-radius-demo.svg" alt="foreshock detecting blast radius live during an agent edit to Flask's url_for" width="780">
</p>

<p align="center">
  <sub>Live capture — an agent adds a parameter to Flask's <code>url_for</code>; foreshock catches that a
  local-looking edit is a <code>SHARED-CORE</code> hub change across 33 files and hands back the test to run.</sub>
</p>

> foreshock works the same on string-literal unions and enums. Add `"sha224"` to zod's
> `HashAlgorithm` and it points at the two `hash()` dispatch sites that compile clean but throw at
> runtime — the lookup is a `keyof typeof` cast `tsc` can't check. ([more real examples →](docs/EXAMPLES.md))

## Why

Every other tool in this space looks at the diff **after** it's written — PR review, a dashboard, a
report. By then the agent has already moved three steps on. foreshock's bet is that the useful moment
is **during** the edit: tell the agent its change is bigger than it looks, *while it can still act on
it.* Proactive, in-loop, single purpose.

## What you get — the packet

For each edit, the engine emits a **diff-aware, symbol-level** packet describing the *consequences* of
the change, not a raw file count:

- **What changed about the public surface** — `API change: +foo` / `~bar (declaration)` vs
  `content-only: changed the body of X, import contract intact`. (Reconstructed from the edit's
  before/after strings.)
- **Who imports this** — every dependent annotated with the symbols it pulls, with **`→`** marking the
  ones that import a *changed* symbol. Kills the "49 files but only 2 are affected" noise.
- **Covered by tests** — the test files that exercise the edited module.
- **Variant / completeness** — *"you added `bar` to the `Foo` set — handle the new case at ⟨dispatch
  sites the compiler won't flag⟩"* (TS string-literal unions, Python `Enum`/`Literal`, Java `enum`).

And critically, it stays **silent** on local, zero-dependent, non-API edits. Signal, not noise.

## Quickstart

```bash
git clone https://github.com/bitey30/foreshock && cd foreshock
./engine/install.sh        # installs the hook into ~/.claude/hooks + registers PostToolUse
```

Restart Claude Code and edit a file that others import — the packet appears in the agent's next turn.
The hook **self-roots** to each edited file's repo, so one global install works across every project.

> Re-run `./engine/install.sh` after changing anything in `engine/` — the global copy doesn't
> auto-update, and a stale copy gives weaker packets.

Run it by hand, too:

```bash
python3 engine/impact_engine.py                  # repo map: blast-radius hot spots
python3 engine/impact_engine.py --file src/x.ts  # context packet for one file
```

**Full setup, packet anatomy, and troubleshooting → [docs/USAGE.md](docs/USAGE.md)**

## How it works

A **language-agnostic core + one plugin per language.** `impact_engine.py` owns the import graph,
blast-radius, and the packet; each `lang_*.py` owns its language's imports, resolution, exports, and
variant types. **Adding a language is one file.**

| | |
|---|---|
| `impact_engine.py` | graph · transitive dependents · diff reconstruction · the packet |
| `lang_ts.py` | TS/JS — `import`/`export … from`, barrels, dynamic `import()`, `require()`, ts/jsconfig path aliases, string-literal unions |
| `lang_python.py` | Python — absolute + relative imports, `sys.path` resolution, `def`/`class`/const exports, `Enum` + `Literal[…]` |
| `lang_java.py` | Java — package→FQCN resolution (`import a.b.C;`, static, wildcard), public type/method exports, `enum` |
| `impact_hook.py` | the `PostToolUse` hook — pipes the tool payload to the engine and injects the packet |

**Local-only, like a library.** Pure Python standard library — no dependencies, no API keys, no
account, no network. It runs as a local subprocess, reads one repo, prints a packet back into your own
agent's context. Nothing is collected, nothing leaves your machine, works fully offline.

## Honest scope

foreshock reads **import-shaped** coupling. It's strong where imports *are* the coupling
(libraries, SDKs, shared modules) and weak where coupling is conventional or runtime (e.g. Next.js app
routes wired by file convention). Parsing is regex-based, not a full compiler front-end, so exotic
re-exports / reflection / dynamic dispatch can slip by. It's a **context layer, not a guarantee** — a
prompt to look, not proof you've found everything. The honest write-up of where it fails (and why
it's a library tool, not an app tool) lives in [`experiments/bugcatch-deviation/`](experiments/bugcatch-deviation/).

## License

[MIT](LICENSE).
