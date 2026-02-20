import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = (REPO_ROOT / "src" / "core").resolve()
REFERENCE_ROOTS = [
    (REPO_ROOT / "src").resolve(),
    (REPO_ROOT / "skills").resolve(),
]


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _collect_core_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in _iter_python_files(CORE_ROOT):
        if path.name == "__init__.py":
            continue
        module_name = ".".join(path.relative_to(CORE_ROOT).with_suffix("").parts)
        modules[module_name] = path
    return modules


def _record_core_reference(references: set[str], module_name: str) -> None:
    raw = str(module_name or "").strip()
    if not raw.startswith("core."):
        return
    reference = raw.removeprefix("core.").strip(".")
    if reference:
        references.add(reference)


def _collect_core_references() -> set[str]:
    references: set[str] = set()
    for root in REFERENCE_ROOTS:
        for path in _iter_python_files(root):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        _record_core_reference(references, alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        _record_core_reference(references, node.module)
                elif isinstance(node, ast.Call):
                    func = node.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    if func.attr != "import_module" or not node.args:
                        continue
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        _record_core_reference(references, arg.value)
    return references


def test_core_modules_are_referenced_by_runtime_code():
    modules = _collect_core_modules()
    references = _collect_core_references()

    unreferenced = sorted(name for name in modules if name not in references)
    assert not unreferenced, "Found unreferenced src/core modules:\n" + "\n".join(
        f"- {name} ({modules[name].relative_to(REPO_ROOT)})" for name in unreferenced
    )
