#!/usr/bin/env bash
# foreshock — global installer for Claude Code.
# Copies the engine + hook into ~/.claude/hooks and registers the PostToolUse hook so it
# fires in every repo and session. Idempotent and safe to re-run (re-sync after an update).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"
CMD='python3 "$HOME/.claude/hooks/impact_hook.py"'

# 1. install the engine, hook, and all language plugins (core imports lang_*.py siblings)
mkdir -p "$HOOKS"
cp "$HERE"/impact_engine.py "$HERE"/impact_hook.py "$HERE"/lang_*.py "$HOOKS/"
chmod +x "$HOOKS/impact_hook.py"
echo "✓ installed engine + hook + $(ls "$HERE"/lang_*.py | wc -l | tr -d ' ') language plugins → $HOOKS"

# 2. register the PostToolUse hook (skip if already present)
if [ -f "$SETTINGS" ] && grep -q "impact_hook.py" "$SETTINGS"; then
  echo "✓ PostToolUse hook already registered in $SETTINGS"
  exit 0
fi

read -r -d '' SNIPPET <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "python3 \"$HOME/.claude/hooks/impact_hook.py\"", "timeout": 15 }
        ]
      }
    ]
  }
}
JSON

if command -v jq >/dev/null 2>&1; then
  [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  cp "$SETTINGS" "$SETTINGS.bak"
  tmp="$(mktemp)"
  if jq '.hooks.PostToolUse += [{"matcher":"Edit|Write|MultiEdit","hooks":[{"type":"command","command":"python3 \"$HOME/.claude/hooks/impact_hook.py\"","timeout":15}]}]' \
       "$SETTINGS" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    mv "$tmp" "$SETTINGS"
    echo "✓ registered PostToolUse hook in $SETTINGS (backup: $SETTINGS.bak)"
    echo "  → restart Claude Code; hooks load at session start."
  else
    rm -f "$tmp"
    echo "⚠ couldn't auto-edit $SETTINGS — add this manually (merge into existing JSON):"
    echo "$SNIPPET"
  fi
else
  echo "→ jq not found. Add this to $SETTINGS (merge into existing JSON):"
  echo "$SNIPPET"
fi
