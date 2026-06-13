#!/usr/bin/env python3
"""foreshock plumbing sanity checks — deterministic facts only, NOT output assertions.

These verify the *pipes* (does the hook gate the right extensions, does each parser see imports /
exports / variants, does resolution link the right files, is the cache deterministic) — facts with a
single right answer regardless of codebase. They do NOT pin packet wording or judge usefulness; that
is the job of the eval/ratings loop, because every codebase foreshock assists is different.

  python3 selftest.py        # exits non-zero if any check fails

Catches the classes of bug that have actually bitten: a parser missing variant members (Java Javadoc
braces, Go iota), and the hook silently not firing on a language (the EXTS gate).
"""
import os, re, sys, json, tempfile, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE_DIR)
import lang_ts, lang_python, lang_java, lang_go, lang_ruby, lang_csharp, lang_sql

PLUGINS = {"ts": lang_ts, "python": lang_python, "java": lang_java, "go": lang_go,
           "ruby": lang_ruby, "csharp": lang_csharp, "sql": lang_sql}

_fail = []
def check(name, ok, detail=""):
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if (not ok and detail) else ""))
    if not ok: _fail.append(name)

def eq(name, got, want):
    check(name, got == want, f"got {got!r}, want {want!r}")

def has(name, container, member):
    check(name, member in container, f"{member!r} not in {container!r}")


# ── A. plugin contract is complete ─────────────────────────────────────────────
print("A. plugin contract")
CONTRACT = ["EXTENSIONS", "is_test", "specs", "build_index", "resolve", "exported_symbols",
            "imported_names", "unions", "decl_lines", "export_positions"]
for name, p in PLUGINS.items():
    missing = [fn for fn in CONTRACT if not hasattr(p, fn)]
    check(f"{name}: contract complete", not missing, f"missing {missing}")


# ── B. the hook fires on every language the plugins handle (the EXTS bug) ────────
print("B. hook EXTS covers all plugins")
hook_src = open(os.path.join(ENGINE_DIR, "impact_hook.py")).read()
m = re.search(r"EXTS\s*=\s*\(([^)]*)\)", hook_src)
hook_exts = set(re.findall(r'"(\.[a-z]+)"', m.group(1))) if m else set()
plugin_exts = set(e for p in PLUGINS.values() for e in p.EXTENSIONS)
missing = plugin_exts - hook_exts
check("hook EXTS ⊇ all plugin extensions", not missing, f"hook never fires on {sorted(missing)}")


# ── C. parsers see imports / exports / variants on a known input ────────────────
print("C. parser facts (catches Java-Javadoc / Go-iota class of bug)")
has("ts: specs sees relative import", lang_ts.specs('import {a} from "./m";'), "./m")
has("ts: export detected", lang_ts.exported_symbols("export function foo(){}"), "foo")
eq("ts: string-union members", lang_ts.unions("export type K = 'a' | 'b';").get("K"), {"a", "b"})

has("python: from-import spec", lang_python.specs("from pkg import x"), "pkg")
has("python: class export", lang_python.exported_symbols("class Bar:\n  pass"), "Bar")
eq("python: Enum members", lang_python.unions("class C(Enum):\n  X=1\n  Y=2\n").get("C"), {"X", "Y"})

# Java enum WITH Javadoc braces — the exact regression that bit us
java_enum = "public enum E {\n  /** a {@code x} */\n  A,\n  /** {@link Y} */\n  B,\n  C\n}"
eq("java: enum members despite Javadoc braces", lang_java.unions(java_enum).get("E"), {"A", "B", "C"})
has("java: import spec (FQCN)", lang_java.specs("import a.b.C;"), "a.b.C")

# Go iota — members after the first omit `=`
go_enum = "type Color int\nconst (\n\tRed Color = iota\n\tGreen\n\tBlue\n)"
eq("go: iota const members", lang_go.unions(go_enum).get("Color"), {"Red", "Green", "Blue"})
has("go: capitalised export", lang_go.exported_symbols("func Add(a int) int { return a }"), "Add")

has("ruby: require_relative spec", lang_ruby.specs('require_relative "lib"'), "lib")
has("ruby: def/class surface", lang_ruby.exported_symbols("class Calc\n  def add; end\nend"), "Calc")

eq("csharp: enum members", lang_csharp.unions("public enum S { Active, Inactive }").get("S"),
   {"Active", "Inactive"})
has("csharp: using spec", lang_csharp.specs("using App.Util;"), "App.Util")

sql_schema = ("CREATE TABLE orders (\n  id serial PRIMARY KEY,\n"
              "  status text CHECK (status IN ('open','paid','void')),\n  amount numeric\n);")
eq("sql: table + qualified columns", lang_sql.exported_symbols(sql_schema),
   {"orders", "orders.id", "orders.status", "orders.amount"})
eq("sql: CHECK-IN closed set", lang_sql.unions(sql_schema).get("status"), {"open", "paid", "void"})
has("sql: FK reference spec", lang_sql.specs("FOREIGN KEY (oid) REFERENCES orders (id)"), "orders")
has("sql: FROM reference spec", lang_sql.specs("SELECT id FROM orders WHERE x=1"), "orders")


# ── D. resolution links the right file(s) — incl. package-as-directory (list) ────
print("D. import resolution links the right files")
def _resolve_case(lang, files, importer, spec, want_contains, extra=None):
    p = PLUGINS[lang]
    root = tempfile.mkdtemp(prefix="fs_self_")
    try:
        paths = {}
        for rel, body in files.items():
            ap = os.path.join(root, rel)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            open(ap, "w").write(body)
            paths[rel] = ap
        for rel, body in (extra or {}).items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
            open(os.path.join(root, rel), "w").write(body)
        flist = list(paths.values())
        text = {f: open(f).read() for f in flist}
        ctx = p.build_index(root, flist, text)
        r = p.resolve(paths[importer], spec, ctx)
        targets = r if isinstance(r, (list, tuple, set)) else ([r] if r else [])
        check(f"{lang}: resolve {spec!r} → {want_contains}", paths[want_contains] in targets,
              f"got {[os.path.relpath(t, root) for t in targets]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

_resolve_case("ts", {"math.ts": "export const a=1;", "calc.ts": 'import {a} from "./math";'},
              "calc.ts", "./math", "math.ts")
_resolve_case("python", {"a.py": "def foo():\n  pass", "b.py": "from a import foo"},
              "b.py", "a", "a.py")
_resolve_case("java",
              {"u/A.java": "package p.u;\npublic class A {}",
               "a/B.java": "package p.a;\nimport p.u.A;\npublic class B {}"},
              "a/B.java", "p.u.A", "u/A.java")
_resolve_case("go",  # package = directory → list resolve
              {"mathx/m.go": "package mathx\nfunc Add(a int) int { return a }",
               "main.go": 'package main\nimport "ex/mathx"\nfunc main() { mathx.Add(1) }'},
              "main.go", "ex/mathx", "mathx/m.go", extra={"go.mod": "module ex\n"})
_resolve_case("ruby", {"lib.rb": "class C\nend", "app.rb": 'require_relative "lib"'},
              "app.rb", "lib", "lib.rb")
_resolve_case("csharp",  # namespace spans files → list resolve
              {"a.cs": "namespace N;\npublic class A {}", "b.cs": "using N;\npublic class B {}"},
              "b.cs", "N", "a.cs")
_resolve_case("sql",  # a query/FK file references a table defined elsewhere
              {"schema.sql": "CREATE TABLE orders (id int);",
               "report.sql": "SELECT id FROM orders;"},
              "report.sql", "orders", "schema.sql")


# ── E. the cache is deterministic (warm output == cold output) ──────────────────
print("E. cache determinism (warm == cold)")
def _engine(root, f, env_extra):
    return subprocess.run([sys.executable, os.path.join(ENGINE_DIR, "impact_engine.py"), "--file", f],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,  # else engine blocks on stdin
                          env={**os.environ, "FS_ROOT": root, **env_extra}).stdout

croot = tempfile.mkdtemp(prefix="fs_self_cache_")
try:
    os.makedirs(os.path.join(croot, "src"))
    open(os.path.join(croot, "tsconfig.json"), "w").write("{}")
    open(os.path.join(croot, "src/core.ts"), "w").write("export function core(){ return 1; }")
    for i in range(20):
        open(os.path.join(croot, f"src/m{i}.ts"), "w").write('import {core} from "./core";\nexport const v=core();')
    cache_home = tempfile.mkdtemp(prefix="fs_self_cachehome_")
    env = {"HOME": cache_home}                       # isolate the cache dir for cold/warm
    target = os.path.join(croot, "src/core.ts")
    cold = _engine(croot, target, env)               # builds the cache
    warm = _engine(croot, target, env)               # serves from cache
    check("warm output identical to cold", cold == warm and cold.strip() != "",
          "cached run diverged from cold run")
    shutil.rmtree(cache_home, ignore_errors=True)
finally:
    shutil.rmtree(croot, ignore_errors=True)


# ── summary ─────────────────────────────────────────────────────────────────────
print()
if _fail:
    print(f"FAIL — {len(_fail)} check(s): " + ", ".join(_fail))
    sys.exit(1)
print("PASS — all plumbing checks green")
