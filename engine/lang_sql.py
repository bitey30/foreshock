"""foreshock language plugin: SQL (schema-shaped). Opt-in — the engine only loads it when
FORESHOCK_SQL=1, so it never adds noise unless you ask for it.

SQL coupling is name-based, not import-based, so the model is bent to fit:
  • SYMBOLS  = tables and **qualified** columns (`orders`, `orders.status`)
  • EDGES    = references — `FOREIGN KEY … REFERENCES`, `FROM`, `JOIN`, INSERT/UPDATE/DELETE,
               `ALTER TABLE` — i.e. a file that references table T depends on the file that defines T
  • VARIANTS = `CHECK (col IN ('a','b',…))` closed sets — add a value, here are the consumers

Precise by construction: a column is only counted as referenced when **qualified** (`t.col`,
`REFERENCES t(col)`), never as a bare word — so common names like `id`/`status` don't explode.
"""
import re

EXTENSIONS = (".sql",)

def is_test(relpath):
    return bool(re.search(r"/tests?/|_test\.sql$|/fixtures?/|/seeds?/", relpath))

_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_]\w*)'
_KW = re.compile(r'(?i)^\s*(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|KEY|INDEX|EXCLUDE|LIKE)\b')

def _norm(raw):
    return raw.strip().strip('"`[]').split(".")[-1].lower()          # drop schema/quotes, lowercase

def _balanced(src, open_idx):
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "(": depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0: return src[open_idx + 1:i]
    return src[open_idx + 1:]

def _split_top(seg):
    out, depth, start = [], 0, 0
    for i, c in enumerate(seg):
        if c == "(": depth += 1
        elif c == ")": depth -= 1
        elif c == "," and depth == 0: out.append(seg[start:i]); start = i + 1
    out.append(seg[start:])
    return out

# ---- references (the graph) ----
_REFS = re.compile(r'(?i)\b(?:REFERENCES|FROM|JOIN|INTO|UPDATE|ALTER\s+TABLE(?:\s+IF\s+EXISTS)?)'
                   r'\s+([.\w"`\[\]]+)')
def specs(src):
    return {_norm(m.group(1)) for m in _REFS.finditer(src)}

_CREATE = re.compile(r'(?i)\bCREATE\s+(?:TABLE|MATERIALIZED\s+VIEW|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                     r'([.\w"`\[\]]+)')
def build_index(root, files, text):
    t2f = {}
    for f in files:
        for m in _CREATE.finditer(text[f]):
            t2f.setdefault(_norm(m.group(1)), f)
    return {"table2file": t2f, "fileset": set(files)}

def resolve(importer, spec, ctx):
    return ctx["table2file"].get(spec)

# ---- table/column definitions (the surface) ----
def _table_defs(src):
    """{table: {column: signature}} from CREATE TABLE (…) and ALTER TABLE … ADD COLUMN."""
    defs = {}
    for m in re.finditer(r'(?i)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([.\w"`\[\]]+)\s*\(', src):
        name = _norm(m.group(1))
        cols = defs.setdefault(name, {})
        for piece in _split_top(_balanced(src, m.end() - 1)):
            if _KW.match(piece): continue
            cm = re.match(r'\s*(' + _IDENT + r')', piece)
            if cm: cols[_norm(cm.group(1))] = re.sub(r'\s+', ' ', piece.strip())
    for m in re.finditer(r'(?i)\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([.\w"`\[\]]+)\s+ADD\s+(?:COLUMN\s+)?'
                         r'([.\w"`\[\]]+)([^;,]*)', src):
        col = _norm(m.group(2))
        if re.match(r'(?i)(constraint|primary|foreign|unique|check)$', col): continue
        defs.setdefault(_norm(m.group(1)), {})[col] = re.sub(r'\s+', ' ', (m.group(2) + m.group(3)).strip())
    return defs

def exported_symbols(src):
    out = set()
    for t, cols in _table_defs(src).items():
        out.add(t); out |= {f"{t}.{c}" for c in cols}
    return out

def decl_lines(src):
    out = {}
    for t, cols in _table_defs(src).items():
        out[t] = t
        for c, sig in cols.items(): out[f"{t}.{c}"] = sig
    return out

def export_positions(src):
    """Positions of tables AND columns, so a body change is attributed to the COLUMN being edited
    (`orders.status`), not the whole table — otherwise every table-referencer would falsely arrow."""
    pos = []
    for m in re.finditer(r'(?i)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([.\w"`\[\]]+)\s*\(', src):
        name = _norm(m.group(1))
        pos.append((m.start(), name))
        body_open = m.end() - 1
        body = _balanced(src, body_open)
        off, depth, start = body_open + 1, 0, 0
        pieces = []
        for i, c in enumerate(body):
            if c == "(": depth += 1
            elif c == ")": depth -= 1
            elif c == "," and depth == 0: pieces.append((start, body[start:i])); start = i + 1
        pieces.append((start, body[start:]))
        for soff, piece in pieces:
            if _KW.match(piece): continue
            cm = re.match(r'\s*(' + _IDENT + r')', piece)
            if cm: pos.append((off + soff + cm.start(1), f"{name}.{_norm(cm.group(1))}"))
    for m in re.finditer(r'(?i)\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([.\w"`\[\]]+)\s+ADD\s+(?:COLUMN\s+)?'
                         r'([.\w"`\[\]]+)', src):
        pos.append((m.start(2), f"{_norm(m.group(1))}.{_norm(m.group(2))}"))
    return sorted(pos)

# ---- which symbols of `target` does `importer` reference? (qualified only) ----
def imported_names(importer_src, target, importer, ctx):
    tables = {t for t, f in ctx["table2file"].items() if f == target}
    names = set()
    for t in tables:
        te = re.escape(t)
        if re.search(r'(?i)\b(?:FROM|JOIN|REFERENCES|INTO|UPDATE|TABLE)\s+(?:\w+\.)?["`\[]?' + te + r'["`\]]?\b',
                     importer_src):
            names.add(t)
        for m in re.finditer(r'(?i)\b' + te + r'\.(' + _IDENT + r')', importer_src):       # t.col
            names.add(f"{t}.{_norm(m.group(1))}")
        for m in re.finditer(r'(?i)\bREFERENCES\s+(?:\w+\.)?["`\[]?' + te + r'["`\]]?\s*\(\s*(' + _IDENT + r')',
                             importer_src):                                                 # REFERENCES t(col)
            names.add(t); names.add(f"{t}.{_norm(m.group(1))}")
    return names

# ---- CHECK (col IN (...)) closed sets ----
def unions(src):
    out = {}
    for m in re.finditer(r'(?i)\bCHECK\s*\(\s*([\w".`\[\]]+)\s+IN\s*\(([^)]*)\)', src):
        vals = set(re.findall(r"'([^']*)'", m.group(2)))
        if vals: out.setdefault(_norm(m.group(1)), set()).update(vals)
    return out
