#!/usr/bin/env python3
"""
foreshock BENCHMARK v2 — predictive impact, graded as a classification task.

Spec being tested (foreshock's actual claim): "editing file X impacts its transitive
dependents — and ONLY those." So for each seed X we treat foreshock's blast radius as a
binary classifier of "did this file co-change with X?" and measure PRECISION (suppression)
and RECALL (coverage), against a same-directory BASELINE.

Ground truth = git co-change (evolutionary coupling, Zimmermann et al.). It is a noisy PROXY:
version-bump/format commits add fake coupling; not-yet-co-changed real coupling is invisible.
We mitigate with a max-commit-size filter and report the proxy honestly. (Known limitation:
the graph is built at HEAD, so there is mild temporal leakage vs. predicting strictly forward.)

Output: one JSON scorecard on stdout.  Usage: python3 bench_cochange.py <clone> <analyzer.py>
"""
import sys, os, json, subprocess, collections

clone = os.path.abspath(sys.argv[1]); analyzer = sys.argv[2]
SRC_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java")
MIN_COCHANGE, MAX_COMMIT_FILES, LOOKBACK, MAX_SEEDS = 3, 25, 800, 200

try:
    g = json.loads(subprocess.run(["python3", analyzer, clone, "--graph"],
                                  capture_output=True, text=True, timeout=420).stdout)
except Exception as e:
    print(json.dumps({"error": f"analyzer failed: {e}"})); sys.exit(0)

indexed = set(g.get("files_list", []))
# reverse adjacency: edge [importer a -> imported b] means a depends on b; dependents(b) walks a's.
radj = collections.defaultdict(set)
for a, b in g.get("edges_list", []):
    radj[b].add(a)
# DEPTH-BOUNDED blast radius: foreshock surfaces the NEAR dependents (ranked/suppressed),
# not the entire transitive closure. depth=2 ≈ what you'd actually show inline. Configurable.
DEPTH = int(os.environ.get("FS_DEPTH", "2"))
_dep = {}
def dependents(x):
    if x not in _dep:
        seen, frontier, d = set(), {x}, 0
        while frontier and d < DEPTH:
            nxt = set()
            for n in frontier:
                for a in radj.get(n, ()):
                    if a not in seen:
                        seen.add(a); nxt.add(a)
            frontier = nxt; d += 1
        seen.discard(x)
        _dep[x] = seen
    return _dep[x]

# ---- ground truth: co-change pairs ----
log = subprocess.run(["git", "-C", clone, "log", "--no-merges", "-n", str(LOOKBACK),
                      "--pretty=format:@@C@@", "--name-only"],
                     capture_output=True, text=True, timeout=300).stdout
commits, cur = [], []
for line in log.splitlines():
    if line == "@@C@@":
        if cur: commits.append(cur)
        cur = []
    elif line.strip().endswith(SRC_EXT):
        cur.append(line.strip())
if cur: commits.append(cur)

pair = collections.Counter()
for fs in commits:
    fs = sorted(set(fs))
    if 2 <= len(fs) <= MAX_COMMIT_FILES:
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                pair[(fs[i], fs[j])] += 1
coupled = collections.defaultdict(set)
for (a, b), c in pair.items():
    if c >= MIN_COCHANGE:
        coupled[a].add(b); coupled[b].add(a)

samedir = lambda x: {f for f in indexed if os.path.dirname(f) == os.path.dirname(x)} - {x}

# ---- grade foreshock vs same-dir baseline (micro-averaged) ----
def acc():
    return {"TP": 0, "FP": 0, "FN": 0}
F, B = acc(), acc()
seeds = sorted((x for x in coupled if x in indexed), key=lambda x: -len(coupled[x]))[:MAX_SEEDS]
used, misses = 0, []
for x in seeds:
    truth = (coupled[x] & indexed) - {x}
    if len(truth) < 2:
        continue
    used += 1
    pred = dependents(x) - {x}
    base = samedir(x)
    for acc_, p in ((F, pred), (B, base)):
        acc_["TP"] += len(p & truth); acc_["FP"] += len(p - truth); acc_["FN"] += len(truth - p)
    fn = truth - pred
    if fn and len(misses) < 8:
        misses.append({"seed": x, "missed": sorted(fn)[:3], "n_missed": len(fn)})

def prf(a):
    tp, fp, fn = a["TP"], a["FP"], a["FN"]
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = round(2 * p * r / (p + r), 3) if p and r else None
    return {"precision": round(p, 3) if p is not None else None,
            "recall": round(r, 3) if r is not None else None, "f1": f1}

fs, bl = prf(F), prf(B)

# ---- property checks ----
checks = []
def chk(n, ok, d=""): checks.append({"check": n, "status": "PASS" if ok else "FAIL", "detail": d})
rr = g.get("resolve_rate")
chk("resolve_rate>=0.85", rr is not None and rr >= 0.85, f"resolve_rate={rr}")
chk("graph_nonempty", g.get("edges", 0) > 0, f"edges={g.get('edges')}")
chk("enough_seeds", used >= 8, f"{used} graded seeds")
beats = fs["f1"] is not None and bl["f1"] is not None and fs["f1"] > bl["f1"]
chk("beats_samedir_baseline", beats, f"foreshock f1={fs['f1']} vs baseline f1={bl['f1']}")

score = sum(1 for c in checks if c["status"] == "PASS")
print(json.dumps({
    "lang": g.get("lang"), "files": g.get("files"), "edges": g.get("edges"), "resolve_rate": rr,
    "bench": {
        "graded_seeds": used, "commits": len(commits),
        "foreshock": fs, "baseline_samedir": bl,
        "lift_f1": round(fs["f1"] - bl["f1"], 3) if fs["f1"] is not None and bl["f1"] is not None else None,
        "top_misses": misses,   # seeds whose real co-changed files foreshock did NOT flag (coverage failures)
    },
    "checks": checks, "score": f"{score}/{len(checks)}",
    "caveats": "co-change = noisy proxy; graph built at HEAD (mild temporal leakage); micro-avg over seeds",
}))
