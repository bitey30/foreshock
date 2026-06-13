#!/usr/bin/env python3
"""
foreshock — inline CONTEXT for coding agents (context-aware, not a linter).

Language-agnostic CORE. It owns the import graph, blast-radius, the diff-aware/symbol-level
CONTEXT PACKET, and variant/completeness — and delegates per-language parsing (imports,
resolution, exports, variant types) to a plugin per language: lang_ts / lang_python / lang_java.
Adding a language = drop in one lang_*.py implementing the same contract.

  python3 impact_engine.py                  # repo map: blast-radius hot spots
  python3 impact_engine.py --file path/x    # CONTEXT PACKET (used by the hook)

The hook pipes the PostToolUse payload on stdin so the engine can diff old vs new.
"""
import os, re, sys, glob, json, collections

# ---- plugin registry ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lang_ts, lang_python, lang_java
PLUGINS = [lang_ts, lang_python, lang_java]
EXT2PLUGIN = {ext: p for p in PLUGINS for ext in p.EXTENSIONS}
ALL_EXTS = tuple(EXT2PLUGIN)

ROOT = os.environ.get("FS_ROOT", os.getcwd())
SKIP = re.compile(r"/(node_modules|\.next|dist|build|out|coverage|vendor|target|\.gradle"
                  r"|\.venv|venv|site-packages|__pycache__|\.tox)/|\.d\.ts$")

# ---- discover files, bucket by language ----
allfiles = []
for ext in ALL_EXTS:
    allfiles += glob.glob(os.path.join(ROOT, "**", f"*{ext}"), recursive=True)
allfiles = sorted({f for f in allfiles if not SKIP.search(f)})
if "--file" in sys.argv and len(allfiles) > 12000:
    sys.exit(0)                                   # portability guard (hook has ~15s)
text = {f: open(f, errors="ignore").read() for f in allfiles}
rel = lambda f: os.path.relpath(f, ROOT)
def plugin_of(f):
    return EXT2PLUGIN.get(os.path.splitext(f)[1])

src_files = [f for f in allfiles if not plugin_of(f).is_test(rel(f))]
test_files = [f for f in allfiles if plugin_of(f).is_test(rel(f))]
fileset = set(src_files)

# ---- per-language index (aliases / module maps / fqcn maps) ----
ctx = {}
for p in PLUGINS:
    pfiles = [f for f in src_files if plugin_of(f) is p]
    ctx[p] = p.build_index(ROOT, pfiles, {f: text[f] for f in pfiles}) if pfiles else {}

# ---- import graph ----
imp = collections.defaultdict(set)
for f in src_files:
    p = plugin_of(f)
    for spec in p.specs(text[f]):
        t = p.resolve(f, spec, ctx[p])
        if t and t != f and t in fileset: imp[f].add(t)
dependents = collections.defaultdict(set)
for a, bs in imp.items():
    for b in bs: dependents[b].add(a)
def transitive_dependents(x):
    seen, st = set(), [x]
    while st:
        n = st.pop()
        for d in dependents.get(n, ()):
            if d not in seen: seen.add(d); st.append(d)
    seen.discard(x); return seen
def tier(n):
    if n == 0: return "LOCAL"
    if n < 3:  return "narrow"
    if n < 8:  return "shared"
    return "SHARED-CORE"

def reconstruct_old(new_src, ti, tool):
    try:
        if tool == "Edit":
            return new_src.replace(ti["new_string"], ti["old_string"], 1), ti["new_string"]
        if tool == "MultiEdit":
            old = new_src; sn = []
            for e in reversed(ti.get("edits", [])):
                old = old.replace(e["new_string"], e["old_string"], 1); sn.append(e["new_string"])
            return old, "\n".join(sn)
    except Exception:
        return None, None
    return None, None

def reconstruct_new(old_src, ti, tool):
    """PreToolUse mirror: project the NEW file content from the proposed edit, BEFORE it lands.
    On Pre the file on disk is still the OLD version; tool_input carries the intended change."""
    try:
        if tool == "Edit":
            return old_src.replace(ti["old_string"], ti["new_string"], 1), ti["new_string"]
        if tool == "MultiEdit":
            new = old_src; sn = []
            for e in ti.get("edits", []):
                new = new.replace(e["old_string"], e["new_string"], 1); sn.append(e["new_string"])
            return new, "\n".join(sn)
        if tool == "Write":
            return ti.get("content"), ti.get("content")
    except Exception:
        return None, None
    return None, None

def body_owner(p, new_src, needle):
    if not needle: return None
    pos = new_src.find(needle)
    if pos < 0: return None
    owner = None
    for start, name in sorted(p.export_positions(new_src)):
        if start <= pos: owner = name
        else: break
    return owner

# ===== single-file CONTEXT PACKET =====
if "--file" in sys.argv:
    path = sys.argv[sys.argv.index("--file") + 1]
    absf = os.path.normpath(path if os.path.isabs(path) else os.path.join(ROOT, path))
    if absf not in fileset:
        sys.exit(0)
    p = plugin_of(absf)
    new_src = text[absf]

    disk_src = new_src                       # what is currently on disk
    old_src = event = new_changed = None
    if not sys.stdin.isatty():
        try:
            payload = json.load(sys.stdin)
            event = payload.get("hook_event_name") or payload.get("hookEventName")
            ti, tool = payload.get("tool_input", {}) or {}, payload.get("tool_name", "")
            if event == "PreToolUse":            # edit not applied yet: project the NEW content
                projected, new_changed = reconstruct_new(disk_src, ti, tool)
                if projected is not None:
                    old_src, new_src = disk_src, projected
            else:                                # PostToolUse: disk is NEW, reconstruct the OLD
                old_src, new_changed = reconstruct_old(disk_src, ti, tool)
        except Exception:
            old_src = None

    direct = sorted(dependents.get(absf, set()))
    n = len(transitive_dependents(absf))
    t = tier(n)

    added = removed = decl_touched = set()
    body_sym = None; union_added = {}; union_removed = {}
    if old_src is not None:
        oe, ne = p.exported_symbols(old_src), p.exported_symbols(new_src)
        added, removed = ne - oe, oe - ne
        od, nd = p.decl_lines(old_src), p.decl_lines(new_src)
        decl_touched = {s for s in (oe & ne) if od.get(s) != nd.get(s)}
        ou, nu = p.unions(old_src), p.unions(new_src)
        for u in nu:
            if u in ou:
                if nu[u] - ou[u]: union_added[u] = nu[u] - ou[u]
                if ou[u] - nu[u]: union_removed[u] = ou[u] - nu[u]
        body_sym = body_owner(p, new_src, new_changed)
    changed = set(added) | set(removed) | set(decl_touched) | set(union_added) | set(union_removed)
    if body_sym: changed.add(body_sym)
    api_change = bool(added or removed or decl_touched or union_added or union_removed)
    content_only = (old_src is not None) and not api_change

    if t == "LOCAL" and not api_change:
        sys.exit(0)

    preview = (event == "PreToolUse")
    lines = [f"foreshock — preview: this change to {rel(absf)} would…" if preview
             else f"foreshock — you edited {rel(absf)}"]
    if api_change:
        parts = []
        if added: parts.append("+" + ",".join(sorted(added)))
        if removed: parts.append("−" + ",".join(sorted(removed)))
        if decl_touched: parts.append("~" + ",".join(sorted(decl_touched)) + " (declaration)")
        for u, m in union_added.items(): parts.append(f"{u}+{{{','.join(sorted(m))}}}")
        for u, m in union_removed.items(): parts.append(f"{u}−{{{','.join(sorted(m))}}}")
        lines.append("  • API change: " + "; ".join(parts))
    elif content_only:
        lines.append(f"  • content-only: changed the body of `{body_sym}` — import contract intact, behavior may differ"
                     if body_sym else
                     "  • content-only: exported API unchanged — dependents' import contract intact")
    lines.append(f"  • blast radius: {n} file(s) import this [{t}]")

    if direct:
        rows, affected = [], []
        for d in direct:
            used = plugin_of(d).imported_names(text[d], absf, d, ctx[plugin_of(d)])
            hit = used & changed if changed else set()
            tag = f" ({', '.join(sorted(used)[:4])})" if used else ""
            rows.append(("→ " if hit else "  ") + f"{rel(d)}{tag}")
            if hit: affected.append(rel(d))
        lines.append("  • who imports this:")
        lines += ["      " + r for r in rows[:8]] + (["      …"] if len(rows) > 8 else [])
        if changed and affected:
            lines.append("  • → = imports a CHANGED symbol — re-check those call sites")
        elif content_only:
            lines.append("  • no dependent's import contract changed — a behavior change is the only thing to weigh")

    covering = sorted(rel(tf) for tf in test_files if plugin_of(tf) is p
                      and any(p.resolve(tf, spec, ctx[p]) == absf for spec in p.specs(text[tf])))
    if covering:
        lines.append("  • covered by tests: " + ", ".join(covering[:5]) + (" …" if len(covering) > 5 else ""))

    same_lang = [f for f in src_files if plugin_of(f) is p and f != absf]
    for u, m in union_added.items():
        cons = [rel(f) for f in same_lang if re.search(rf"\b{u}\b", text[f])]
        lines.append(f"  • ADDED to the `{u}` set ({', '.join(sorted(m))}) — handle the new case at: " + ", ".join(cons[:6]))
    for u, m in union_removed.items():
        cons = [rel(f) for f in same_lang if re.search(rf"\b{u}\b", text[f])]
        lines.append(f"  • REMOVED from the `{u}` set ({', '.join(sorted(m))}) — drop stale handling at: " + ", ".join(cons[:6]))
    if old_src is None:                                   # diff-blind fallback (manual runs)
        for u in p.unions(new_src):
            cons = [rel(f) for f in same_lang if re.search(rf"\b{u}\b", text[f])]
            if cons:
                lines.append(f"  • defines the `{u}` set — if you changed its members, update: " + ", ".join(cons[:6]))

    # ---- Tier 3: deep simulation (opt-in). Apply the projected edit in an isolated copy,
    #      run the project's real checker, and surface only the NEW diagnostics it introduces. ----
    if os.environ.get("FORESHOCK_DEEP") and new_src is not None and old_src is not None:
        try:
            import deep_check
            diags = deep_check.run(absf, new_src, ROOT, dependents=list(direct))
            if diags:
                lines.append("  • deep check — NEW errors this change introduces (real checker):")
                lines += ["      ✗ " + d for d in diags[:8]] + (["      …"] if len(diags) > 8 else [])
            elif diags == []:
                lines.append("  • deep check: no new type/compile errors introduced ✓")
        except Exception:
            pass

    print("\n".join(lines))
    sys.exit(0)

# ===== repo map =====
by_lang = collections.Counter(plugin_of(f).__name__.replace("lang_", "") for f in src_files)
print(f"{len(src_files)} source files ({dict(by_lang)}), {sum(len(v) for v in imp.values())} import edges\n")
print("blast-radius hot spots (a change here ripples widest):")
for f in sorted(fileset, key=lambda f: len(transitive_dependents(f)), reverse=True)[:12]:
    n = len(transitive_dependents(f))
    if n == 0: break
    print(f"  {n:>4} ← {rel(f)}")
