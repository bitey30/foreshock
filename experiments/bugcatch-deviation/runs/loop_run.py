#!/usr/bin/env python3
"""
One iteration of the foreshock hardening loop: pick the next varied repo, shallow-clone,
run the analyzer, append results to LOG.md + runs.jsonl, delete the clone. Self-limiting at LIMIT.
"""
import os, sys, json, subprocess, shutil

HOME = os.path.expanduser("~")
FS = os.path.join(HOME, "foreshock")
RUNS = os.path.join(FS, "runs"); os.makedirs(RUNS, exist_ok=True)
LOG = os.path.join(RUNS, "LOG.md"); JSONL = os.path.join(RUNS, "runs.jsonl")
LIMIT = 10

# varied across LANGUAGES and styles (ts / py / java interleaved), kept reasonably sized for 10-min windows
POOL = [
    "https://github.com/statelyai/xstate",            # ts  state-machine union dispatch
    "https://github.com/pallets/flask",               # py  web framework
    "https://github.com/sindresorhus/ky",             # ts  tiny http client
    "https://github.com/google/gson",                 # java json lib
    "https://github.com/psf/requests",                # py  http lib
    # --- runs 6-10: BIG / COMPLEX repos (monorepos, frameworks, DI/reflection-heavy) ---
    "https://github.com/django/django",                    # py   large mature framework (run 6)
    "https://github.com/nestjs/nest",                      # ts   big DI monorepo — import graph won't see DI (run 7)
    "https://github.com/spring-projects/spring-framework", # java huge, reflection/DI heavy (run 8)
    "https://github.com/vuejs/core",                       # ts   reactivity + compiler monorepo (run 9)
    "https://github.com/fastify/fastify",                  # JS   plugin-based web framework, dense history — gradeable JS (run 10)
]

ANALYZER = {"ts": "analyze_repo.py", "py": "analyze_repo_py.py", "java": "analyze_repo_java.py"}

def detect_lang(clone):
    import collections as _c
    c = _c.Counter()
    for dp, _, fns in os.walk(clone):
        if any(x in dp for x in ("/node_modules/", "/.git/", "/target/", "/build/", "/.venv/", "/dist/")):
            continue
        for fn in fns:
            if fn.endswith((".ts", ".tsx", ".js", ".jsx")): c["ts"] += 1
            elif fn.endswith(".py"): c["py"] += 1
            elif fn.endswith(".java"): c["java"] += 1
    return c.most_common(1)[0][0] if c else "ts"

def count():
    return sum(1 for _ in open(JSONL)) if os.path.exists(JSONL) else 0

n = count()
if n >= LIMIT:
    print(f"DONE: {n}/{LIMIT} runs complete — STOP THE LOOP (CronDelete).")
    sys.exit(0)

repo = POOL[n % len(POOL)]
name = repo.rstrip("/").split("/")[-1]
clone = f"/tmp/fs_clone_{n}_{name}"
shutil.rmtree(clone, ignore_errors=True)

res = {"run": n + 1, "repo": repo}
try:
    # depth 500 = enough history for the co-change benchmark, still bounded
    cp = subprocess.run(["git", "clone", "--depth", "500", "-q", repo, clone],
                        capture_output=True, text=True, timeout=900)
    if cp.returncode != 0:
        res["error"] = "clone failed: " + (cp.stderr.strip()[:200] or "unknown")
    else:
        lang = detect_lang(clone)
        res["lang"] = lang
        analyzer = os.path.join(FS, "engine", ANALYZER[lang])
        a = subprocess.run(["python3", os.path.join(FS, "engine", "bench_cochange.py"), clone, analyzer],
                           capture_output=True, text=True, timeout=900)
        if a.returncode != 0:
            res["error"] = "bench failed: " + a.stderr.strip()[:300]
        else:
            res.update(json.loads(a.stdout))
except Exception as e:
    res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
finally:
    shutil.rmtree(clone, ignore_errors=True)

with open(JSONL, "a") as f:
    f.write(json.dumps(res) + "\n")

def md(r):
    if "error" in r:
        return f"\n### Run {r['run']}: {r['repo']}\n- ❌ {r['error']}\n"
    b = r.get("bench", {})
    fs, bl = b.get("foreshock", {}), b.get("baseline_samedir", {})
    s = f"\n### Run {r['run']}: {r['repo']}  [{r.get('lang','?')}]\n"
    s += (f"- graph: files={r.get('files')}, edges={r.get('edges')}, resolve_rate={r.get('resolve_rate')}\n")
    s += (f"- **foreshock: P={fs.get('precision')} R={fs.get('recall')} F1={fs.get('f1')}** "
          f"| baseline(same-dir): P={bl.get('precision')} R={bl.get('recall')} F1={bl.get('f1')} "
          f"| lift_F1={b.get('lift_f1')}  ({b.get('graded_seeds')} seeds, {b.get('commits')} commits)\n")
    s += "- checks " + r.get("score", "") + ": " + ", ".join(
        f"{c['check']}={c['status']}" for c in r.get("checks", [])) + "\n"
    miss = b.get("top_misses", [])
    if miss:
        s += "- ⚠️ COVERAGE FAILURES (co-changed with seed but NOT in foreshock's blast radius):\n"
        for m in miss[:5]:
            s += f"    - {m['seed'].split('/')[-1]} ⇸ {', '.join(x.split('/')[-1] for x in m['missed'])} (+{m['n_missed']})\n"
    return s

if not os.path.exists(LOG):
    open(LOG, "w").write("# foreshock hardening loop\n\nVaried public repos analyzed to find where "
                         "foreshock breaks. Each run: clone → analyze → log → delete.\n")
open(LOG, "a").write(md(res))

print(f"RUN {res['run']}/{LIMIT} — {repo}")
print(md(res))
if res["run"] >= LIMIT:
    print("\nThat was the FINAL run — STOP THE LOOP (CronDelete) and report the summary.")
