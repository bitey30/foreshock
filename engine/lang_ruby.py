"""foreshock language plugin: Ruby.

Import graph from `require_relative` (file-relative, cleanly resolvable) and `require` (resolved
by unique basename as a fallback). Ruby has no export keyword and everything is open, so the
"public surface" is modelled as top-level class / module / method (def) names — a change to one
of those is what ripples. Ruby has no enums, so no variant family (returns none).
"""
import os, re

EXTENSIONS = (".rb",)

def is_test(relpath):
    return bool(re.search(r"_(spec|test)\.rb$|/spec/|/test/", relpath))

_REQ = re.compile(r'^\s*require(_relative)?\s+["\']([^"\']+)["\']', re.M)

def specs(src):
    return {p for _rel, p in _REQ.findall(src)}

def build_index(root, files, text):
    base = {}
    for f in files:
        base.setdefault(os.path.basename(f)[:-3], set()).add(f)
    return {"root": root, "fileset": set(files), "base2files": base}

def resolve(importer, spec, ctx):
    fs = ctx["fileset"]
    for cand in (os.path.normpath(os.path.join(os.path.dirname(importer), spec)) + ".rb",
                 os.path.normpath(os.path.join(ctx["root"], spec)) + ".rb"):
        if cand in fs: return cand
    hit = ctx["base2files"].get(os.path.basename(spec))      # `require "foo"` → unique foo.rb
    return next(iter(hit)) if hit and len(hit) == 1 else None

_DEF = re.compile(r'^\s*(?:class|module)\s+([A-Z]\w*)|^\s*def\s+(?:self\.)?(\w+)', re.M)

def exported_symbols(src):
    return {a or b for a, b in _DEF.findall(src) if (a or b)}

def imported_names(importer_src, target, importer, ctx):
    for _rel, p in _REQ.findall(importer_src):
        if resolve(importer, p, ctx) == target: return {"*"}    # require pulls a file, not names
    return set()

def unions(src):
    return {}                                                    # Ruby has no enums / closed sets

def _signature(src, start):
    seg = src[start:start + 400]
    nl = seg.find("\n")
    return re.sub(r"\s+", " ", seg[:nl] if nl != -1 else seg[:120]).strip()

def export_positions(src):
    return sorted((m.start(), (m.group(1) or m.group(2))) for m in _DEF.finditer(src)
                  if (m.group(1) or m.group(2)))

def decl_lines(src):
    out = {}
    for start, name in export_positions(src):
        out.setdefault(name, _signature(src, start))
    return out
