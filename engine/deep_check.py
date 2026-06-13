"""foreshock deep-check (Tier 3): simulate the proposed edit, run the real checker, report NEW errors.

Opt-in (FORESHOCK_DEEP=1) because it runs a project toolchain and is slower than the static packet.
Strategy — never touch the user's real files:
  1. copy the repo into a temp dir (excluding .git / node_modules / build dirs), symlinking
     node_modules back so the type-checker can still resolve dependencies;
  2. run the checker on the copy as a BASELINE;
  3. apply the projected new content to the copy and run the checker again;
  4. return only the diagnostics that the change INTRODUCED (after − baseline).
Fail-safe: returns None if no checker is available / on any error; [] if it ran clean.
"""
import os, re, shutil, subprocess, tempfile

TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _which_tsc(root):
    local = os.path.join(root, "node_modules", ".bin", "tsc")
    if os.path.exists(local):
        return [local]
    if shutil.which("npx"):
        return ["npx", "--yes", "tsc"]
    if shutil.which("tsc"):
        return ["tsc"]
    return None


def _run(cmd, cwd, timeout):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return None


def _copy_tree(root):
    tmp = tempfile.mkdtemp(prefix="fs_deep_")
    dst = os.path.join(tmp, "repo")
    if shutil.which("rsync"):
        ok = subprocess.run(
            ["rsync", "-a", "--exclude", ".git", "--exclude", "node_modules",
             "--exclude", "dist", "--exclude", "build", "--exclude", ".next",
             root.rstrip("/") + "/", dst + "/"],
            capture_output=True, timeout=60).returncode == 0
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True); return None
    else:
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns(
            ".git", "node_modules", "dist", "build", ".next"), symlinks=True)
    nm = os.path.join(root, "node_modules")          # symlink deps back so tsc can resolve them
    if os.path.isdir(nm):
        try: os.symlink(nm, os.path.join(dst, "node_modules"))
        except OSError: pass
    return dst


# checker → set of diagnostic strings for a given copy of the repo
def _ts_diags(copy_root, timeout):
    cmd = _which_tsc(copy_root)
    if not cmd:
        return None
    out = _run(cmd + ["--noEmit", "--pretty", "false"], copy_root, timeout)
    if out is None:
        return None
    return set(re.findall(r"^.+?\(\d+,\d+\): error TS\d+:.*$", out, re.M))


def _py_diags(files):
    import py_compile
    diags = set()
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            diags.add(str(e).strip().splitlines()[-1])
        except Exception:
            pass
    return diags


def run(edited_abs, new_content, root, dependents=(), timeout=90):
    ext = os.path.splitext(edited_abs)[1]
    is_ts, is_py = ext in TS_EXT, ext == ".py"
    if not (is_ts or is_py):
        return None                                   # Java/other: not wired in this prototype
    if is_ts and not _which_tsc(root):
        return ["(deep check skipped: no tsc — `npm i -D typescript` or ensure npx is available)"]

    copy_root = _copy_tree(root)
    if not copy_root:
        return None
    try:
        rel = os.path.relpath(edited_abs, root)
        target = os.path.join(copy_root, rel)
        if is_ts:
            before = _ts_diags(copy_root, timeout) or set()
            open(target, "w", encoding="utf-8").write(new_content)
            after = _ts_diags(copy_root, timeout) or set()
        else:
            files = [os.path.join(copy_root, os.path.relpath(p, root))
                     for p in [edited_abs, *dependents]]
            before = _py_diags(files)
            open(target, "w", encoding="utf-8").write(new_content)
            after = _py_diags(files)
        return sorted(after - before)
    except Exception:
        return None
    finally:
        shutil.rmtree(os.path.dirname(copy_root), ignore_errors=True)
