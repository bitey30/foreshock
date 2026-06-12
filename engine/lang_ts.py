"""foreshock language plugin: TypeScript / JavaScript.

Owns TS/JS import-graph + symbol extraction. Edges from import, `export … from` (barrels),
dynamic `import()`, `require()`, and side-effect imports. Resolves relative + ts/jsconfig
path aliases. Variant types = string-literal unions.
"""
import os, re, glob, json

EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

def is_test(relpath):
    return bool(re.search(r"\.(test|spec)\.[tj]sx?$|/tests?/|/__tests__/", relpath))

IMPORT_RE   = re.compile(r"""import\s+([\s\S]*?)\s+from\s*['"]([^'"]+)['"]""")
REEXPORT_RE = re.compile(r"""export\s+(\*(?:\s+as\s+\w+)?|type\s*\{[^}]*\}|\{[^}]*\})\s+from\s*['"]([^'"]+)['"]""")
DYN_RE      = re.compile(r"""\b(?:import|require)\s*\(\s*['"]([^'"]+)['"]""")
BARE_RE     = re.compile(r"""(?:^|[\n;])\s*import\s+['"]([^'"]+)['"]""")

def specs(src):
    out = set()
    for _, s in IMPORT_RE.findall(src):   out.add(s)
    for _, s in REEXPORT_RE.findall(src): out.add(s)
    for s in DYN_RE.findall(src):         out.add(s)
    for s in BARE_RE.findall(src):        out.add(s)
    return out

# ---- index: ts/jsconfig path aliases + the fileset (for extensionless resolution) ----
def build_index(root, files, text):
    aliases = {}
    cfgs = glob.glob(os.path.join(root, "**", "tsconfig*.json"), recursive=True) \
         + glob.glob(os.path.join(root, "**", "jsconfig*.json"), recursive=True)
    for tsc in cfgs:
        if "/node_modules/" in tsc: continue
        raw = open(tsc, errors="ignore").read()
        cfg = None
        for attempt in (raw, re.sub(r",(\s*[}\]])", r"\1", re.sub(r"^\s*//[^\n]*$", "", raw, flags=re.M))):
            try: cfg = json.loads(attempt); break
            except Exception: cfg = None
        if cfg is None: continue
        co = cfg.get("compilerOptions", {}) or {}
        base = os.path.normpath(os.path.join(os.path.dirname(tsc), co.get("baseUrl", "."))) \
            if co.get("baseUrl") else os.path.dirname(tsc)
        for k, v in (co.get("paths") or {}).items():
            pre = k[:-1] if k.endswith("*") else k
            for t in (v if isinstance(v, list) else [v]):
                t = t[:-1] if t.endswith("*") else t
                aliases.setdefault(pre, []).append(os.path.normpath(os.path.join(base, t)))
    return {"aliases": aliases, "fileset": set(files)}

def _try(base, fileset):
    base = re.sub(r"\.(js|jsx|mjs|cjs|ts|tsx)$", "", base)
    for c in (base+".ts", base+".tsx", base+".js", base+".jsx",
              os.path.join(base, "index.ts"), os.path.join(base, "index.tsx"),
              os.path.join(base, "index.js"), os.path.join(base, "index.jsx"), base):
        if c in fileset: return c
    return None

def resolve(importer, spec, ctx):
    fileset = ctx["fileset"]
    if spec.startswith("."):
        return _try(os.path.normpath(os.path.join(os.path.dirname(importer), spec)), fileset)
    for pre, tgts in ctx["aliases"].items():
        if spec == pre.rstrip("/") or spec.startswith(pre):
            for tg in tgts:
                r = _try(os.path.normpath(os.path.join(tg, spec[len(pre):])), fileset)
                if r: return r
    return None

# ---- symbols ----
EXPORT_DECL = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let|var|class|type|interface|enum)\s+(\w+)", re.M)

def exported_symbols(src):
    names = set(EXPORT_DECL.findall(src))
    for blk in re.findall(r"export\s*\{([^}]*)\}", src):
        for piece in blk.split(","):
            piece = piece.strip()
            if not piece: continue
            m = re.match(r"\w+\s+as\s+(\w+)", piece) or re.match(r"(\w+)", piece)
            if m: names.add(m.group(1))
    if re.search(r"export\s+default", src): names.add("default")
    return names

def _names_from_clause(clause, names):
    clause = clause.strip()
    mb = re.search(r"\{([^}]*)\}", clause)
    if mb:
        for piece in mb.group(1).split(","):
            piece = piece.strip()
            if not piece: continue
            m = re.match(r"(?:type\s+)?(\w+)", piece)
            if m: names.add(m.group(1))
    if re.search(r"\*\s+as\s+\w+", clause) or clause.startswith("*"): names.add("*")
    head = clause.split("{")[0].split(",")[0].strip()
    if head and head != "*" and not head.startswith("{") and not head.startswith("type"):
        names.add("default")

def imported_names(importer_src, target, importer, ctx):
    names = set()
    for clause, spec in IMPORT_RE.findall(importer_src):
        if resolve(importer, spec, ctx) == target: _names_from_clause(clause, names)
    for clause, spec in REEXPORT_RE.findall(importer_src):
        if resolve(importer, spec, ctx) == target: _names_from_clause(clause, names)
    for spec in DYN_RE.findall(importer_src):
        if resolve(importer, spec, ctx) == target: names.add("*")
    return names

# string-literal unions: `export type X = 'a' | 'b'` (semicolon optional)
TYPE_RE = re.compile(r"export\s+type\s+(\w+)\s*=\s*([\s\S]*?)(?=;|\n\s*\n|\nexport\b|\Z)")
def unions(src):
    out = {}
    for m in TYPE_RE.finditer(src):
        name, body = m.group(1), m.group(2)
        lits = re.findall(r"['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", body)
        if lits and re.sub(r"['\"][^'\"]*['\"]|\s|\|", "", body) == "":
            out[name] = set(lits)
    return out

def _signature(src, start):
    """Full declaration signature from `export …`: balanced param list + return type,
    stopping at the body (`=>` or `{`). Captures multi-line arrow-const signatures so a
    param/return change is seen as an API change, not 'content-only'."""
    seg = src[start:start + 1000]
    paren = seg.find("(")
    if paren == -1:                       # no params: const value / type / interface / enum
        m = re.search(r"[;\n]", seg)
        return re.sub(r"\s+", " ", (seg[:m.start()] if m else seg[:120])).strip()
    depth, i = 0, paren                    # balance parens to close the param list
    while i < len(seg):
        if seg[i] == "(": depth += 1
        elif seg[i] == ")":
            depth -= 1
            if depth == 0: break
        i += 1
    tail = seg[i + 1:]                      # include return type up to the body start
    mt = re.search(r"=>|\{|\n", tail)
    sig = seg[:i + 1] + (tail[:mt.start()] if mt else "")
    return re.sub(r"\s+", " ", sig).strip()

def decl_lines(src):
    out = {}
    for m in EXPORT_DECL.finditer(src):
        out.setdefault(m.group(1), _signature(src, m.start()))
    return out

def export_positions(src):
    return [(m.start(), m.group(1)) for m in EXPORT_DECL.finditer(src)]
