#!/usr/bin/env python3
"""
foreshock BUG-CATCH benchmark — does foreshock catch the file a real bug proved you missed?

Ground truth = REAL BUGS, not casual co-change. A commit whose message says fix/bug/regression/
revert that changes >=2 source files is a *bug-validated coupling cluster*: those files were
provably coupled enough that fixing the bug required touching all of them. The test:

  For each bug cluster, pick a seed file. Does foreshock's structural neighborhood of the seed
  (bounded undirected reachability — "files it would surface as related") contain the OTHER files
  in the cluster?  YES = foreshock would have pointed you at the file you missed = a caught bug.

  bug-catch recall = of bug-coupled file pairs, fraction foreshock connects.   ← THE number.
  precision / neighborhood-size = the noise cost (suppression quality).
  baseline = same-directory heuristic. foreshock must beat it.

Caveat (stated, not hidden): graph built at HEAD, so structure may differ from the bug's era
(mild temporal leakage); fix-message mining is heuristic; this is a lower bound on real coupling.

Usage: python3 bench_bugcatch.py <clone> <analyzer.py>
"""
import sys, os, re, json, subprocess, collections

clone, analyzer = os.path.abspath(sys.argv[1]), sys.argv[2]
SRC_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java")
FIX_RE = re.compile(r"\b(fix(e[sd])?|bug(s|fix)?|broke|broken|regress\w*|revert|crash|hotfix)\b", re.I)
DEPTH, MAX_FILES, LOOKBACK, MAX_SEEDS = 2, 20, 4000, 400

# ---- foreshock graph (undirected, bounded) ----
try:
    g = json.loads(subprocess.run(["python3", analyzer, clone, "--graph"],
                                  capture_output=True, text=True, timeout=600).stdout)
except Exception as e:
    print(json.dumps({"error": f"analyzer failed: {e}"})); sys.exit(0)

indexed = set(g.get("files_list", []))
uadj = collections.defaultdict(set)
for a, b in g.get("edges_list", []):
    uadj[a].add(b); uadj[b].add(a)
TOPK = int(os.environ.get("FS_TOPK", "0"))   # 0 = raw neighborhood; >0 = ranked top-k
RANK = int(os.environ.get("FS_RANK", "2"))   # 0=hub-first(naive) 1=anti-hub 2=structural-sim(best on complex) 3=blend
deg = {f: len(uadj[f]) for f in uadj}
import math
def jacc(a, b):
    if not a or not b: return 0.0
    i = len(a & b); return i / (len(a) + len(b) - i)
def keyfn(x, m, dist):
    d, dg = dist[m], deg.get(m, 0)
    if RANK == 1:   # anti-hub: prefer specific (low-degree) candidates
        return (d, dg)
    if RANK == 2:   # structural similarity: share dependencies with the seed
        return (d, -jacc(uadj.get(x, set()), uadj.get(m, set())))
    if RANK == 3:   # blend: similarity, dampened by hub-ness (IDF-like)
        sim = jacc(uadj.get(x, set()), uadj.get(m, set()))
        return (d, -(sim / math.log2(2 + dg)))
    return (d, -dg)  # 0 = naive (hub-first)
_nb = {}
def neighbors(x):
    if x not in _nb:
        dist, frontier, d = {x: 0}, [x], 0
        while frontier and d < DEPTH:
            nxt = []
            for n in frontier:
                for m in uadj.get(n, ()):
                    if m not in dist:
                        dist[m] = d + 1; nxt.append(m)
            frontier = nxt; d += 1
        cand = [m for m in dist if m != x]
        if TOPK > 0:
            cand.sort(key=lambda m: keyfn(x, m, dist))
            cand = cand[:TOPK]
        _nb[x] = set(cand)
    return _nb[x]
samedir = lambda x: {f for f in indexed if os.path.dirname(f) == os.path.dirname(x)} - {x}

# ---- mine bug-validated coupling clusters from fix commits ----
log = subprocess.run(["git", "-C", clone, "log", "--no-merges", "-n", str(LOOKBACK),
                      "--pretty=format:@@C@@%s", "--name-only"],
                     capture_output=True, text=True, timeout=300).stdout
clusters, n_fix = [], 0
cur_files, is_fix = [], False
def flush():
    global n_fix
    if is_fix:
        n_fix += 1
        src = [f for f in set(cur_files) if f.endswith(SRC_EXT)]
        idx = [f for f in src if f in indexed]
        if len(idx) >= 2 and len(src) <= MAX_FILES:
            clusters.append(sorted(idx))
for line in log.splitlines():
    if line.startswith("@@C@@"):
        flush()
        cur_files, is_fix = [], bool(FIX_RE.search(line[5:]))
    elif line.strip():
        cur_files.append(line.strip())
flush()

# ---- grade: foreshock neighborhood vs bug clusters, vs same-dir baseline ----
F = {"TP": 0, "FP": 0, "FN": 0}; B = {"TP": 0, "FP": 0, "FN": 0}
nbsizes, clsizes, used, misses = [], [], 0, []
seeds = 0
for cl in clusters:
    for seed in cl:
        if seeds >= MAX_SEEDS: break
        truth = set(cl) - {seed}
        pred = neighbors(seed)
        base = samedir(seed)
        F["TP"] += len(pred & truth); F["FP"] += len(pred - truth); F["FN"] += len(truth - pred)
        B["TP"] += len(base & truth); B["FP"] += len(base - truth); B["FN"] += len(truth - base)
        nbsizes.append(len(pred)); clsizes.append(len(truth)); used += 1; seeds += 1
        fn = truth - pred
        if fn and len(misses) < 8:
            misses.append({"seed": seed, "missed": sorted(fn)[:3], "n": len(fn)})

def prf(a):
    tp, fp, fn = a["TP"], a["FP"], a["FN"]
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = round(2 * p * r / (p + r), 3) if p and r else None
    return {"precision": round(p, 3) if p is not None else None,
            "recall": round(r, 3) if r is not None else None, "f1": f1}
fs, bl = prf(F), prf(B)

checks = []
def chk(n, ok, d=""): checks.append({"check": n, "status": "PASS" if ok else "FAIL", "detail": d})
chk("enough_bug_clusters", len(clusters) >= 10, f"{len(clusters)} clusters from {n_fix} fix-commits")
chk("graded_seeds>=15", used >= 15, f"{used} seeds")
chk("catches_real_coupling", fs["recall"] is not None and fs["recall"] >= 0.30,
    f"bug-catch recall={fs['recall']}")
chk("beats_samedir", fs["recall"] is not None and bl["recall"] is not None and fs["recall"] > bl["recall"],
    f"foreshock R={fs['recall']} vs baseline R={bl['recall']}")
score = sum(1 for c in checks if c["status"] == "PASS")

print(json.dumps({
    "lang": g.get("lang"), "files": g.get("files"), "edges": g.get("edges"), "resolve_rate": g.get("resolve_rate"),
    "bugcatch": {
        "fix_commits": n_fix, "bug_clusters": len(clusters), "graded_seeds": used,
        "avg_cluster_size": round(sum(clsizes)/len(clsizes), 2) if clsizes else None,
        "avg_neighborhood_size": round(sum(nbsizes)/len(nbsizes), 2) if nbsizes else None,
        "foreshock": fs, "baseline_samedir": bl,
        "headline_bug_catch_recall": fs["recall"],
        "top_misses": misses,   # real bug-coupled files foreshock did NOT connect = where it would have failed you
    },
    "checks": checks, "score": f"{score}/{len(checks)}",
    "caveats": "fix-message mining heuristic; graph at HEAD (mild leakage); undirected depth-2 neighborhood",
}))
