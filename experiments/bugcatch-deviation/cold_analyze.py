"""
COLD test: run the impact-engine mechanism on repos I did NOT write, with invariants
I did NOT hand-craft. Everything below is auto-derived from source.

Two auto-derived analyses, both generalizations of what we validated on a private codebase:

(A) DEPENDENCY CONE / blast radius  — parse imports → file graph → highest-fan-in core files
    and their transitive dependents (what a change to them ripples into).

(B) CATEGORY-COMPLETENESS (auto-derived invariant) — discover a discriminated-union "category"
    purely from source: the set of string literals that appear BOTH as `type: "X"` (definitions)
    AND as `case "X"` / `=== "X"` (consumers). Each consumer that enumerates many members is a
    completeness site: ADDING a member impacts all of them, usually with no compiler enforcement.
    This is the exact bug class we found in a private codebase, auto-instantiated on unseen code.
"""
import sys, os, re, glob
from collections import defaultdict, Counter

ROOT = sys.argv[1]
LABEL = sys.argv[2] if len(sys.argv) > 2 else ROOT

def srcfiles(root):
    out = []
    for f in glob.glob(os.path.join(root, "**", "*.ts"), recursive=True):
        if f.endswith(".d.ts"): continue
        if re.search(r"/(test|tests|__tests__|node_modules|dist)/", f): continue
        if re.search(r"\.(test|spec)\.ts$", f): continue
        out.append(f)
    return out

FILES = srcfiles(ROOT)
text = {f: open(f, errors="ignore").read() for f in FILES}
rel = lambda f: os.path.relpath(f, ROOT)

# ---------- (A) import graph + cones ----------
def resolve(importer, spec):
    if not spec.startswith("."): return None
    spec = re.sub(r"\.(js|jsx|mjs|cjs)$", "", spec)   # ESM: import "./foo.js" -> foo.ts
    base = os.path.normpath(os.path.join(os.path.dirname(importer), spec))
    for cand in (base + ".ts", os.path.join(base, "index.ts"), base):
        if cand in text: return cand
    return None

deps = defaultdict(set)        # file -> files it imports
for f, src in text.items():
    for spec in re.findall(r"""import[^'"]*?from\s*['"]([^'"]+)['"]""", src):
        t = resolve(f, spec)
        if t and t != f: deps[f].add(t)

rev = defaultdict(set)
for a, bs in deps.items():
    for b in bs: rev[b].add(a)

def dependents(target):
    seen, stack = set(), [target]
    while stack:
        x = stack.pop()
        if x in seen: continue
        seen.add(x); stack.extend(rev.get(x, ()))
    seen.discard(target); return seen

fanin = sorted(text, key=lambda f: len(dependents(f)), reverse=True)

print("="*78)
print(f"[{LABEL}]  {len(FILES)} source files,  {sum(len(v) for v in deps.values())} import edges")
print("="*78)
print("\n(A) DEPENDENCY BLAST RADIUS — highest-fan-in files (a change here ripples widest):")
for f in fanin[:6]:
    d = dependents(f)
    if not d: break
    print(f"  {len(d):>3} dependents  ←  {rel(f)}")

# ---------- (B) auto-derived category-completeness ----------
# domain = literals used as `type: "X"` (definitions)
defs = Counter(re.findall(r"""\btype:\s*['"]([a-z_][a-z0-9_]*)['"]""", "\n".join(text.values())))
domain = {m for m, c in defs.items() if c >= 1}

# consumers = literals used as `case "X"` or `=== "X"` / `== "X"`
def consumer_members(src):
    cs = set(re.findall(r"""case\s+['"]([a-z_][a-z0-9_]*)['"]""", src))
    cs |= set(re.findall(r"""\.type\s*===?\s*['"]([a-z_][a-z0-9_]*)['"]""", src))
    return cs & domain

# de-facto discriminant = domain members that are actually consumed somewhere
consumed = set()
sites = []   # (file, members_handled)
for f, src in text.items():
    m = consumer_members(src)
    if len(m) >= 3:                      # a real dispatch site enumerates several kinds
        sites.append((f, m)); consumed |= m

discriminant = sorted(consumed)
print(f"\n(B) AUTO-DISCOVERED CATEGORY (discriminated union), inferred purely from source:")
print(f"    {len(discriminant)} members consumed by dispatch sites, out of {len(domain)} defined `type:` literals")
print(f"    sample members: {discriminant[:14]}")

if sites:
    print(f"\n    COMPLETENESS CONSUMERS — adding a new member impacts every one of these:")
    for f, m in sorted(sites, key=lambda s: -len(s[1]))[:8]:
        missing = sorted(set(discriminant) - m)
        print(f"      handles {len(m):>2}/{len(discriminant)}  {rel(f)}")
    # the addition-impact set for a hypothetical new member:
    impacted = [rel(f) for f, _ in sites]
    print(f"\n    => ADDITION-IMPACT BLAST RADIUS of one new category member: {len(impacted)} dispatch site(s),")
    print(f"       none of which has a dependency edge to the new member. A naive forward-slice = 0.")
    # flag registry-style consumers (runtime-throw / silent-miss risk)
    reg = [rel(f) for f, src in text.items() if re.search(r"\[\s*\w*def\w*\.type\s*\]|processors\[", src)]
    if reg:
        print(f"\n    ⚠ registry/index dispatch (silent-miss or runtime-throw on a new member): {reg[:4]}")
else:
    print("    (no multi-member dispatch sites found — category-completeness not applicable here)")
