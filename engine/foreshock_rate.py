#!/usr/bin/env python3
"""Record an agent's 1–5 usefulness rating for a foreshock packet.

  foreshock_rate.py <session> <packet_id> <1-5> [note...]

The packet the agent received tells it the exact command to run (session + id pre-filled).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foreshock_session

if len(sys.argv) < 4:
    print("usage: foreshock_rate.py <session> <packet_id> <1-5> [note...]")
    sys.exit(1)

session, pid, rating = sys.argv[1], sys.argv[2], sys.argv[3]
note = " ".join(sys.argv[4:])
try:
    ok = foreshock_session.set_rating(session, pid, rating, note)
except Exception:
    ok = False
print(f"✓ foreshock packet {pid} rated {max(1, min(5, int(rating)))}/5" if ok
      else "✗ foreshock: packet id not found for this session")
