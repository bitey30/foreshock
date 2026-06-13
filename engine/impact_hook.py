#!/usr/bin/env python3
"""
foreshock PostToolUse hook (USER-LEVEL, self-rooting).

After every Edit/Write/MultiEdit, run impact_engine.py on the changed file and inject
the CONTEXT PACKET into the agent's next turn. Unlike the project-scoped variant, this
roots itself at the EDITED FILE's repo (nearest .git / package.json ancestor) instead of
relying on CLAUDE_PROJECT_DIR — so it fires no matter how the session was launched.

Fails safe (exit 0, silent) on anything unexpected: non-JS/TS file, no engine, no repo
root, engine error, or empty output (a local-only change).
"""
import sys, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "impact_engine.py")


EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java")
# repo-root markers across ecosystems (Python/Java repos have no package.json)
ROOT_MARKERS = (".git", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
                "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml")


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

if not out:
    sys.exit(0)  # local-only change — stay quiet

closing = ("\n(Reconsider or adjust the change before applying.)" if event == "PreToolUse"
           else "\n(Consider these before continuing.)")

# FORESHOCK_RATE=1 → log this packet and ask the agent to rate its usefulness 1–5 (off by default)
rate_ask = ""
if os.environ.get("FORESHOCK_RATE"):
    try:
        import re, foreshock_session
        session = payload.get("session_id", "default")
        tm = re.search(r"\[(LOCAL|narrow|shared|SHARED-CORE)\]", out)
        pid = foreshock_session.log_packet(session, os.path.relpath(path, root), event,
                                           tm.group(1) if tm else "", "API change" in out)
        rate_ask = (f"\n[foreshock] How useful was this to your NEXT action? Rate 1–5 "
                    f"(1=noise, 5=changed what I do): "
                    f'python3 "$HOME/.claude/hooks/foreshock_rate.py" {session} {pid} <N>')
    except Exception:
        rate_ask = ""

print(json.dumps({
    "systemMessage": out,
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": out + closing + rate_ask,
    },
}))
sys.exit(0)
