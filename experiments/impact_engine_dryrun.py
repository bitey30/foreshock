"""
Dry-run of the thesis: "fact-keyed truth maintenance over agent verdicts."

We simulate a codebase as a dependency graph, attach "agent verdicts" (safe/unsafe)
to subject nodes, stream random edits, and compare strategies for keeping verdicts
up to date:

  NAIVE         - re-verify EVERY verdict on EVERY change   (correct, max cost)
  STRUCTURAL    - re-verify only if subject or its DIRECT deps changed (depth-1)
  TMS(c)        - fact-keyed: re-verify iff the content-hash of the verdict's
                  DECLARED justification set changed.  c = capture completeness
                  (prob the agent actually declared each true premise).
  TMS-CONSV(c)  - conservative: declared justification UNION structural neighborhood,
                  i.e. over-invalidate to buy back safety.

Two non-trivial realities are modeled:
  * cosmetic edits (whitespace/rename) that DON'T change semantic content  -> early cutoff
  * non-deterministic agent verdicts (per-call noise) with a stability gate (majority of k)

Metrics:
  agent_calls   - cost (number of LLM/agent invocations)
  stale         - verdicts whose cached value != ground truth (safety failures)
  dangerous     - the catastrophic subset: cached "SAFE" but truth is "UNSAFE"
"""

import random, statistics

SEED = 7
N_NODES = 300
N_VERDICTS = 120
DEP_FANIN = 3          # each node depends on up to this many lower nodes
TRUE_DEPTH = 4         # a verdict truly depends on subject + deps within this depth
STEPS = 2000           # number of edits in the stream
COSMETIC_FRAC = 0.35   # fraction of edits that are cosmetic (no semantic change)
NOISE = 0.10           # per-call prob the agent verdict flips
STABILITY_K = 5        # majority-of-k stability gate


def build_graph(rng):
    deps = {i: [] for i in range(N_NODES)}
    for i in range(N_NODES):
        k = rng.randint(0, DEP_FANIN)
        for _ in range(k):
            if i > 0:
                deps[i].append(rng.randrange(0, i))
        deps[i] = sorted(set(deps[i]))
    return deps


def true_justification(node, deps, depth):
    """subject + transitive deps within `depth` = the facts the verdict REALLY depends on."""
    seen, frontier = {node}, [node]
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for d in deps[n]:
                if d not in seen:
                    seen.add(d); nxt.append(d)
        frontier = nxt
    return seen


def verdict_truth(just_set, sem):
    """Deterministic ground-truth verdict given current semantic contents of the justification."""
    s = sum(sem[n] for n in just_set)
    return "SAFE" if s % 2 == 0 else "UNSAFE"


def agent_verify(just_set, sem, rng):
    """Non-deterministic agent: k noisy reads, majority vote (the stability gate). Returns (verdict, calls)."""
    truth = verdict_truth(just_set, sem)
    votes = []
    for _ in range(STABILITY_K):
        if rng.random() < NOISE:
            votes.append("UNSAFE" if truth == "SAFE" else "SAFE")
        else:
            votes.append(truth)
    maj = "SAFE" if votes.count("SAFE") >= votes.count("UNSAFE") else "UNSAFE"
    return maj, STABILITY_K


def hash_of(nodes, sem):
    return tuple(sem[n] for n in sorted(nodes))


def run(strategy, capture_c, rng_seed):
    rng = random.Random(rng_seed)
    deps = build_graph(rng)
    sem = [rng.randrange(0, 1000) for _ in range(N_NODES)]      # semantic content
    cosmetic = [0] * N_NODES                                     # cosmetic counter (irrelevant to truth)

    # pick verdict subjects + their true / declared justification sets
    subjects = rng.sample(range(N_NODES), N_VERDICTS)
    verdicts = []
    for vid, s in enumerate(subjects):
        true_j = true_justification(s, deps, TRUE_DEPTH)
        # declared = subject always + each other true premise captured w.p. capture_c
        declared = {s} | {n for n in true_j if n != s and rng.random() < capture_c}
        # structural neighborhood = subject + depth-1 deps
        structural = {s} | set(deps[s])
        verdicts.append({"s": s, "true": true_j, "decl": declared, "struct": structural})

    # initial verification of all verdicts
    agent_calls = 0
    cache = {}
    for vid, v in enumerate(verdicts):
        val, c = agent_verify(v["true"], sem, rng); agent_calls += c
        cache[vid] = {"val": val, "hash": hash_of(v["decl"], sem)}

    stale_samples, dangerous_samples = [], []

    for step in range(STEPS):
        # one edit
        node = rng.randrange(0, N_NODES)
        is_cosmetic = rng.random() < COSMETIC_FRAC
        if is_cosmetic:
            cosmetic[node] += 1               # semantic content UNCHANGED
        else:
            sem[node] = rng.randrange(0, 1000)  # semantic content changed

        # decide what to re-verify
        to_reverify = []
        for vid, v in enumerate(verdicts):
            if strategy == "NAIVE":
                to_reverify.append(vid)
            elif strategy == "STRUCTURAL":
                if node in v["struct"] and not is_cosmetic:
                    to_reverify.append(vid)
            elif strategy == "TMS":
                cur = hash_of(v["decl"], sem)
                if cur != cache[vid]["hash"]:        # fact-keyed early-cutoff invalidation
                    to_reverify.append(vid)
            elif strategy == "TMS-CONSV":
                cur = hash_of(v["decl"] | v["struct"], sem)
                key = cache[vid].get("ckey")
                if key is None or cur != key:
                    to_reverify.append(vid)

        for vid in to_reverify:
            v = verdicts[vid]
            val, c = agent_verify(v["true"], sem, rng); agent_calls += c
            cache[vid]["val"] = val
            cache[vid]["hash"] = hash_of(v["decl"], sem)
            cache[vid]["ckey"] = hash_of(v["decl"] | v["struct"], sem)

        # sample correctness every 20 steps
        if step % 20 == 0:
            stale = dang = 0
            for vid, v in enumerate(verdicts):
                truth = verdict_truth(v["true"], sem)
                if cache[vid]["val"] != truth:
                    stale += 1
                    if cache[vid]["val"] == "SAFE" and truth == "UNSAFE":
                        dang += 1
            stale_samples.append(stale)
            dangerous_samples.append(dang)

    return {
        "calls": agent_calls,
        "avg_stale": statistics.mean(stale_samples),
        "avg_dangerous": statistics.mean(dangerous_samples),
        "max_dangerous": max(dangerous_samples),
    }


def pct(a, base):
    return f"{100*(1 - a/base):+.0f}%"


print(f"Config: {N_NODES} nodes, {N_VERDICTS} verdicts, {STEPS} edits, "
      f"{int(COSMETIC_FRAC*100)}% cosmetic, noise={NOISE}, stability_k={STABILITY_K}\n")

naive = run("NAIVE", 1.0, SEED)
base = naive["calls"]

rows = [
    ("NAIVE (re-check all)",      naive),
    ("STRUCTURAL (depth-1)",      run("STRUCTURAL", 1.0, SEED)),
    ("TMS  capture=100%",         run("TMS", 1.00, SEED)),
    ("TMS  capture=90%",          run("TMS", 0.90, SEED)),
    ("TMS  capture=70%",          run("TMS", 0.70, SEED)),
    ("TMS-CONSV capture=70%",     run("TMS-CONSV", 0.70, SEED)),
]

hdr = f"{'strategy':<24}{'agent_calls':>12}{'vs naive':>10}{'avg_stale':>11}{'avg_danger':>12}{'max_danger':>12}"
print(hdr); print("-" * len(hdr))
for name, r in rows:
    print(f"{name:<24}{r['calls']:>12,}{pct(r['calls'], base):>10}"
          f"{r['avg_stale']:>11.2f}{r['avg_dangerous']:>12.2f}{r['max_dangerous']:>12}")

print(f"\n(stale/danger are out of {N_VERDICTS} verdicts; NAIVE is correct by construction)")

print("\nCapture-completeness sweep for plain TMS (cost vs safety tradeoff):")
print(f"{'capture':>8}{'agent_calls':>14}{'savings':>10}{'avg_stale':>11}{'avg_danger':>12}")
for c in [1.0, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5]:
    r = run("TMS", c, SEED)
    print(f"{c:>8.2f}{r['calls']:>14,}{pct(r['calls'], base):>10}{r['avg_stale']:>11.2f}{r['avg_dangerous']:>12.2f}")

print("\nStability-gate sweep at capture=100% (the 'are verdicts stable enough to cache' axis):")
print(f"{'k':>4}{'agent_calls':>14}{'avg_danger':>12}{'max_danger':>12}")
for k in [1, 3, 5, 9, 15]:
    globals()['STABILITY_K'] = k
    r = run("TMS", 1.0, SEED)
    print(f"{k:>4}{r['calls']:>14,}{r['avg_dangerous']:>12.2f}{r['max_dangerous']:>12}")
