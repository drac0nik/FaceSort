"""The code's layout holds together: every module imports, the window's parts don't clash,
every database connection goes through the write gate, and the version is in the README."""
import ast
import glob
import importlib
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, "*.py")))


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name, qapp):
    importlib.import_module(name)


def mixin_files():
    return sorted(glob.glob(os.path.join(ROOT, "mw_*.py")))


def methods_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(item.name, []).append(f"{os.path.basename(path)}:{node.name}")
    return out


def test_window_parts_dont_define_the_same_method_twice():
    seen = {}
    for p in mixin_files() + [os.path.join(ROOT, "facesort.py")]:
        tree = ast.parse(open(p, encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and (node.name.endswith("Mixin") or node.name == "MainWindow"):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        seen.setdefault(item.name, []).append(f"{os.path.basename(p)}:{node.name}")
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not clashes, clashes


def test_every_mixin_is_part_of_the_window(qapp):
    import facesort
    mro = facesort.MainWindow.__mro__
    for p in mixin_files():
        mod = importlib.import_module(os.path.splitext(os.path.basename(p))[0])
        for name, obj in vars(mod).items():
            if isinstance(obj, type) and name.endswith("Mixin") and obj.__module__ == mod.__name__:
                assert obj in mro, f"{name} isn't in MainWindow"


def test_connections_go_through_the_gate():
    """Only store.connect() opens the library (sqlite3.connect elsewhere would skip the gate)."""
    bad = []
    for p in glob.glob(os.path.join(ROOT, "*.py")):
        name = os.path.basename(p)
        for n, line in enumerate(open(p, encoding="utf-8"), 1):
            if "sqlite3.connect(" in line and name not in ("store.py", "known.py", "clip_search.py", "dbgate.py"):
                if "library.db" in line or "data_dir()" in line:
                    bad.append(f"{name}:{n}")
    assert not bad, bad
    src = open(os.path.join(ROOT, "store.py"), encoding="utf-8").read()
    assert "factory=dbgate.GatedConnection" in src


def test_no_undefined_names():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes not installed")
    files = glob.glob(os.path.join(ROOT, "*.py"))
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files], capture_output=True, text=True).stdout
    undefined = [line for line in out.splitlines() if "undefined name '" in line]
    assert not undefined, "\n".join(undefined)


def test_version_is_in_readme():
    import ui_common
    readme = open(os.path.join(ROOT, "README.txt"), encoding="utf-8").read()
    major_minor = ".".join(ui_common.APP_VERSION.split(".")[:2])
    assert re.search(rf"\b{re.escape(major_minor)}\b", readme), f"README has no section for {major_minor}"
