#!/usr/bin/env python3
"""foreshock SessionStart hook — keep foreshock registered so it can't silently drift off.

Every time a Claude Code session starts, this checks ~/.claude/settings.json still has foreshock's
hooks and re-adds any that went missing (e.g. another tool or /config rewrote the hooks block).
Silent, idempotent, fail-safe — it never blocks the session and only writes when something changed.

(It cannot recover from settings.json being wiped entirely — this hook would be gone too — but that
is rare and recovered by re-running engine/install.sh.)
"""
import json, os, sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
EDIT = {"matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/impact_hook.py"', "timeout": 90}]}
STOP = {"hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/foreshock_stop.py"'}]}
ENSURE = {"hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/foreshock_ensure.py"'}]}

# event → (object to add, substring that proves it's already there)
WANT = [("PreToolUse", EDIT, "impact_hook.py"),
        ("PostToolUse", EDIT, "impact_hook.py"),
        ("Stop", STOP, "foreshock_stop.py"),
        ("SessionStart", ENSURE, "foreshock_ensure.py")]

try:
    settings = json.load(open(SETTINGS)) if os.path.exists(SETTINGS) else {}
except Exception:
    sys.exit(0)                       # unreadable settings — do nothing, never block the session

hooks = settings.setdefault("hooks", {})
changed = False
for event, obj, needle in WANT:
    present = any(needle in hh.get("command", "")
                  for entry in hooks.get(event, []) for hh in entry.get("hooks", []))
    if not present:
        hooks.setdefault(event, []).append(obj)
        changed = True

if changed:
    try:
        json.dump(settings, open(SETTINGS, "w"), indent=2)
        print(json.dumps({"systemMessage": "foreshock: re-registered missing hooks (self-heal)."}))
    except Exception:
        pass
sys.exit(0)
