"""foreshock language plugin: Java.

Import graph via package+classname -> fully-qualified class resolution of `import a.b.C;`
(and `import static a.b.C.member;`, `import a.b.*;`). Exports = public types + public
methods. Variant types = `enum` declarations. Ported from analyze_repo_java.py.
"""
import os, re

EXTENSIONS = (".java",)

def is_test(relpath):
    return bool(re.search(r"/src/test/|Test\.java$|Tests\.java$", relpath))

IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.M)

def specs(src):
    return set(IMPORT_RE.findall(src))

def build_index(root, files, text):
    fqcn2file = {}
    for f in files:
        src = text[f]
        m = re.search(r"^\s*package\s+([\w.]+)\s*;", src, re.M)
        pkg = m.group(1) if m else ""
        cls = os.path.basename(f)[:-5]
        fqcn2file[f"{pkg}.{cls}" if pkg else cls] = f
    return {"fqcn2file": fqcn2file, "top_pkgs": {k.split(".")[0] for k in fqcn2file}}

def resolve(importer, spec, ctx):
    if spec.split(".")[0] not in ctx["top_pkgs"]:
        return None
    f2 = ctx["fqcn2file"]
    if spec.endswith(".*"):
        prefix = spec[:-2] + "."
        for fq, fl in f2.items():
            if fq.startswith(prefix): return fl
        return None
    return f2.get(spec) or f2.get(spec.rsplit(".", 1)[0])   # 2nd: static member import

TYPE_RE = re.compile(r"(?:public\s+)?(?:final\s+|abstract\s+|sealed\s+|static\s+)*(?:class|interface|enum|record)\s+(\w+)")
METH_RE = re.compile(r"\bpublic\s+(?:static\s+|final\s+|synchronized\s+|abstract\s+)*[\w<>\[\],.\s]+?\s+(\w+)\s*\(")

def exported_symbols(src):
    return set(TYPE_RE.findall(src)) | set(METH_RE.findall(src))

def imported_names(importer_src, target, importer, ctx):
    names = set()
    for spec in IMPORT_RE.findall(importer_src):
        if resolve(importer, spec, ctx) != target: continue
        if spec.endswith(".*"): names.add("*")
        else: names.add(spec.rsplit(".", 1)[-1])
    return names

def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # block/Javadoc comments ({@code}, {@link} braces)
    return re.sub(r"//[^\n]*", "", src)               # line comments

def _balanced(src, open_pos):
    """Index of the `}` matching the `{` at open_pos."""
    depth, i = 0, open_pos
    while i < len(src):
        if src[i] == "{": depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0: return i
        i += 1
    return len(src)

def _split_top(seg, seps=","):
    """Split on separators that are at brace/paren/bracket depth 0."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(seg):
        if c in "([{": depth += 1
        elif c in ")]}": depth -= 1
        elif c in seps and depth == 0:
            parts.append(seg[start:i]); start = i + 1
    parts.append(seg[start:])
    return parts

def unions(src):
    src = _strip_comments(src)        # Javadoc `}` (e.g. {@code null}) used to truncate the body
    out = {}
    for m in re.finditer(r"\benum\s+(\w+)\b", src):
        b = src.find("{", m.end())
        if b == -1: continue
        body = src[b + 1:_balanced(src, b)]
        consts = _split_top(body, ";")[0]            # constant section = up to first top-level ';'
        mem = set()
        for piece in _split_top(consts):             # top-level commas (skip constant args/bodies)
            piece = re.sub(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*", "", piece)   # drop leading annotations
            mm = re.match(r"\s*([A-Z_][A-Za-z0-9_]*)", piece)
            if mm: mem.add(mm.group(1))
        if mem: out[m.group(1)] = mem
    return out

def _signature(src, start):
    """Full type/method signature: balanced param list (or type header up to `{`).
    Captures multi-line method signatures so a param/return change is an API change."""
    seg = src[start:start + 800]
    paren = seg.find("(")
    brace = seg.find("{")
    if paren == -1 or (brace != -1 and brace < paren):   # type header: no param list before body
        return re.sub(r"\s+", " ", (seg[:brace] if brace != -1 else seg[:160])).strip()
    depth, i = 0, paren
    while i < len(seg):
        if seg[i] == "(": depth += 1
        elif seg[i] == ")":
            depth -= 1
            if depth == 0: break
        i += 1
    tail = seg[i + 1:]                        # return/throws up to the body `{` or abstract `;`
    mt = re.search(r"\{|;|\n", tail)
    sig = seg[:i + 1] + (tail[:mt.start()] if mt else "")
    return re.sub(r"\s+", " ", sig).strip()

def decl_lines(src):
    out = {}
    for start, name in export_positions(src):
        out.setdefault(name, _signature(src, start))
    return out

def export_positions(src):
    pos = [(m.start(), m.group(1)) for m in re.finditer(r"(?:class|interface|enum|record)\s+(\w+)", src)]
    pos += [(m.start(), m.group(1)) for m in METH_RE.finditer(src)]
    return sorted(pos)
