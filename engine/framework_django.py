"""foreshock framework adapter: Django.

Adds convention/runtime edges the import graph CAN'T see. The headline one: Django relations are
often declared by STRING — `models.ForeignKey("blog.Post")` — with no `import` of the target model.
So editing `Post` looks like it has zero dependents to a pure import graph, when in fact every app
that points a string FK/M2M/O2O at it is coupled. This adapter recovers those edges.

Contract (same shape any framework adapter follows):
  detect(root)            -> bool
  edges(root, files, text) -> list[(src_abs, target_abs)]   # extra edges to fold into the graph
"""
import os, re

def detect(root):
    if os.path.exists(os.path.join(root, "manage.py")):
        return True
    import glob
    return any("INSTALLED_APPS" in open(s, errors="ignore").read()
               for s in glob.glob(os.path.join(root, "**", "settings.py"), recursive=True)[:5])

_MODEL = re.compile(r"^\s*class\s+(\w+)\s*\(([^)]*)\)", re.M)
_REL = re.compile(r"""(?:ForeignKey|OneToOneField|ManyToManyField)\(\s*["']([\w.]+)["']"""
                  r"""|\bto\s*=\s*["']([\w.]+)["']""")

def _app_label(f, root):
    # Django app label defaults to the app package name — the dir holding models.py
    return os.path.basename(os.path.dirname(f))

def _model_maps(root, files, text):
    """ModelName -> {files}, and 'applabel.ModelName' -> file."""
    by_name, by_qual = {}, {}
    for f in files:
        if not f.endswith(".py"):
            continue
        for m in _MODEL.finditer(text[f]):
            name, bases = m.group(1), m.group(2)
            if "Model" not in bases:                 # only Django models (base mentions ...Model)
                continue
            by_name.setdefault(name, set()).add(f)
            by_qual[f"{_app_label(f, root)}.{name}"] = f
    return by_name, by_qual

def edges(root, files, text):
    by_name, by_qual = _model_maps(root, files, text)
    out = []
    for f in files:
        if not f.endswith(".py"):
            continue
        for m in _REL.finditer(text[f]):
            ref = m.group(1) or m.group(2)
            if not ref or ref in ("self",):
                continue
            target = None
            if "." in ref:                           # "app.Model"
                target = by_qual.get(ref)
                if not target:                       # fall back to the bare model name
                    cand = by_name.get(ref.split(".")[-1])
                    target = next(iter(cand)) if cand and len(cand) == 1 else None
            else:                                    # bare "Model" in the same project
                cand = by_name.get(ref)
                target = next(iter(cand)) if cand and len(cand) == 1 else None
            if target and target != f:
                out.append((f, target))
    return out
