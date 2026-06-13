#!/usr/bin/env bash
# foreshock — global installer for Claude Code.
# Copies the engine + hook into ~/.claude/hooks and registers BOTH a PreToolUse (preview:
# "this change would…") and a PostToolUse (confirm: "you edited…") hook, so it fires in every
# repo and session. Idempotent and safe to re-run (re-sync after an update).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"

# 1. install the engine, hook, deep-check, and all language plugins
mkdir -p "$HOOKS"
cp "$HERE"/impact_engine.py "$HERE"/impact_hook.py "$HERE"/deep_check.py "$HERE"/lang_*.py "$HERE"/framework_*.py "$HOOKS/"
chmod +x "$HOOKS/impact_hook.py"
echo "✓ installed engine + hook + deep_check + $(ls "$HERE"/lang_*.py | wc -l | tr -d ' ') language plugins + $(ls "$HERE"/framework_*.py | wc -l | tr -d ' ') framework adapters → $HOOKS"

# the hook object registered on both events (timeout covers Tier 3 deep mode when FORESHOCK_DEEP=1)
read -r -d '' HOOKOBJ <<'JSON'
{ "matcher": "Edit|Write|MultiEdit", "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/hooks/impact_hook.py\"", "timeout": 90 } ] }
JSON

if command -v jq >/dev/null 2>&1; then
  [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  cp "$SETTINGS" "$SETTINGS.bak"
  tmp="$(mktemp)"
  # idempotent: strip any existing impact_hook.py entries, then re-add to Pre + Post
  if jq --argjson h "$HOOKOBJ" '
        (.hooks.PreToolUse  //= []) | (.hooks.PostToolUse //= []) |
        .hooks.PreToolUse  |= map(select(((.hooks[0].command // "") | test("impact_hook.py")) | not)) |
        .hooks.PostToolUse |= map(select(((.hooks[0].command // "") | test("impact_hook.py")) | not)) |
        .hooks.PreToolUse  += [$h] | .hooks.PostToolUse += [$h]
      ' "$SETTINGS" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    mv "$tmp" "$SETTINGS"
    echo "✓ registered PreToolUse (preview) + PostToolUse (confirm) hooks in $SETTINGS (backup: $SETTINGS.bak)"
    echo "  → restart Claude Code; hooks load at session start."
    echo "  → Tier 3 deep simulation is opt-in: export FORESHOCK_DEEP=1 (runs the project's real checker)."
  else
    rm -f "$tmp"
    echo "⚠ couldn't auto-edit $SETTINGS — add an Edit|Write|MultiEdit hook calling impact_hook.py under BOTH .hooks.PreToolUse and .hooks.PostToolUse."
  fi
else
  echo "→ jq not found. Add this hook object under BOTH .hooks.PreToolUse and .hooks.PostToolUse in $SETTINGS:"
  echo "$HOOKOBJ"
fi
