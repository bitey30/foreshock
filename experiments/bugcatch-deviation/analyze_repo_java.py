#!/usr/bin/env python3
"""
Java foreshock extractor (v1). Maps package+filename -> fully-qualified class, resolves
`import a.b.C;`, builds blast-radius, and finds enum/string switch dispatch sites.
Note: modern Java (17+ sealed + switch) has some compiler exhaustiveness, so value is
mainly in enum-switch-without-default and registry-style dispatch.
Usage: python3 analyze_repo_java.py <repo_root>
"""
import sys, os, re, glob, json, collections

root = os.path.abspath(sys.argv[1])
EXCL = re.compile(r"/(target|build|out|\.gradle|node_modules)/|/src/test/|Test\.java$|Tests\.java$")
files = [f for f in glob.glob(os.path.join(root, "**", "*.java"), recursive=True) if not EXCL.search(f)]
text = {f: open(f, errors="ignore").read() for f in files}

# fully-qualified class name -> file
fqcn2file = {}
for f, src in text.items():
    pkg = ""
    m = re.search(r"^\s*package\s+([\w.]+)\s*;", src, re.M)
    if m: pkg = m.group(1)
    cls = os.path.basename(f)[:-5]   # strip .java
    fqcn = f"{pkg}.{cls}" if pkg else cls
    fqcn2file[fqcn] = f
top_pkgs = {k.split(".")[0] for k in fqcn2file}

def resolve(imp):
    if imp.endswith(".*"):
        prefix = imp[:-2] + "."
        # wildcard: link to first class in that package (coarse)
        for fq, fl in fqcn2file.items():
            if fq.startswith(prefix): return fl
        return None
    return fqcn2file.get(imp)

deps = collections.defaultdict(set)
unresolved = total_internalish = 0
for f, src in text.items():
    for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", src, re.M):
        imp = m.group(1)
        if imp.split(".")[0] not in top_pkgs:
            continue  # external / stdlib
        total_internalish += 1
        t = resolve(imp)
        if t and t != f: deps[f].add(t)
        elif not t: unresolved += 1

rev = collections.defaultdict(set)
for a, bs in deps.items():
    for b in bs: rev[b].add(a)
def dependents(t):
    seen, st = set(), [t]
    while st:
        x = st.pop()
        if x in seen: continue
        seen.add(x); st.extend(rev.get(x, ()))
    seen.discard(t); return len(seen)
edges = sum(len(v) for v in deps.values())
fanin = sorted(((dependents(f), f) for f in text), reverse=True)[:6]

# switch dispatch: >=3 `case X:` (enum constants are UNQUOTED in Java) or case "str":
def members(src):
    return set(re.findall(r"""case\s+([A-Z_][A-Z0-9_]*|"[a-z_][a-z0-9_]*")\s*(?:->|:)""", src))
sites = sorted(((len(members(src)), f) for f, src in text.items() if len(members(src)) >= 3), reverse=True)

rel = lambda f: os.path.relpath(f, root)
out = {
    "lang": "java", "files": len(files), "edges": edges,
    "internalish_imports": total_internalish, "unresolved_internalish": unresolved,
    "resolve_rate": round(1 - unresolved / total_internalish, 3) if total_internalish else None,
    "aliases": [], "top_fanin": [[n, rel(f)] for n, f in fanin if n > 0],
    "completeness_sites": [[n, rel(f)] for n, f in sites[:6]],
}
if "--graph" in sys.argv:
    out["files_list"] = [rel(f) for f in files]
    out["edges_list"] = [[rel(a), rel(b)] for a, bs in deps.items() for b in bs]
print(json.dumps(out))
