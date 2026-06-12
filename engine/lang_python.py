"""foreshock language plugin: Python.

Import graph from absolute + relative imports (module-path resolution). Exports = public
top-level defs/classes/constants. Variant types = Enum classes + `Literal[...]` aliases —
Python has no compiler exhaustiveness, so "forgot a case" is exactly where context helps.
Ported from experiments/bugcatch-deviation/analyze_repo_py.py.
"""
import os, re

EXTENSIONS = (".py",)

def is_test(relpath):
    return bool(re.search(r"/tests?/|/(test_|conftest)|_test\.py$|/__pycache__/", relpath))

FROM_RE = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$", re.M)
IMP_RE  = re.compile(r"^\s*import\s+([\w.]+)", re.M)

def specs(src):
    out = set()
    for dots, mod, _ in FROM_RE.findall(src):
        out.add(dots + mod)          # ".x" / "..a.b" / "." / "a.b"
    for mod in IMP_RE.findall(src):
        out.add(mod)
    return out

def _mod_of(f, root):
    parts = os.path.relpath(f, root)[:-3].split(os.sep)   # strip .py
    if parts[-1] == "__init__": parts = parts[:-1]
    return ".".join(parts)

def build_index(root, files, text):
    mod2file = {_mod_of(f, root): f for f in files}
    base2files = {}
    for m, f in mod2file.items():
        if m: base2files.setdefault(m.split(".")[-1], set()).add(f)
    return {"root": root, "mod2file": mod2file, "fileset": set(files),
            "top_pkgs": {m.split(".")[0] for m in mod2file if m}, "base2files": base2files}

def _resolve_abs(dotted, ctx):
    m2f = ctx["mod2file"]
    if dotted in m2f: return m2f[dotted]
    return m2f.get(dotted.rsplit(".", 1)[0])

def _resolve_rel(importer, level, mod, ctx):
    pkg = _mod_of(importer, ctx["root"]).split(".")
    base = pkg[:-1] if not importer.endswith("__init__.py") else pkg[:]
    up = level - 1
    base = base[:len(base) - up] if up <= len(base) else []
    target = ".".join([p for p in base + (mod.split(".") if mod else []) if p])
    return _resolve_abs(target, ctx) if target else None

def resolve(importer, spec, ctx):
    if spec.startswith("."):
        m = re.match(r"(\.+)(.*)", spec)
        return _resolve_rel(importer, len(m.group(1)), m.group(2), ctx)
    if spec.split(".")[0] in ctx["top_pkgs"]:
        r = _resolve_abs(spec, ctx)
        if r: return r
    # sibling / sys.path-rooted fallback: `import sibling` in script-style repos, or a
    # src-layout root not equal to the repo root. Resolve by path next to the importer,
    # then by a UNIQUE basename match (ambiguous names stay unresolved — no false edge).
    sib = os.path.join(os.path.dirname(importer), spec.replace(".", os.sep) + ".py")
    if sib in ctx["fileset"]: return sib
    cand = ctx["base2files"].get(spec.split(".")[-1])
    if cand and len(cand) == 1: return next(iter(cand))
    return None

DEF_RE = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)|^([A-Z][A-Z0-9_]*)\s*=", re.M)
def exported_symbols(src):
    out = set()
    for a, b, c in DEF_RE.findall(src):
        n = a or b or c
        if n and not n.startswith("_"): out.add(n)
    return out

def imported_names(importer_src, target, importer, ctx):
    names = set()
    for dots, mod, what in FROM_RE.findall(importer_src):
        if resolve(importer, dots + mod, ctx) != target: continue
        what = what.split("#")[0].strip().strip("()")
        if "*" in what: names.add("*"); continue
        for piece in what.split(","):
            nm = piece.strip().split(" as ")[0].strip()
            if nm: names.add(nm)
    for mod in IMP_RE.findall(importer_src):
        if resolve(importer, mod, ctx) == target: names.add("*")
    return names

# Enum classes + Literal aliases — the closed variant sets a switch should cover
def unions(src):
    out = {}
    for m in re.finditer(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:", src, re.M):
        if "Enum" in m.group(2):
            body = src[m.end():]
            body = body[:re.search(r"\n\S", body).start()] if re.search(r"\n\S", body) else body
            mem = set(re.findall(r"^\s+([A-Za-z_]\w*)\s*=", body, re.M))
            if mem: out[m.group(1)] = mem
    for m in re.finditer(r"^(\w+)\s*(?::\s*\w+\s*)?=\s*Literal\[([^\]]*)\]", src, re.M):
        lits = set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(2)))
        if lits: out[m.group(1)] = lits
    return out

def _signature(src, start):
    """Full def/class signature: balanced param list + return annotation, up to the body `:`.
    Captures multi-line signatures so a param/return change is an API change, not 'content-only'."""
    seg = src[start:start + 800]
    paren = seg.find("(")
    colon = seg.find(":")
    if paren == -1 or (colon != -1 and colon < paren):   # plain class / constant: no param list
        cut = colon if colon != -1 else seg.find("\n")    # (find("(") would wander into method bodies)
        return re.sub(r"\s+", " ", (seg[:cut] if cut != -1 else seg[:120])).strip()
    depth, i = 0, paren
    while i < len(seg):
        if seg[i] == "(": depth += 1
        elif seg[i] == ")":
            depth -= 1
            if depth == 0: break
        i += 1
    tail = seg[i + 1:]                        # `-> Ret:` (param `:` annotations stay inside the parens)
    mt = re.search(r":|\n", tail)
    sig = seg[:i + 1] + (tail[:mt.start()] if mt else "")
    return re.sub(r"\s+", " ", sig).strip()

def decl_lines(src):
    out = {}
    for start, name in export_positions(src):
        out.setdefault(name, _signature(src, start))
    return out

def export_positions(src):
    return [(m.start(), (a or b or c)) for m in DEF_RE.finditer(src)
            for a, b, c in [m.groups()] if (a or b or c) and not (a or b or c).startswith("_")]
