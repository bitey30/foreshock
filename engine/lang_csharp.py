"""foreshock language plugin: C#.

Import graph by namespace: `using Some.Namespace;` depends on EVERY file that declares that
namespace (a namespace spans many files — resolve returns a list, like Go). Exports = public
types + public methods. Variant types = `enum`.
"""
import os, re

EXTENSIONS = (".cs",)

def is_test(relpath):
    return bool(re.search(r"Tests?\.cs$|/Tests?/|\.Tests/", relpath))

_USING = re.compile(r'^\s*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;', re.M)   # skip `using X = Y;` aliases
_NS    = re.compile(r'^\s*namespace\s+([A-Za-z_][\w.]*)', re.M)

def specs(src):
    return {u for u in _USING.findall(src) if "=" not in u}

def build_index(root, files, text):
    ns2files = {}
    for f in files:
        for ns in _NS.findall(text[f]):
            ns2files.setdefault(ns, set()).add(f)
    return {"ns2files": ns2files, "fileset": set(files)}

def resolve(importer, spec, ctx):
    files = ctx["ns2files"].get(spec)
    return sorted(files) if files else None         # namespace = all files declaring it (a list)

_TYPE = re.compile(r'\b(?:public|internal)\s+(?:partial\s+|static\s+|sealed\s+|abstract\s+|readonly\s+)*'
                   r'(?:class|struct|interface|enum|record)\s+(\w+)')
_METH = re.compile(r'\bpublic\s+(?:static\s+|virtual\s+|override\s+|async\s+|sealed\s+)*'
                   r'[\w<>\[\],.\s]+?\s+(\w+)\s*\(')

def exported_symbols(src):
    return set(_TYPE.findall(src)) | set(_METH.findall(src))

def imported_names(importer_src, target, importer, ctx):
    for spec in specs(importer_src):
        files = ctx["ns2files"].get(spec) or set()
        if target in files: return {"*"}            # using imports a namespace, not names
    return set()

def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)

def unions(src):
    src = _strip_comments(src)                       # XML-doc `///` braces would corrupt the body
    out = {}
    for m in re.finditer(r'\benum\s+(\w+)\s*(?::\s*\w+\s*)?\{([^}]*)\}', src):
        mem = set()
        for piece in m.group(2).split(","):
            mm = re.match(r'\s*([A-Za-z_]\w*)', piece)
            if mm: mem.add(mm.group(1))
        if mem: out[m.group(1)] = mem
    return out

def _signature(src, start):
    seg = src[start:start + 600]
    paren, brace = seg.find("("), seg.find("{")
    if paren == -1 or (brace != -1 and brace < paren):
        cut = min(x for x in (brace, seg.find("\n"), 160) if x != -1)
        return re.sub(r"\s+", " ", seg[:cut]).strip()
    depth, i = 0, paren
    while i < len(seg):
        if seg[i] == "(": depth += 1
        elif seg[i] == ")":
            depth -= 1
            if depth == 0: break
        i += 1
    tail = seg[i + 1:]
    mt = re.search(r"\{|;|\n", tail)
    return re.sub(r"\s+", " ", seg[:i + 1] + (tail[:mt.start()] if mt else "")).strip()

def export_positions(src):
    return sorted([(m.start(), m.group(1)) for m in _TYPE.finditer(src)] +
                  [(m.start(), m.group(1)) for m in _METH.finditer(src)])

def decl_lines(src):
    out = {}
    for start, name in export_positions(src):
        out.setdefault(name, _signature(src, start))
    return out
