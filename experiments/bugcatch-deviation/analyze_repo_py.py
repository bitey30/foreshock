#!/usr/bin/env python3
"""
Python foreshock extractor. Builds the import graph (absolute + relative imports),
blast-radius, and string-dispatch completeness sites. Output: one JSON object on stdout.
Python is a high-value target: no compiler exhaustiveness, so 'forgot a case' bugs go uncaught.
Usage: python3 analyze_repo_py.py <repo_root>
"""
import sys, os, re, glob, json, collections

root = os.path.abspath(sys.argv[1])
EXCL = re.compile(r"/(\.venv|venv|site-packages|node_modules|build|dist|\.tox|tests?|__pycache__)/"
                  r"|/(test_|conftest)|_test\.py$")
files = [f for f in glob.glob(os.path.join(root, "**", "*.py"), recursive=True) if not EXCL.search(f)]
fileset = set(files)
text = {f: open(f, errors="ignore").read() for f in files}

# module dotted-path -> file
def mod_of(f):
    relp = os.path.relpath(f, root)
    parts = relp[:-3].split(os.sep)          # strip .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)
mod2file = {}
for f in files:
    mod2file[mod_of(f)] = f
top_pkgs = {m.split(".")[0] for m in mod2file}   # internal top-level names

def resolve_abs(dotted):
    # try full module, then drop trailing symbol (from a.b import c -> a.b)
    if dotted in mod2file:
        return mod2file[dotted]
    parent = dotted.rsplit(".", 1)[0]
    return mod2file.get(parent)

def resolve_rel(f, level, mod):
    pkg = mod_of(f).split(".")
    # in a module file, level 1 = current package (dir); each extra level goes up
    base = pkg[:-1] if not f.endswith("__init__.py") else pkg[:]
    up = level - 1
    base = base[:len(base) - up] if up <= len(base) else []
    target = ".".join([p for p in base + (mod.split(".") if mod else []) if p])
    return resolve_abs(target) if target else None

deps = collections.defaultdict(set)
unresolved = total_internalish = 0
for f, src in text.items():
    for m in re.finditer(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+", src, re.M):
        dots, mod = m.group(1), m.group(2)
        if dots:  # relative -> always internal
            total_internalish += 1
            t = resolve_rel(f, len(dots), mod)
            if t and t != f: deps[f].add(t)
            elif not t: unresolved += 1
        elif mod and mod.split(".")[0] in top_pkgs:
            total_internalish += 1
            t = resolve_abs(mod)
            if t and t != f: deps[f].add(t)
            elif not t: unresolved += 1
    for m in re.finditer(r"^\s*import\s+([\w.]+)", src, re.M):
        mod = m.group(1)
        if mod.split(".")[0] in top_pkgs:
            total_internalish += 1
            t = resolve_abs(mod)
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

# string-dispatch completeness: sites with >=3 distinct string literals in == / case / dict keys
def members(src):
    s = set(re.findall(r"""==\s*['"]([a-z_][a-z0-9_]*)['"]""", src))
    s |= set(re.findall(r"""case\s+['"]([a-z_][a-z0-9_]*)['"]""", src))
    return s
sites = sorted(((len(members(src)), f) for f, src in text.items() if len(members(src)) >= 3), reverse=True)

rel = lambda f: os.path.relpath(f, root)
out = {
    "lang": "python", "files": len(files), "edges": edges,
    "internalish_imports": total_internalish, "unresolved_internalish": unresolved,
    "resolve_rate": round(1 - unresolved / total_internalish, 3) if total_internalish else None,
    "aliases": [], "top_fanin": [[n, rel(f)] for n, f in fanin if n > 0],
    "completeness_sites": [[n, rel(f)] for n, f in sites[:6]],
}
if "--graph" in sys.argv:
    out["files_list"] = [rel(f) for f in files]
    out["edges_list"] = [[rel(a), rel(b)] for a, bs in deps.items() for b in bs]
print(json.dumps(out))
