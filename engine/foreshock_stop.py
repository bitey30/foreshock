#!/usr/bin/env python3
"""Claude Code Stop hook: when the session ends, surface the foreshock usefulness review.

Only active when FORESHOCK_RATE is set (the same flag that turns on per-packet rating), so it
stays silent for normal use.
"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if not os.environ.get("FORESHOCK_RATE"):
    sys.exit(0)
try:
    import foreshock_session
    payload = json.load(sys.stdin)
    summary = foreshock_session.summarize(payload.get("session_id", "default"))
    print(json.dumps({"systemMessage": summary}))
except Exception:
    pass
sys.exit(0)
