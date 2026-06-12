#!/usr/bin/env python3
"""
foreshock CALIBRATION benchmark — does per-codebase co-change history break the static
precision ceiling, and is the dependency graph still needed?  Leakage-free TEMPORAL SPLIT.

  TRAIN = older 70% of commits → learn co-change weight[(a,b)] = times a,b changed together.
  TEST  = newer 30%, bug-fix clusters only → the future we must predict.

Three predictors, top-k each, graded P/R/F1 on TEST bug clusters:
  STATIC  — structural neighborhood (graph only), ranked by shared-dependency similarity.
  HIST    — files ranked purely by learned train co-change weight with the seed (no graph).
  HYBRID  — structural-neighborhood candidates, ranked by learned co-change weight (graph+history).
  + baseline same-dir.

Usage: python3 bench_calibrated.py <clone> <analyzer.py>
"""
import sys, os, re, json, subprocess, collections

clone, analyzer = os.path.abspath(sys.argv[1]), sys.argv[2]
SRC_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java")
FIX_RE = re.compile(r"\b(fix(e[sd])?|bug(s|fix)?|broke|broken|regress\w*|revert|crash|hotfix)\b", re.I)
DEPTH, MAX_FILES, K, TRAIN_FRAC = 2, 20, 5, 0.70

g = json.loads(subprocess.run(["python3", analyzer, clone, "--graph"],
                              capture_output=True, text=True, timeout=600).stdout)
indexed = set(g.get("files_list", []))
uadj = collections.defaultdict(set)
for a, b in g.get("edges_list", []):
    uadj[a].add(b); uadj[b].add(a)
def jacc(a, b):
    if not a or not b: return 0.0
    i = len(a & b); return i / (len(a) + len(b) - i)
def neighborhood(x):
    dist, frontier, d = {x: 0}, [x], 0
    while frontier and d < DEPTH:
        nxt = []
        for n in frontier:
            for m in uadj.get(n, ()):
                if m not in dist: dist[m] = d + 1; nxt.append(m)
        frontier = nxt; d += 1
    return {m for m in dist if m != x}

# ---- commits, chronological, split ----
log = subprocess.run(["git", "-C", clone, "log", "--no-merges", "--reverse",
                      "--pretty=format:@@C@@%s", "--name-only"],
                     capture_output=True, text=True, timeout=300).stdout
commits, cur, subj = [], [], ""
for line in log.splitlines():
    if line.startswith("@@C@@"):
        if cur: commits.append((subj, cur))
        subj, cur = line[5:], []
    elif line.strip().endswith(SRC_EXT):
        cur.append(line.strip())
if cur: commits.append((subj, cur))
split = int(len(commits) * TRAIN_FRAC)
train, test = commits[:split], commits[split:]

# ---- TRAIN: learned co-change weights ----
weight = collections.defaultdict(int)
for _, fs in train:
    fs = sorted(set(f for f in fs if f in indexed))
    if 2 <= len(fs) <= MAX_FILES:
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                weight[(fs[i], fs[j])] += 1
def w(a, b): return weight.get((a, b), 0) + weight.get((b, a), 0)
cocphist = collections.defaultdict(set)
for (a, b) in weight:
    cocphist[a].add(b); cocphist[b].add(a)

# ---- TEST: future bug clusters ----
clusters = []
for subj, fs in test:
    if FIX_RE.search(subj):
        idx = sorted(set(f for f in fs if f in indexed))
        if 2 <= len(idx) <= MAX_FILES:
            clusters.append(idx)

samedir = lambda x: {f for f in indexed if os.path.dirname(f) == os.path.dirname(x)} - {x}

def predict(mode, seed):
    if mode == "STATIC":
        cand = neighborhood(seed)
        return set(sorted(cand, key=lambda m: -jacc(uadj.get(seed, set()), uadj.get(m, set())))[:K])
    if mode == "HIST":
        cand = [f for f in cocphist.get(seed, ()) if f in indexed]
        return set(sorted(cand, key=lambda m: -w(seed, m))[:K])
    if mode == "HYBRID":
        cand = neighborhood(seed)
        return set(sorted(cand, key=lambda m: (-w(seed, m),
                          -jacc(uadj.get(seed, set()), uadj.get(m, set()))))[:K])
    if mode == "BASE":
        return set(list(samedir(seed))[:K]) if False else samedir(seed)

modes = ["STATIC", "HIST", "HYBRID", "BASE"]
acc = {m: {"TP": 0, "FP": 0, "FN": 0} for m in modes}
used = 0
for cl in clusters:
    for seed in cl:
        truth = set(cl) - {seed}
        used += 1
        for m in modes:
            p = predict(m, seed)
            acc[m]["TP"] += len(p & truth); acc[m]["FP"] += len(p - truth); acc[m]["FN"] += len(truth - p)

def prf(a):
    tp, fp, fn = a["TP"], a["FP"], a["FN"]
    P = tp / (tp + fp) if tp + fp else None
    R = tp / (tp + fn) if tp + fn else None
    F = round(2 * P * R / (P + R), 3) if P and R else None
    return {"P": round(P, 3) if P is not None else None,
            "R": round(R, 3) if R is not None else None, "F1": F}

res = {m: prf(acc[m]) for m in modes}
winner = max([m for m in ["STATIC", "HIST", "HYBRID"] if res[m]["F1"] is not None],
             key=lambda m: res[m]["F1"], default=None)
print(json.dumps({
    "lang": g.get("lang"), "files": g.get("files"),
    "train_commits": len(train), "test_commits": len(test),
    "test_bug_clusters": len(clusters), "graded_seeds": used,
    "STATIC": res["STATIC"], "HIST": res["HIST"], "HYBRID": res["HYBRID"], "baseline_samedir": res["BASE"],
    "winner": winner,
    "caveat": "leakage-free temporal split (train older 70%, test newer 30%); cold-start seeds hurt HIST",
}))
