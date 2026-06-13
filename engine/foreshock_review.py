#!/usr/bin/env python3
"""Print the foreshock usefulness review for a session (defaults to the most recent).

  foreshock_review.py [session]
"""
import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foreshock_session

session = sys.argv[1] if len(sys.argv) > 1 else None
if not session:
    files = sorted(glob.glob(os.path.join(foreshock_session.SESS_DIR, "*.jsonl")), key=os.path.getmtime)
    session = os.path.basename(files[-1])[:-6] if files else "default"
print(foreshock_session.summarize(session))
