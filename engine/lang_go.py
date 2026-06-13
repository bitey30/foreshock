"""foreshock language plugin: Go.

Import graph by Go's package model: a package is a DIRECTORY, and `import "mod/sub"` depends on
EVERY file in that package's directory (so resolve() returns a list — the engine handles that).
Exports = top-level identifiers with a Capital initial. Variant types = typed `const`/`iota`
groups (Go's enum analog — a `switch` over them has no compiler exhaustiveness, which is exactly
where "you forgot a case" context helps).
"""
import os, re

EXTENSIONS = (".go",)

def is_test(relpath):
    return relpath.endswith("_test.go")

# `import "x"` and `import ( "a"\n  al "b"\n  _ "c" )`
_SINGLE = re.compile(r'^\s*import\s+(?:[\w.]+\s+|_\s+|\.\s+)?"([^"]+)"', re.M)
_BLOCK  = re.compile(r'import\s*\(([\s\S]*?)\)', re.M)
_INBLK  = re.compile(r'(?:([\w.]+)\s+|(_)\s+|(\.)\s+)?"([^"]+)"')

def specs(src):
    out = set()
    for s in _SINGLE.findall(src): out.add(s)
    for blk in _BLOCK.findall(src):
        for _a, _u, _d, path in _INBLK.findall(blk): out.add(path)
    return out

def _alias_for(src, path):
    """The identifier a file uses to reference an imported package (explicit alias or last segment)."""
    for blk in _BLOCK.findall(src):
        for a, u, d, p in _INBLK.findall(blk):
            if p == path: return a or (None if (u or d) else path.rsplit("/", 1)[-1])
    m = re.search(r'import\s+([\w.]+)\s+"' + re.escape(path) + r'"', src)
    if m: return m.group(1)
    return path.rsplit("/", 1)[-1]

def build_index(root, files, text):
    module = ""
    gomod = os.path.join(root, "go.mod")
    if os.path.exists(gomod):
        m = re.search(r'^module\s+(\S+)', open(gomod, errors="ignore").read(), re.M)
        module = m.group(1) if m else ""
    pkg2files = {}
    for f in files:
        reldir = os.path.dirname(os.path.relpath(f, root)).replace(os.sep, "/")
        importpath = module if reldir in ("", ".") else f"{module}/{reldir}" if module else reldir
        pkg2files.setdefault(importpath, set()).add(f)
    return {"module": module, "pkg2files": pkg2files, "fileset": set(files)}

def resolve(importer, spec, ctx):
    files = ctx["pkg2files"].get(spec)
    return sorted(files) if files else None          # a package = all its files (engine accepts a list)

# ---- exported (Capitalised) top-level symbols ----
_FUNC   = re.compile(r'^func\s+(?:\([^)]*\)\s+)?([A-Z]\w*)\s*\(', re.M)   # funcs + methods
_TYPE   = re.compile(r'^type\s+([A-Z]\w*)', re.M)
_VARCON = re.compile(r'^(?:var|const)\s+([A-Z]\w*)', re.M)
_GROUP  = re.compile(r'^(?:var|const)\s*\(([\s\S]*?)\)', re.M)            # var(...)/const(...) blocks

def exported_symbols(src):
    out = set(_FUNC.findall(src)) | set(_TYPE.findall(src)) | set(_VARCON.findall(src))
    for blk in _GROUP.findall(src):
        for m in re.finditer(r'^\s*([A-Z]\w*)', blk, re.M): out.add(m.group(1))
    return out

def imported_names(importer_src, target, importer, ctx):
    names = set()
    for spec in specs(importer_src):
        files = ctx["pkg2files"].get(spec) or set()
        if target not in files: continue
        alias = _alias_for(importer_src, spec)
        used = set(re.findall(r'\b' + re.escape(alias) + r'\.([A-Z]\w*)', importer_src))
        names |= (used or {"*"})
    return names

# ---- variant analog: typed const / iota groups ----
def unions(src):
    out = {}
    for blk in re.finditer(r'const\s*\(([\s\S]*?)\)', src):
        cur, members = None, {}
        for line in blk.group(1).splitlines():
            s = line.strip()
            if not s or s.startswith("//") or s.startswith("/*"): continue
            m = re.match(r'([A-Za-z_]\w*)(?:\s+([A-Z]\w*))?', s)  # iota members after the first omit `=`
            if not m: continue
            if m.group(2): cur = m.group(2)            # type appears on the first member, carries down
            if cur: members.setdefault(cur, set()).add(m.group(1))
        for t, ms in members.items():
            if ms: out.setdefault(t, set()).update(ms)
    return out

def _signature(src, start):
    seg = src[start:start + 800]
    paren = seg.find("(")
    brace = seg.find("{")
    nl = seg.find("\n")
    if paren == -1 or (brace != -1 and brace < paren) or (nl != -1 and nl < paren):   # type/var/const
        cut = min(x for x in (brace, nl, 120) if x != -1)
        return re.sub(r"\s+", " ", seg[:cut]).strip()
    depth, i = 0, paren
    while i < len(seg):
        if seg[i] == "(": depth += 1
        elif seg[i] == ")":
            depth -= 1
            if depth == 0: break
        i += 1
    tail = seg[i + 1:]                                  # return signature up to the body `{`
    mt = re.search(r"\{|\n", tail)
    return re.sub(r"\s+", " ", seg[:i + 1] + (tail[:mt.start()] if mt else "")).strip()

def export_positions(src):
    pos = []
    for rx in (_FUNC, _TYPE, _VARCON):
        pos += [(m.start(), m.group(1)) for m in rx.finditer(src)]
    return sorted(pos)

def decl_lines(src):
    out = {}
    for start, name in export_positions(src):
        out.setdefault(name, _signature(src, start))
    return out
