#!/usr/bin/env python3
"""
foreshock Pre/PostToolUse hook (USER-LEVEL, self-rooting).

On every Edit/Write/MultiEdit, run impact_engine.py on the changed file and inject the CONTEXT
PACKET into the agent's next turn — as a preview before the edit (PreToolUse) and a confirm after
(PostToolUse). It roots itself at the EDITED FILE's repo (nearest .git / package.json / go.mod …
ancestor) instead of relying on CLAUDE_PROJECT_DIR, so it fires no matter how the session launched.

Fails safe (exit 0, silent) on anything unexpected: unsupported file type, no engine, no repo root,
engine error, or empty output (a local-only change).
"""
import sys, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "impact_engine.py")


# must cover every extension the lang_*.py plugins handle (keep in sync when adding a language)
EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".go", ".rb", ".cs", ".sql")
# repo-root markers across ecosystems
ROOT_MARKERS = (".git", "package.json", "pyproject.toml", "setup.py", "setup.cfg", "go.mod",
                "go.work", "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml", "Gemfile")


def find_repo_root(file_path):
    """Walk up from the edited file to the nearest project-root marker."""
    d = os.path.dirname(os.path.abspath(file_path))
    while d and d != os.path.dirname(d):
        if any(os.path.exists(os.path.join(d, m)) for m in ROOT_MARKERS):
            return d
        d = os.path.dirname(d)
    return None


try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

ti = payload.get("tool_input", {}) or {}
path = ti.get("file_path") or ti.get("path")
if not path or not path.endswith(EXTS):
    sys.exit(0)
if not os.path.exists(ENGINE):
    sys.exit(0)

root = find_repo_root(path)
if not root:
    sys.exit(0)

event = payload.get("hook_event_name") or "PostToolUse"   # Pre = preview, Post = confirm

# Default to ONE packet per edit — the preview. The after-edit confirm is opt-in, so foreshock
# never doubles its footprint in the agent's context. (FORESHOCK_CONFIRM=1 re-enables it.)
if event == "PostToolUse" and not os.environ.get("FORESHOCK_CONFIRM"):
    sys.exit(0)

deep = bool(os.environ.get("FORESHOCK_DEEP"))             # Tier 3 runs a real checker — give it room

try:
    out = subprocess.run(
        ["python3", ENGINE, "--file", path],
        capture_output=True, text=True, timeout=(90 if deep else 15),
        env={**os.environ, "FS_ROOT": root},
        input=json.dumps(payload),   # pipe the payload so the engine can diff old vs new
    ).stdout.strip()
except Exception:
    sys.exit(0)

# FORESHOCK_RATE: log EVERY preview edit (even silent ones) so the behavioral proxy's denominator
# — "did a later edit touch a flagged dependent?" — sees ALL edits, not just the ones that fired a
# packet. (A flagged dependent fixed by a silent leaf edit must still count as "acted on".)
# Only fired packets (non-empty out) get a rating prompt.
pid = None
if os.environ.get("FORESHOCK_RATE") and event == "PreToolUse":
    try:
        import re, foreshock_session
        tm = re.search(r"\[(LOCAL|narrow|shared|SHARED-CORE)\]", out)
        flagged = [p for p in re.findall(r"^\s+→\s+(\S+)", out, re.M) if "/" in p or "." in p]
        for m in re.findall(r"handle the new case at:\s*(.+)", out):   # variant dispatch sites count too
            flagged += [s.strip() for s in m.split(",") if "/" in s or "." in s]
        pid = foreshock_session.log_packet(payload.get("session_id", "default"),
                                           os.path.relpath(path, root), event,
                                           tm.group(1) if tm else "", "API change" in out,
                                           list(dict.fromkeys(flagged)), fired=bool(out))
    except Exception:
        pid = None

if not out:
    sys.exit(0)  # silent packet — nothing to inject (the edit was still logged above, for coverage)

closing = ("\n(Reconsider or adjust the change before applying.)" if event == "PreToolUse"
           else "\n(Consider these before continuing.)")
rate_ask = ""
if pid is not None:
    rate_ask = (f"\n[foreshock] How useful was this to your NEXT action? Rate 1–5 "
                f"(1=noise, 5=changed what I do): "
                f'python3 "$HOME/.claude/hooks/foreshock_rate.py" {payload.get("session_id", "default")} {pid} <N>')

print(json.dumps({
    "systemMessage": out,
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": out + closing + rate_ask,
    },
}))
sys.exit(0)
