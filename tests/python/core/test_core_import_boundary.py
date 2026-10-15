"""Standalone source and tests may not import legacy code, even lazily."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest

from codex_harness.core.allowlist import ALLOWED_EXTERNAL_MODULES, FORBIDDEN

CORE = "codex_harness.core"
SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "codex_harness"


@dataclass(frozen=True)
class ImportEdge:
    target: str
    line: int
    parent_package: bool = False


class ImportCollector(ast.NodeVisitor):
    def __init__(self, module: str, packages: set[str], modules: set[str]) -> None:
        self.package = module if module in packages else module.rpartition(".")[0]
        self.modules = modules
        self.direct: list[ImportEdge] = []
        self.eager: list[ImportEdge] = []
        self.nonliteral_calls: list[int] = []
        self.at_module_scope = True
        self.importlib_names = {"importlib"}
        self.import_module_names: set[str] = set()

    def add(self, target: str, line: int) -> None:
        if target == "codex_harness" or target.startswith("codex_harness."):
            edge = ImportEdge(target, line)
            self.direct.append(edge)
            if self.at_module_scope:
                self.eager.append(edge)

    def absolute(self, module: str, level: int) -> str:
        if not level:
            return module
        parts = self.package.split(".")
        base = ".".join(parts[: len(parts) - level + 1])
        return f"{base}.{module}" if module else base

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add(alias.name, node.lineno)
            if alias.name == "importlib":
                self.importlib_names.add(alias.asname or alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self.absolute(node.module or "", node.level)
        self.add(base, node.lineno)
        for alias in node.names:
            child = f"{base}.{alias.name}"
            if child in self.modules:
                self.add(child, node.lineno)
            if base == "importlib" and alias.name == "import_module":
                self.import_module_names.add(alias.asname or alias.name)

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if type_checking:
            for statement in node.orelse:
                self.visit(statement)
        else:
            self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators/defaults run at definition time; the function body does not.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for parameter in getattr(node, "type_params", []):
            self.visit(parameter)
        previous = self.at_module_scope
        self.at_module_scope = False
        for statement in node.body:
            self.visit(statement)
        self.at_module_scope = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        previous = self.at_module_scope
        self.at_module_scope = False
        self.visit(node.body)
        self.at_module_scope = previous

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        dynamic_import = (
            isinstance(function, ast.Name)
            and function.id in {"__import__", *self.import_module_names}
        ) or (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id in self.importlib_names
        )
        if dynamic_import:
            argument = (
                node.args[0]
                if node.args
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "name"), None
                )
            )
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                self.nonliteral_calls.append(node.lineno)
            else:
                name = argument.value
                if name.startswith("."):
                    # Resolve import_module's literal package argument when supplied.
                    package = (
                        node.args[1]
                        if len(node.args) > 1
                        else next((kw.value for kw in node.keywords if kw.arg == "package"), None)
                    )
                    if isinstance(package, ast.Constant) and isinstance(package.value, str):
                        level = len(name) - len(name.lstrip("."))
                        base = ".".join(package.value.split(".")[: -level + 1 or None])
                        name = f"{base}.{name.lstrip('.')}".rstrip(".")
                self.add(name, node.lineno)
        self.generic_visit(node)


def collect(source: str, module: str, packages: set[str], modules: set[str]) -> ImportCollector:
    collector = ImportCollector(module, packages, modules)
    collector.visit(ast.parse(source, filename=module))
    return collector


def parent_edges(module: str, packages: set[str]) -> list[ImportEdge]:
    parts = module.split(".")
    return [
        ImportEdge(parent, 0, parent_package=True)
        for length in range(1, len(parts))
        if (parent := ".".join(parts[:length])) in packages
    ]


def is_forbidden(module: str) -> bool:
    return any(
        module.startswith(rule)
        if rule.endswith("_")
        else module == rule
        if rule.startswith("codex_harness.")
        else rule in module
        for rule in FORBIDDEN
    )


def forbidden_chain(root: str, graph: dict[str, list[ImportEdge]]) -> str | None:
    """Breadth-first search returns the shortest offending chain, including edge lines."""
    pending: deque[tuple[str, list[tuple[str, ImportEdge]]]] = deque([(root, [])])
    seen = {root}
    while pending:
        module, chain = pending.popleft()
        if is_forbidden(module):
            return " -> ".join(
                [
                    f"{source}:{edge.line}"
                    + (" (parent package __init__)" if edge.parent_package else "")
                    for source, edge in chain
                ]
                + [module]
            )
        for edge in graph.get(module, []):
            if edge.target not in seen:
                seen.add(edge.target)
                pending.append((edge.target, [*chain, (module, edge)]))
    return None


@pytest.fixture(scope="module")
def imports() -> tuple[dict[str, ImportCollector], dict[str, list[ImportEdge]], set[str]]:
    paths = {}
    packages = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        parts = path.relative_to(SOURCE_ROOT.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        paths[module] = path
        if path.name == "__init__.py":
            packages.add(module)
    modules = set(paths)
    collectors = {
        module: collect(path.read_text(encoding="utf-8-sig"), module, packages, modules)
        for module, path in paths.items()
    }
    graph = {
        module: [*collector.direct, *parent_edges(module, packages)]
        for module, collector in collectors.items()
    }
    return collectors, graph, packages


def core_modules(collectors: dict[str, ImportCollector]) -> list[str]:
    return [module for module in collectors if module == CORE or module.startswith(f"{CORE}.")]


def test_core_direct_imports_are_allowlisted(imports) -> None:
    collectors, _, packages = imports
    assert core_modules(collectors), "No core modules discovered"
    assert ALLOWED_EXTERNAL_MODULES <= collectors.keys(), "Allowlist contains missing modules"
    allowed_packages = ALLOWED_EXTERNAL_MODULES & packages
    violations = []
    for module in core_modules(collectors):
        for edge in collectors[module].direct:
            target = edge.target
            if target == CORE or target.startswith(f"{CORE}."):
                continue
            if target in ALLOWED_EXTERNAL_MODULES or any(
                target.startswith(f"{package}.") for package in allowed_packages
            ):
                continue
            violations.append(f"{module}:{edge.line} -> {target}")
    assert not violations, "Unallowlisted direct imports:\n" + "\n".join(violations)


def test_standalone_has_no_external_allowlist_or_legacy_test_imports(imports) -> None:
    collectors, _, packages = imports
    assert not ALLOWED_EXTERNAL_MODULES
    modules = set(collectors)
    violations = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        collector = collect(
            path.read_text(encoding="utf-8-sig"), CORE + ".tests", packages, modules
        )
        violations.extend(
            f"{path.name}:{edge.line} -> {edge.target}"
            for edge in collector.direct
            if edge.target != CORE and not edge.target.startswith(CORE + ".")
        )
        violations.extend(
            f"{path.name}:{line} -> nonliteral dynamic import"
            for line in collector.nonliteral_calls
        )
    assert not violations, "Legacy imports in core tests:\n" + "\n".join(violations)


def test_core_top_level_closure_excludes_forbidden_modules(imports) -> None:
    collectors, graph, _ = imports
    violations = [
        chain
        for module in core_modules(collectors)
        if (chain := forbidden_chain(module, graph)) is not None
    ]
    assert not violations, "Forbidden import chains:\n" + "\n".join(violations)


def test_core_dynamic_imports_have_literal_arguments(imports) -> None:
    collectors, _, _ = imports
    violations = [
        f"{module}:{line}"
        for module in core_modules(collectors)
        for line in collectors[module].nonliteral_calls
    ]
    assert not violations, "Nonliteral dynamic imports:\n" + "\n".join(violations)


def test_checker_detects_shortest_forbidden_import_chain() -> None:
    graph = {
        CORE: [ImportEdge(f"{CORE}.long", 2), ImportEdge("codex_harness.proof.models", 3)],
        f"{CORE}.long": [ImportEdge(f"{CORE}.longer", 4)],
        f"{CORE}.longer": [ImportEdge("codex_harness.state.store", 5)],
        "codex_harness.proof.models": [ImportEdge("codex_harness.state.store", 7)],
    }
    assert forbidden_chain(CORE, graph) == (
        "codex_harness.core:3 -> codex_harness.proof.models:7 -> codex_harness.state.store"
    )


def test_collector_resolves_relative_imports_and_parent_initializers() -> None:
    packages = {CORE, "codex_harness.proof"}
    modules = packages | {f"{CORE}.graph", "codex_harness.proof.models"}
    collector = collect(
        "from . import graph\n"
        "from ..proof import models\n"
        "import codex_harness.proof.models\n"
        "if TYPE_CHECKING:\n"
        "    import codex_harness.state.store\n"
        "if typing.TYPE_CHECKING:\n"
        "    import codex_harness.operator.api\n"
        "def factory():\n"
        "    import codex_harness.state.worker_process_repository\n",
        CORE,
        packages,
        modules,
    )
    assert collector.eager == [
        ImportEdge(CORE, 1),
        ImportEdge(f"{CORE}.graph", 1),
        ImportEdge("codex_harness.proof", 2),
        ImportEdge("codex_harness.proof.models", 2),
        ImportEdge("codex_harness.proof.models", 3),
    ]
    assert collector.direct == [
        *collector.eager,
        ImportEdge("codex_harness.state.worker_process_repository", 9),
    ]
    graph = {
        CORE: [ImportEdge("codex_harness.proof.models", 3)],
        "codex_harness.proof.models": parent_edges("codex_harness.proof.models", packages),
        "codex_harness.proof": [ImportEdge("codex_harness.execution.linux_launch", 12)],
    }
    assert forbidden_chain(CORE, graph) == (
        "codex_harness.core:3 -> codex_harness.proof.models:0 (parent package __init__)"
        " -> codex_harness.proof:12 -> codex_harness.execution.linux_launch"
    )
    assert graph["codex_harness.proof.models"] == [
        ImportEdge("codex_harness.proof", 0, parent_package=True)
    ]


@pytest.mark.parametrize(
    ("source", "nonliteral"),
    [
        ('importlib.import_module("codex_harness.state.store")', False),
        ('__import__("codex_harness.state.store")', False),
        ("importlib.import_module(module_name)", True),
        ("__import__(name=module_name)", True),
        ("import importlib as loader\nloader.import_module(module_name)", True),
        ("from importlib import import_module as load\nload(module_name)", True),
        ("def factory():\n    return __import__(module_name)", True),
        ("def factory() -> importlib.import_module(module_name):\n    pass", True),
    ],
)
def test_collector_checks_dynamic_imports(source: str, nonliteral: bool) -> None:
    collector = collect(source, CORE, {CORE}, {CORE})
    assert bool(collector.nonliteral_calls) is nonliteral
    if not nonliteral:
        assert collector.eager == [ImportEdge("codex_harness.state.store", 1)]
