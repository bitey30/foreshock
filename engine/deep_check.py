"""foreshock deep-check (Tier 3): simulate the proposed edit, run the real checker, report NEW errors.

Opt-in (FORESHOCK_DEEP=1) because it runs a project toolchain and is slower than the static packet.
Strategy — never touch the user's real files:
  1. copy the repo into a temp dir (excluding .git / node_modules / build dirs), symlinking
     node_modules back so the type-checker can still resolve dependencies;
  2. run the checker on the copy as a BASELINE;
  3. apply the projected new content to the copy and run the checker again;
  4. return only the diagnostics the change INTRODUCED (after − baseline).
Fail-safe: returns None if no checker is available / on any error; [] if it ran clean.

Checkers: TS/JS → tsc · Python → mypy, else pyflakes, else stdlib py_compile (syntax only) ·
Java → javac · Go → go build. Whichever is present wins; absent toolchains skip gracefully.
"""
import os, re, shutil, subprocess, sys, tempfile, glob

TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _run(cmd, cwd, timeout):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return None


def _has_mod(m):
    return subprocess.run([sys.executable, "-c", f"import {m}"], capture_output=True).returncode == 0

def _toolchain_ok(probe, pattern):
    """A checker is only usable if its toolchain actually RUNS (guards e.g. the macOS javac stub
    that has no JDK and would otherwise make every change look 'clean')."""
    out = _run(probe, None, 15)
    return bool(out and re.search(pattern, out))


# ---------- isolation ----------
def _copy_tree(root):
    tmp = tempfile.mkdtemp(prefix="fs_deep_")
    dst = os.path.join(tmp, "repo")
    if shutil.which("rsync"):
        if subprocess.run(["rsync", "-a", "--exclude", ".git", "--exclude", "node_modules",
                           "--exclude", "dist", "--exclude", "build", "--exclude", ".next",
                           root.rstrip("/") + "/", dst + "/"],
                          capture_output=True, timeout=60).returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True); return None
    else:
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns(
            ".git", "node_modules", "dist", "build", ".next"), symlinks=True)
    nm = os.path.join(root, "node_modules")
    if os.path.isdir(nm):
        try: os.symlink(nm, os.path.join(dst, "node_modules"))
        except OSError: pass
    return dst


# ---------- per-language checkers: (copy_root) -> set(diagnostic lines) ----------
def _which_tsc(root):
    local = os.path.join(root, "node_modules", ".bin", "tsc")
    if os.path.exists(local): return [local]
    if shutil.which("npx"): return ["npx", "--yes", "tsc"]
    if shutil.which("tsc"): return ["tsc"]
    return None

def _ts_diags(cr, timeout):
    out = _run(_which_tsc(cr) + ["--noEmit", "--pretty", "false"], cr, timeout)
    return set(re.findall(r"^.+?\(\d+,\d+\): error TS\d+:.*$", out or "", re.M))

def _py_diags(cr, rel_files, timeout):
    files = [os.path.join(cr, p) for p in rel_files]
    if _has_mod("mypy"):
        out = _run([sys.executable, "-m", "mypy", "--no-error-summary", "--no-color-output",
                    "--follow-imports=normal", *files], cr, timeout)
        return set(re.findall(r"^.+?:\d+: error:.*$", out or "", re.M))
    if _has_mod("pyflakes"):
        out = _run([sys.executable, "-m", "pyflakes", *files], cr, timeout)
        return set(l for l in (out or "").splitlines() if re.search(r":\d+:", l))
    import py_compile                                   # stdlib fallback: syntax only
    diags = set()
    for f in files:
        try: py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e: diags.add(str(e).strip().splitlines()[-1])
        except Exception: pass
    return diags

def _java_diags(cr, rel_files, timeout):
    srcs = [os.path.join(cr, p) for p in rel_files if os.path.exists(os.path.join(cr, p))]
    if not srcs: return set()
    # -sourcepath lets javac pull in referenced types (incl. cross-package dependents)
    out = _run(["javac", "-sourcepath", cr, "-d", tempfile.mkdtemp(prefix="fs_javac_"), *srcs], cr, timeout)
    return set(re.findall(r"^.+\.java:\d+: error:.*$", out or "", re.M))

def _go_diags(cr, timeout):
    out = _run(["go", "build", "./..."], cr, timeout)
    return set(l for l in (out or "").splitlines() if re.search(r"\.go:\d+:", l))

def _ruby_diags(cr, rel_files, timeout):             # `ruby -c` is per-file syntax only
    diags = set()
    for p in rel_files:
        out = _run(["ruby", "-c", os.path.join(cr, p)], cr, timeout)
        if out and "Syntax OK" not in out:
            diags |= set(l for l in out.splitlines() if ".rb:" in l)
    return diags


def run(edited_abs, new_content, root, dependents=(), timeout=120):
    ext = os.path.splitext(edited_abs)[1]
    relf = os.path.relpath(edited_abs, root)
    rel_files = [relf] + [os.path.relpath(p, root) for p in dependents]

    if ext in TS_EXT:
        if not _which_tsc(root):
            return ["(deep check skipped: no tsc — `npm i -D typescript` or ensure npx is available)"]
        checker = lambda cr: _ts_diags(cr, timeout)
    elif ext == ".py":
        checker = lambda cr: _py_diags(cr, rel_files, timeout)
    elif ext == ".java":
        if not _toolchain_ok(["javac", "-version"], r"javac \d"): return None   # real JDK, not the stub
        checker = lambda cr: _java_diags(cr, rel_files, timeout)
    elif ext == ".go":
        if not _toolchain_ok(["go", "version"], r"go version"): return None
        checker = lambda cr: _go_diags(cr, timeout)
    elif ext == ".rb":
        if not _toolchain_ok(["ruby", "--version"], r"ruby \d"): return None
        checker = lambda cr: _ruby_diags(cr, rel_files, timeout)
    else:
        return None

    copy_root = _copy_tree(root)
    if not copy_root:
        return None
    try:
        before = checker(copy_root)
        open(os.path.join(copy_root, relf), "w", encoding="utf-8").write(new_content)
        after = checker(copy_root)
        # rewrite temp-copy paths back to repo-relative so diagnostics read cleanly
        return [d.replace(copy_root + os.sep, "").replace(copy_root + "/", "")
                for d in sorted(after - before)]
    except Exception:
        return None
    finally:
        shutil.rmtree(os.path.dirname(copy_root), ignore_errors=True)
