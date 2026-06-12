# foreshock

**Inline context for coding agents.** When an agent edits a file, foreshock hands it a context
packet — *what you touched → what depends on it → what to check* — at the moment of the edit,
before it moves on. Proactive peripheral vision inside the agent loop, not a post-commit report.

It rides *inside* Claude Code / Cursor / Codex via a hook. It is not a linter or an app — it's a
context layer the agent consumes.

**Local-only, like a library.** Pure Python stdlib — no dependencies, no API keys, no account, no
network. It runs as a local subprocess, reads one repo, and prints a packet back into your own
agent's context. Nothing is collected and nothing leaves your machine; it works fully offline.

## The product (`engine/`)
**Architecture: a language-agnostic core + one plugin per language.** `impact_engine.py` owns the
graph, blast-radius, and the packet; each `lang_*.py` owns its language's imports, resolution,
exports, and variant types. Adding a language = drop in one file.
- **`lang_ts.py`** — TS/JS: `import`/`export … from` (barrels), dynamic `import()`, `require()`,
  side-effect imports; ts/jsconfig path aliases; string-literal unions.
- **`lang_python.py`** — Python: absolute + relative imports, sibling/`sys.path`-rooted resolution;
  `def`/`class`/const exports; `Enum` + `Literal[…]` variants.
- **`lang_java.py`** — Java: package→FQCN resolution of `import a.b.C;` (+ static/wildcard); public
  type/method exports; `enum` variants.
- **`impact_engine.py`** — for an edited file emits a **diff-aware, symbol-level** CONTEXT PACKET
  describing the *consequences* of the edit, not a raw file count:
  - **what changed about the public surface** — `API change: +getMonthlyMechanic` /
    `~computeMonthlyDeadline (declaration)` vs `content-only: changed the body of X — import
    contract intact`. (Reconstructs the old file from the edit's `old_string`/`new_string`.)
  - **who imports this** — each dependent annotated with the exact symbols it pulls, **→ marking**
    the ones that import a *changed* symbol (kills the "49 files but only 2 use it" noise).
  - **covered by tests** — the test files that import the edited module.
  - **variant/completeness** — "ADDED to the `Foo` set (bar) — handle the new case at: \<dispatch
    sites the compiler won't flag\>" (TS string unions, Python `Enum`/`Literal`, Java `enum`).
  Stays **silent** on local (0-dependent) non-API edits. Repo-agnostic, stdlib-only.
- **`impact_hook.py`** — Claude Code `PostToolUse` hook: pipes the tool payload to the engine (so it
  can diff old vs new) and injects the packet into the agent's next turn. **Self-roots** to the
  edited file's repo (nearest project-root marker: `.git`, `package.json`, `pyproject.toml`,
  `pom.xml`, …), so it works whether installed per-repo or user-level — no `CLAUDE_PROJECT_DIR`.

### Use
```bash
python3 engine/impact_engine.py                  # repo map: blast-radius hot spots
python3 engine/impact_engine.py --file src/x.ts  # context packet for one file
```
**Install (global, all repos):** run `./engine/install.sh` — it copies the engine + hook +
language plugins into `~/.claude/hooks/` and registers the `PostToolUse` hook in
`~/.claude/settings.json` (idempotent; re-run to re-sync after an update). Because the hook
self-roots, it fires correctly in every repo and every session. Restart Claude Code afterward.

The global install does **not** auto-update: after editing anything in `engine/`, re-run
`./engine/install.sh` to re-sync `~/.claude/hooks/` — a stale copy silently gives weaker packets.

**→ Full setup, packet anatomy, verification, and troubleshooting: [docs/USAGE.md](docs/USAGE.md).**
**→ Real output on public repos (zod, flask): [docs/EXAMPLES.md](docs/EXAMPLES.md).**

## Honest scope
foreshock reads **import-shaped** coupling across TS/JS, Python, and Java (one plugin each). It's
strong where imports = coupling (libraries/SDKs) and weak where coupling is conventional/runtime
(e.g. Next.js app routes — see the deviation notes). Parsing is regex-based, not a full compiler
front-end: exotic re-exports / reflection / dynamic dispatch can still slip by.
Best tested with an **agent in the loop**: does the injected context change the agent's next action
for the better.

## experiments/bugcatch-deviation/  — what NOT to do (kept as a lesson)
A multi-day detour that reframed foreshock from "inline context for an agent" into a standalone
**bug-predictor** graded against git co-change (precision/recall, calibration, temporal splits).
It removed the agent from the loop entirely and measured statistical graph-overlap instead of
"does context make the agent less blind." Useful eval machinery, wrong instrument for this vision —
a fishnet for mosquitos. The findings (incl. that it works on libraries, not apps) live in
`experiments/bugcatch-deviation/`. The product is `engine/`.
