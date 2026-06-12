#!/usr/bin/env python3
"""
General foreshock analyzer for ANY TS/JS repo. Auto-detects tsconfig path aliases,
builds the import graph, and reports blast-radius + variant-completeness — plus a
RESOLVE_RATE metric (how many internal imports actually resolved) to surface resolver
gaps, the #1 generality blocker. Output: one JSON object on stdout.

Usage: python3 analyze_repo.py <repo_root>
"""
import sys, os, re, glob, json, collections

root = os.path.abspath(sys.argv[1])

# ---- alias map from tsconfig(s) ----
aliases = {}  # prefix -> [abs target dirs]
for tsc in glob.glob(os.path.join(root, "**", "tsconfig*.json"), recursive=True):
    if "/node_modules/" in tsc:
        continue
    try:
        raw = open(tsc, errors="ignore").read()
    except Exception:
        continue
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"//[^\n]*", "", raw)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)  # trailing commas
    try:
        cfg = json.loads(raw)
    except Exception:
        continue
    co = cfg.get("compilerOptions", {}) or {}
    bdir = os.path.dirname(tsc)
    base = os.path.normpath(os.path.join(bdir, co["baseUrl"])) if co.get("baseUrl") else bdir
    for k, v in (co.get("paths") or {}).items():
        pre = k[:-1] if k.endswith("*") else k
        tgts = []
        for t in (v if isinstance(v, list) else [v]):
            t = t[:-1] if t.endswith("*") else t
            tgts.append(os.path.normpath(os.path.join(base, t)))
        aliases.setdefault(pre, [])
        for t in tgts:
            if t not in aliases[pre]:
                aliases[pre].append(t)

# ---- index source files ----
EXTS = ("ts", "tsx", "js", "jsx", "mjs", "cjs")
EXCL = re.compile(r"/(node_modules|dist|build|\.next|out|coverage|vendor|fixtures?)/"
                  r"|\.d\.ts$|\.(test|spec)\.[tj]sx?$|/tests?/|/__tests__/")
files = []
for ext in EXTS:
    files += glob.glob(os.path.join(root, "**", f"*.{ext}"), recursive=True)
files = [f for f in files if not EXCL.search(f)]
fileset = set(files)
text = {}
for f in files:
    try:
        text[f] = open(f, errors="ignore").read()
    except Exception:
        text[f] = ""

def try_paths(base):
    base = re.sub(r"\.(js|jsx|mjs|cjs|ts|tsx)$", "", base)
    for c in (base+".ts", base+".tsx", base+".js", base+".jsx",
              os.path.join(base, "index.ts"), os.path.join(base, "index.tsx"),
              os.path.join(base, "index.js"), os.path.join(base, "index.jsx"), base):
        if c in fileset:
            return c
    return None

def resolve(importer, spec):
    if spec.startswith("."):
        return try_paths(os.path.normpath(os.path.join(os.path.dirname(importer), spec)))
    for pre, tgts in aliases.items():
        if spec == pre.rstrip("/") or spec.startswith(pre):
            rest = spec[len(pre):]
            for tg in tgts:
                r = try_paths(os.path.normpath(os.path.join(tg, rest)))
                if r:
                    return r
            return None
    return None  # external package

deps = collections.defaultdict(set)
unresolved = total_internalish = 0
imp_re = re.compile(r"""import[^'"]*?from\s*['"]([^'"]+)['"]""")
req_re = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
for f, src in text.items():
    for spec in imp_re.findall(src) + req_re.findall(src):
        internalish = spec.startswith(".") or any(spec == p.rstrip("/") or spec.startswith(p) for p in aliases)
        if not internalish:
            continue
        total_internalish += 1
        t = resolve(f, spec)
        if t and t != f:
            deps[f].add(t)
        elif not t:
            unresolved += 1

rev = collections.defaultdict(set)
for a, bs in deps.items():
    for b in bs:
        rev[b].add(a)

def dependents(t):
    seen, st = set(), [t]
    while st:
        x = st.pop()
        if x in seen:
            continue
        seen.add(x); st.extend(rev.get(x, ()))
    seen.discard(t)
    return len(seen)

edges = sum(len(v) for v in deps.values())
fanin = sorted(((dependents(f), f) for f in text), reverse=True)[:6]

defs = set(re.findall(r"""\b(?:type|kind|status|variant|tag):\s*['"]([a-z_][a-z0-9_]*)['"]""",
                      "\n".join(text.values())))
sites = []
for f, src in text.items():
    m = set(re.findall(r"""case\s+['"]([a-z_][a-z0-9_]*)['"]""", src)) & defs
    m |= set(re.findall(r"""\.(?:type|kind|status|tag)\s*===?\s*['"]([a-z_][a-z0-9_]*)['"]""", src)) & defs
    if len(m) >= 3:
        sites.append((len(m), f))
sites.sort(reverse=True)

rel = lambda f: os.path.relpath(f, root)
out = {
    "lang": "ts", "files": len(files), "edges": edges,
    "internalish_imports": total_internalish, "unresolved_internalish": unresolved,
    "resolve_rate": round(1 - unresolved / total_internalish, 3) if total_internalish else None,
    "aliases": list(aliases.keys()),
    "top_fanin": [[n, rel(f)] for n, f in fanin if n > 0],
    "completeness_sites": [[n, rel(f)] for n, f in sites[:6]],
}
if "--graph" in sys.argv:
    out["files_list"] = [rel(f) for f in files]
    out["edges_list"] = [[rel(a), rel(b)] for a, bs in deps.items() for b in bs]
    out["domain"] = sorted(defs)
print(json.dumps(out))
