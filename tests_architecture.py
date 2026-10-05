"""Architecture fitness checks: keep the module boundaries from eroding.

These are ratchets, not style rules. Each limit is the size or coupling the code
had when it was set; lowering a number after a cleanup is welcome, raising it
means the code is drifting back toward one big file. When a check fails, the
message says where the code should go instead (see docs/architecture.md).

Static only (AST and text): it runs in milliseconds and does not import main.py.
"""
import ast
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
FAILED = []

# ---- size ceilings (lines) -------------------------------------------------
MAIN_PY_MAX = 6150          # 10,306 before the second Api decomposition
API_CLASS_MAX = 3000        # 7,254 before; new js_api methods go in an api_<domain>.py mixin
DEFAULT_MODULE_MAX = 1500   # any other non-test .py file
LEGACY_MAX = {              # oversized files that predate the budget: may shrink, not grow
    "bridge_telegram.py": 7300,
    "frame_link.py": 2100,
    "agent_status.py": 1950,
    "api_history.py": 1600,
    "usage_probe.py": 1400,
    "web/index.html": 10250,
}

# ---- late-bound `main.<name>` references per mixin (see api_host.py) -------
# Moving a helper out of main.py into a module the mixin can import lowers these.
SEAM_MAX = {
    "api_accounts.py": 69, "api_bridges.py": 22, "api_desktop.py": 22,
    "api_extensions.py": 23, "api_glasses.py": 16, "api_history.py": 0,
    "api_link.py": 12, "api_remote.py": 45, "api_schedules.py": 0,
    "api_status.py": 11, "api_update.py": 44, "api_voice.py": 21,
}
NEW_MIXIN_SEAM_MAX = 10


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n         {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def lines(path):
    return len(path.read_text(encoding="utf-8").splitlines())


def mixin_modules():
    out = {}
    for p in sorted(HERE.glob("api_*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith("ApiMixin")]
        if classes:
            out[p.name] = (tree, classes)
    return out


def api_class():
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    return tree, next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def test_size_budgets():
    n = lines(HERE / "main.py")
    check(f"main.py <= {MAIN_PY_MAX} lines (now {n})", n <= MAIN_PY_MAX,
          "move a cohesive group of functions into its own module instead of growing main.py")
    _, api = api_class()
    span = api.end_lineno - api.lineno + 1
    check(f"Api class in main.py <= {API_CLASS_MAX} lines (now {span})", span <= API_CLASS_MAX,
          "put new js_api methods in the matching api_<domain>.py mixin")
    for p in sorted(HERE.glob("*.py")):
        if p.name.startswith(("tests_", "test_", "_")) or p.name == "main.py":
            continue
        cap = LEGACY_MAX.get(p.name, MAIN_PY_MAX if p.name == "main.py" else DEFAULT_MODULE_MAX)
        n = lines(p)
        if n > cap:
            check(f"{p.name} <= {cap} lines (now {n})", False,
                  "split it along a domain boundary; see docs/architecture.md")
    for rel, cap in LEGACY_MAX.items():
        if rel.endswith(".html"):
            n = lines(HERE / rel)
            check(f"{rel} <= {cap} lines (now {n})", n <= cap,
                  "new UI code belongs in its own file, not the single index.html")


def test_mixins_do_not_import_main():
    bad = []
    for name, (tree, _) in mixin_modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "main" for a in node.names):
                bad.append(name)
            if isinstance(node, ast.ImportFrom) and node.module == "main":
                bad.append(name)
    check("no api_*.py mixin imports main", not bad,
          f"{sorted(set(bad))}: main.py runs as __main__; use `from api_host import main`")


def test_no_method_defined_twice():
    owners = {}
    _, api = api_class()
    for m in api.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners.setdefault(m.name, []).append("Api")
    for name, (_, classes) in mixin_modules().items():
        for c in classes:
            for m in c.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners.setdefault(m.name, []).append(c.name)
    dup = {k: v for k, v in owners.items() if len(v) > 1}
    check("each Api method is defined in exactly one class", not dup,
          f"{dup}: the MRO would silently pick one of them")


def test_host_is_bound_before_mixins_are_imported():
    src = (HERE / "main.py").read_text(encoding="utf-8")
    bind = src.find("api_host.bind(")
    users = [n for n, (tree, _) in mixin_modules().items()
             if "from api_host import main" in (HERE / n).read_text(encoding="utf-8")]
    first = min((src.find(f"from {n[:-3]} import") for n in users if f"from {n[:-3]} import" in src),
                default=-1)
    check("main.py binds api_host before importing the mixins that use it",
          bind != -1 and (first == -1 or bind < first))


def test_frontend_calls_exist_on_the_backend():
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    called = set(re.findall(r"pywebview\.api\.([A-Za-z_][A-Za-z0-9_]*)", html))
    defined = set()
    _, api = api_class()
    defined |= {m.name for m in api.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for _, (_, classes) in mixin_modules().items():
        for c in classes:
            defined |= {m.name for m in c.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = sorted(called - defined)
    check(f"all {len(called)} js_api methods the UI calls are defined", not missing, f"missing: {missing}")


def test_seam_does_not_grow():
    for name, (tree, _) in mixin_modules().items():
        n = sum(1 for x in ast.walk(tree)
                if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "main")
        cap = SEAM_MAX.get(name, NEW_MIXIN_SEAM_MAX)
        check(f"{name}: late-bound main.* refs <= {cap} (now {n})", n <= cap,
              "import the helper from its own module instead of reaching into main.py")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("ALL PASS")
