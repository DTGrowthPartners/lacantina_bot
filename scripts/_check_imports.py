"""Dependency-free static check: verify every `from app.X import name` resolves
to a top-level symbol actually defined/exported in app/X. Catches imports of
symbols removed during the DTGP→Cantina refactor without needing the runtime
deps installed.

Run: python scripts/_check_imports.py   (exit 0 = clean)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def module_to_path(mod: str) -> Path | None:
    rel = mod.replace(".", "/")
    for cand in (ROOT / f"{rel}.py", ROOT / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def toplevel_names(path: Path) -> set[str]:
    """Top-level bound names in a module: defs, classes, assignments, imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    explicit_all: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                    if t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                explicit_all.add(el.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names | explicit_all


def main() -> int:
    problems: list[str] = []
    py_files = sorted(list(APP.rglob("*.py")) + list((ROOT / "scripts").glob("*.py")))
    cache: dict[str, set[str]] = {}
    for f in py_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as e:
            problems.append(f"{f}: SYNTAX {e}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.level and node.level > 0:
                continue
            if not node.module.startswith("app"):
                continue
            target = module_to_path(node.module)
            if target is None:
                problems.append(f"{f.relative_to(ROOT)}:{node.lineno}  ->  module '{node.module}' NOT FOUND")
                continue
            if node.module not in cache:
                cache[node.module] = toplevel_names(target)
            avail = cache[node.module]
            for a in node.names:
                if a.name == "*":
                    continue
                # Valid if it's a top-level symbol OR a submodule (from pkg import submod).
                if a.name in avail:
                    continue
                if module_to_path(f"{node.module}.{a.name}") is not None:
                    continue
                problems.append(
                    f"{f.relative_to(ROOT)}:{node.lineno}  ->  "
                    f"'{a.name}' not defined in '{node.module}'"
                )

    if problems:
        print("BROKEN IMPORTS:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"OK — checked {len(py_files)} files, all intra-app imports resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
