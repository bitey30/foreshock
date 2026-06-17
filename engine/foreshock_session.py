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

def log_packet(session, file, event, tier, api, flagged=(), fired=True):
    """Append an edit record (rating starts null); return its session-local id.
    `flagged` = the dependent files foreshock surfaced (→ / variant sites) — used for the proxy.
    `fired` = whether a packet was actually shown (False = silent edit, logged only for coverage)."""
    os.makedirs(SESS_DIR, exist_ok=True)
    rows = _read(session)
    pid = len(rows) + 1
    rec = {"id": pid, "ts": time.time(), "file": file, "event": event or "PostToolUse",
           "tier": tier or "", "api": bool(api), "flagged": list(flagged), "fired": bool(fired),
           "rating": None, "note": ""}
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

def behavioral(rows):
    """Full-coverage, model-free proxy: of the packets that surfaced dependents (the → files),
    how many were FOLLOWED by an edit to one of those files? i.e. did the agent act on the blast
    radius foreshock showed. Noisier than an explicit rating but covers every fire, unbiased.
    Returns (acted_on, with_flagged)."""
    acted = 0
    with_flagged = [r for r in rows if r.get("flagged")]
    for r in with_flagged:
        flagged = set(r["flagged"])
        rid = r.get("id", 0)
        if any(q.get("file") in flagged for q in rows if q.get("id", 0) > rid):
            acted += 1
    return acted, len(with_flagged)

def summarize(session):
    rows = _read(session)
    if not rows:
        return "foreshock: no packets logged this session."
    fired = [r for r in rows if r.get("fired", True)]      # old rows lack 'fired' → they were fires
    rated = [r for r in rows if r.get("rating")]
    span = f" across {len(rows)} edits" if len(rows) != len(fired) else ""
    out = [f"foreshock session review — {len(fired)} packet(s) fired{span}, {len(rated)} rated"]
    if rated:
        avg = sum(r["rating"] for r in rated) / len(rated)
        dist = " ".join(f"{k}★:{sum(1 for r in rated if r['rating'] == k)}" for k in range(1, 6))
        out.append(f"  explicit rating: {avg:.1f}/5    [{dist}]   (model-judged, {len(rated)}/{len(fired)} covered)")
        hi = [r for r in rated if r["rating"] >= 4]
        lo = [r for r in rated if r["rating"] <= 2]
        if hi: out.append("  most useful: " + ", ".join(f"{r['file']} ({r['rating']}★)" for r in hi[:6]))
        if lo: out.append("  noise:       " + ", ".join(f"{r['file']} ({r['rating']}★)" for r in lo[:6]))
    acted, withf = behavioral(rows)
    if withf:
        out.append(f"  behavioral signal: {acted}/{withf} ({100 * acted // withf}%) of packets that flagged "
                   f"dependents were followed by an edit to one — agent acted on the blast radius (full coverage)")
    if len(fired) - len(rated):
        out.append(f"  {len(fired) - len(rated)} packet(s) had no explicit rating (behavioral signal still covers them)")
    return "\n".join(out)
