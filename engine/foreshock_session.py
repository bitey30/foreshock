"""foreshock session ledger — per-session packet log + 1–5 usefulness ratings.

When FORESHOCK_RATE=1, the hook logs each injected packet here and asks the agent to rate how
useful it was to its NEXT action (1=noise … 5=changed what I do). Ratings accumulate across the
session; `foreshock_review.py` (and the Stop hook) summarize them when the session ends.

One JSONL file per session under ~/.cache/foreshock/sessions/<session>.jsonl.
"""
import os, json, time

SESS_DIR = os.path.join(os.path.expanduser("~"), ".cache", "foreshock", "sessions")

def _path(session):
    return os.path.join(SESS_DIR, (session or "default").replace("/", "_") + ".jsonl")

def _read(session):
    p = _path(session)
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []

def log_packet(session, file, event, tier, api):
    """Append a packet record (rating starts null); return its session-local id."""
    os.makedirs(SESS_DIR, exist_ok=True)
    rows = _read(session)
    pid = len(rows) + 1
    rec = {"id": pid, "ts": time.time(), "file": file, "event": event or "PostToolUse",
           "tier": tier or "", "api": bool(api), "rating": None, "note": ""}
    with open(_path(session), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return pid

def set_rating(session, pid, rating, note=""):
    rows = _read(session)
    hit = False
    for r in rows:
        if r.get("id") == int(pid):
            r["rating"] = max(1, min(5, int(rating))); r["note"] = note; hit = True
    if hit:
        with open(_path(session), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return hit

def summarize(session):
    rows = _read(session)
    if not rows:
        return "foreshock: no packets logged this session."
    rated = [r for r in rows if r.get("rating")]
    out = [f"foreshock session review — {len(rows)} packet(s) fired, {len(rated)} rated"]
    if rated:
        avg = sum(r["rating"] for r in rated) / len(rated)
        dist = " ".join(f"{k}★:{sum(1 for r in rated if r['rating'] == k)}" for k in range(1, 6))
        out.append(f"  average usefulness: {avg:.1f}/5    [{dist}]")
        hi = [r for r in rated if r["rating"] >= 4]
        lo = [r for r in rated if r["rating"] <= 2]
        if hi: out.append("  most useful: " + ", ".join(f"{r['file']} ({r['rating']}★)" for r in hi[:6]))
        if lo: out.append("  noise:       " + ", ".join(f"{r['file']} ({r['rating']}★)" for r in lo[:6]))
    if len(rows) - len(rated):
        out.append(f"  {len(rows) - len(rated)} packet(s) left unrated")
    return "\n".join(out)
