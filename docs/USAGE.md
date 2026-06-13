# Using foreshock

foreshock gives your coding agent **peripheral vision**. Every time the agent edits a
file, foreshock looks at *what you changed*, figures out *who depends on it*, and hands the
agent a short **context packet** — before it moves on to the next step.

You install it once. After that it's invisible until an edit actually has consequences,
at which point a few lines appear in the agent's context.

---

## Local-only — nothing leaves your machine

foreshock is a **library that sits on your machine, not a service.** It is pure Python
**standard library** — no dependencies, no API keys, no account, no network calls. It does
not phone home, collect telemetry, or send your code anywhere.

When the agent edits a file, foreshock runs as a **local subprocess**, reads files inside
that one repo, and prints a short text packet **back into your own agent's context** on
the same machine. That's the entire data path. Nothing is uploaded, logged off-box, or
shared. If you're offline, it works exactly the same.

(Want to confirm? It's a few hundred lines of stdlib Python — `grep -ri "http\|requests\|urllib\|socket" engine/` returns nothing.)

---

## 1. Requirements

- **Python 3** (stdlib only — nothing to `pip install`)
- A coding agent that supports **`PostToolUse` hooks** (Claude Code today). Cursor/Codex
  work the same way if they expose an equivalent post-edit hook.
- A repo with a root marker foreshock can find: `.git`, `package.json`, `pyproject.toml`,
  `setup.py`, `go.mod`, `pom.xml`, `build.gradle`, or `Cargo.toml`.

Languages: **TypeScript / JavaScript, Python, Java, Go, Ruby, C#**, plus a **Django** framework
adapter and **SQL** schema coupling (opt-in: `export FORESHOCK_SQL=1` — tables/columns, FK edges,
`CHECK (col IN …)` variants). Other files are silently ignored.

---

## 2. Install

### Global (recommended) — every repo, every session

From the foreshock directory:

```bash
./engine/install.sh
```

This:
1. Copies the engine, hook, and all `lang_*.py` plugins into `~/.claude/hooks/`.
2. Registers a `PostToolUse` hook (matcher `Edit|Write|MultiEdit`) in
   `~/.claude/settings.json` — backing up the old file to `settings.json.bak` first.

It's **idempotent**: re-run it any time you pull an update to re-sync the files. If `jq`
isn't installed it prints the JSON snippet for you to merge by hand.

**Always-on / self-healing.** Installing also registers a `SessionStart` hook (`foreshock_ensure.py`)
that re-adds foreshock's hooks if `~/.claude/settings.json` ever loses them (e.g. another tool or
`/config` rewrites the hooks block). So foreshock can't silently drift off between sessions. (It
can't recover from `settings.json` being deleted outright — re-run `install.sh` for that.)

**Restart Claude Code afterward** — hooks load at session start.

The hook **self-roots**: it locates the edited file's repo on its own, so the same global
install works correctly across all your projects with no per-repo configuration.

### Per-repo (no global changes)

If you'd rather scope it to one project:

```bash
mkdir -p tools
cp engine/impact_engine.py engine/impact_hook.py engine/lang_*.py tools/
```

Then add a hook to that repo's `.claude/settings.json` (merge into existing JSON):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/impact_hook.py\"", "timeout": 15 }
        ]
      }
    ]
  }
}
```

---

## 3. Verify it's working

1. Restart the agent.
2. Edit a file that **other files import** — e.g. change the signature of an exported
   function in a shared module.
3. You should see a `foreshock — you edited …` block appear in the agent's context after
   the edit.

If you edit a **leaf file** (nothing imports it) and make a non-API change, foreshock
**stays silent by design** — that's not a failure, it's the noise filter working. Test
with a file you know has dependents.

You can also confirm the engine itself runs (outside the hook):

```bash
python3 engine/impact_engine.py            # repo map — blast-radius hot spots
```

> Note: hook delivery into the agent's context is version-dependent in Claude Code
> (issue #18427). If the engine runs but you never see packets, that's the thing to check.

---

## 4. How to read a packet

A packet is at most a handful of lines. Here's an annotated example for an edit to a
shared module:

```
foreshock — you edited src/lib/deadlines.ts
  • API change: +getMonthlyMechanic; ~computeMonthlyDeadline (declaration)
  • blast radius: 6 file(s) import this [shared]
  • who imports this:
      → src/routes/notice.ts (computeMonthlyDeadline, getMonthlyMechanic)
        src/lib/format.ts (computeMonthlyDeadline)
      → src/jobs/reminder.ts (computeMonthlyDeadline)
  • → = imports a CHANGED symbol — re-check those call sites
  • covered by tests: src/lib/deadlines.test.ts
```

Line by line:

| Line | What it tells the agent |
|------|--------------------------|
| **`API change:`** | What changed about the public surface. `+name` added, `−name` removed, `~name (declaration)` signature changed. (Shown only when the export surface actually changed.) |
| **`content-only:`** | *Alternative* to the above — you changed the body of a symbol but its signature is intact. "Import contract intact, behavior may differ." |
| **`blast radius:`** | How many files transitively import this, and a tier: `LOCAL` / `narrow` / `shared` / `SHARED-CORE`. |
| **`who imports this:`** | The direct dependents, each annotated with the symbols it pulls. A **`→`** marks the ones importing a *changed* symbol — those are the call sites to re-check. The rest are just FYI. |
| **`covered by tests:`** | Test files that import the edited module — your safety net for this change. |

For **variant types** (TS string unions, Python `Enum`/`Literal`, Java/C# `enum`, Go typed
`const`/`iota`),
adding a member produces:

```
  • ADDED to the `Status` set (archived) — handle the new case at: src/ui/badge.ts, src/api/filter.ts
```

i.e. the dispatch sites the compiler **won't** flag for you.

**When foreshock says nothing:** a local (0-dependent) edit with no API change produces no
output. Silence means "nothing downstream to worry about."

---

## 5. Manual CLI use (optional)

The engine is a normal script you can run yourself, independent of the hook:

```bash
# Repo map: which files ripple widest if changed
python3 engine/impact_engine.py

# Context packet for one file (diff-blind — shows dependents & variants, no +/− symbols)
python3 engine/impact_engine.py --file src/lib/deadlines.ts
```

A manual `--file` run can't see your edit's diff (the hook feeds that in via stdin), so it
shows the dependency picture and variant definitions but not the precise added/removed
symbols. It's useful for "what would happen if I touch this?" before you start.

Point it at another repo with `FS_ROOT`:

```bash
FS_ROOT=/path/to/repo python3 engine/impact_engine.py
```

---

## 6. Where it's strong / weak

- **Strong** where imports *are* the coupling: libraries, SDKs, shared utility/domain
  modules. This is where "I changed X, who breaks?" maps cleanly to the import graph.
- **Weak** where coupling is runtime or conventional rather than import-based — e.g.
  Next.js app routes wired by file convention, reflection, dynamic dispatch, or exotic
  re-exports. Parsing is regex-based, not a full compiler front-end, so those can slip by.

It is a **context layer, not a guarantee.** Treat packets as a prompt to look, not proof
that you've found everything.

---

## Change preview (Tier 1) & deep simulation (Tier 3)

By default foreshock fires **once per edit** — the **preview**:

- **`PreToolUse` — preview (default).** *Before* an edit is applied, it projects the change from the
  proposed `old_string`/`new_string` and shows what it *would* do:
  `foreshock — preview: this change to X would… • API change: +sum; −add • blast radius: …`.
  The agent sees the consequences while it can still adjust the edit. Nothing is written to disk.
- **`PostToolUse` — confirm (opt-in).** Set `FORESHOCK_CONFIRM=1` to *also* get a packet after the
  edit lands (`you edited X`). Off by default so foreshock never doubles its context footprint.

`install.sh` registers both hooks, but the Post one stays silent unless `FORESHOCK_CONFIRM=1`.

### Deep simulation — opt-in
Set `FORESHOCK_DEEP=1` and the preview adds a **real-checker** pass: foreshock copies the repo to
a temp dir (symlinking `node_modules`), applies the projected edit there, runs the project's own
checker, and reports **only the diagnostics the change introduces** (baseline-subtracted). Your
real files are never touched.

```
  • deep check — NEW errors this change introduces (real checker):
      ✗ src/calc.ts(1,10): error TS2305: Module './math' has no exported member 'add'.
```

Checkers wired: **TypeScript/JS** via the project's `tsc` (local `node_modules/.bin/tsc`, else
`npx tsc`); **Python** falls back to stdlib `py_compile` (syntax only — install `mypy`/`pyflakes`
for cross-file checks). It's slower (runs a toolchain), which is why it's off by default; the hook
timeout is raised to 90s when `FORESHOCK_DEEP` is set.

## Rate usefulness — turn impressions into data (`FORESHOCK_RATE=1`)

To find out whether the packets actually help (vs. just feel helpful), turn on rating:

```bash
export FORESHOCK_RATE=1
```

Now every packet ends with a prompt the agent can act on:

```
[foreshock] How useful was this to your NEXT action? Rate 1–5 (1=noise, 5=changed what I do):
python3 "$HOME/.claude/hooks/foreshock_rate.py" <session> <id> <N>
```

Ratings accumulate per session in `~/.cache/foreshock/sessions/<session>.jsonl`. When the session
ends, the **Stop hook** surfaces a review, or run it yourself any time:

```bash
python3 engine/foreshock_review.py            # most recent session
foreshock session review — 7 packet(s) fired, 6 rated
  average usefulness: 3.8/5    [1★:1 2★:0 3★:1 4★:2 5★:2]
  most useful: src/auth.ts (5★), src/db/schema.ts (5★)
  noise:       src/util/log.ts (1★)
```

It's **off by default** (no rating prompt, no logging) so it never bloats normal use. Silent edits
(local, zero-dependent) aren't logged — only packets that actually fired get rated, so there's no
rating noise. Over a few weeks of real sessions this gives you grounded numbers (and the per-file
hit/miss pattern) instead of impressions — and it's the raw material for outcome-calibrated weighting.

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| No packet ever appears | Hook not registered or agent not restarted. Re-run `install.sh`, restart. Check `~/.claude/settings.json` contains `impact_hook.py`. |
| Engine runs manually but no packets in-agent | Hook-context delivery (Claude Code #18427) — version-dependent. |
| Packet on a leaf-file edit expected but absent | By design — 0 dependents + no API change = silence. Test on a shared module. |
| Nothing on a huge monorepo | Portability guard: `--file` runs bail above ~12,000 source files to stay within the hook's ~15s budget. |
| Wrong repo root picked up | foreshock walks up to the nearest root marker; an unexpected `.git`/`package.json` ancestor can shadow the real root. |

---

## 8. Uninstall

```bash
rm ~/.claude/hooks/impact_engine.py ~/.claude/hooks/impact_hook.py ~/.claude/hooks/lang_*.py
```

Then remove the `PostToolUse` entry referencing `impact_hook.py` from
`~/.claude/settings.json` (or restore `~/.claude/settings.json.bak`).
