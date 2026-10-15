"""Static template-local proof imports shared by dossiers and worker packets."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from codex_harness.core.plan_store import PlanStore
from codex_harness.core.runners import Runner
from codex_harness.core.spine import compile_manifest, requirement_file_sources


def python_import_paths(path: Path, root: Path, body: str):
    """Yield existing Python imports and package initializers inside root."""
    tree = ast.parse(body, filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((module, node.level))
            imports.extend(
                ((module + "." if module else "") + alias.name, node.level)
                for alias in node.names
                if alias.name != "*"
            )
    for module, level in imports:
        bases = [path.parent, root, root / "src"]
        if level:
            base = path.parent
            for _ in range(level - 1):
                base = base.parent
            bases = [base]
        for base in bases:
            target = base.joinpath(*module.split(".")) if module else base
            candidates = [target.with_suffix(".py"), target / "__init__.py"]
            if not any(p.resolve().is_relative_to(root) and p.is_file() for p in candidates):
                continue
            # Package initializers can import more protected helpers.
            candidates += [parent / "__init__.py" for parent in target.parents if parent != root]
            for candidate in candidates:
                candidate = candidate.resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    continue
                yield candidate
            if any(p.resolve().is_relative_to(root) and p.is_file() for p in candidates[:2]):
                break


def protected_helpers(proofs: list[Path], root: Path, runner: Runner, requirements) -> list[Path]:
    """Follow static imports transitively, exposing only protected template files."""
    root = root.resolve()
    protected = [(root / name).resolve() for name in (runner.test_dir, *requirements)]
    seen = {path.resolve() for path in proofs}
    pending = sorted(seen)
    helpers = set()
    while pending:
        path = pending.pop(0)
        body = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            candidates = python_import_paths(path, root, body)
        elif runner.name == "command":
            candidates = []
            for pattern in runner.import_patterns or ():
                for match in re.finditer(pattern, body):
                    specifier = match[1]
                    if not specifier or not specifier.startswith(("./", "../")):
                        continue
                    target = path.parent / specifier
                    options = [target, *(Path(str(target) + ext) for ext in (".ts", ".js"))]
                    options.extend(target / ("index" + ext) for ext in (".ts", ".js"))
                    if target.suffix == ".js":
                        options.append(target.with_suffix(".ts"))
                    resolved = next((item.resolve() for item in options if item.is_file()), None)
                    if resolved is not None:
                        candidates.append(resolved)
        else:
            continue
        for candidate in candidates:
            if candidate in seen or not candidate.is_relative_to(root):
                continue
            if not any(
                candidate == boundary or (boundary.is_dir() and candidate.is_relative_to(boundary))
                for boundary in protected
            ):
                continue
            seen.add(candidate)
            helpers.add(candidate)
            pending.append(candidate)
    return sorted(helpers)


def _protected_tests(store: PlanStore) -> list[dict]:
    template = store.workspace / "template"
    if not template.is_dir():
        return []
    spine = store.spine()
    manifest = compile_manifest(spine, template)
    sources = requirement_file_sources(template, manifest["requirementFiles"])
    selected = {}
    for requirement in sorted(spine.ids("requirement")):
        for test_id in sorted(spine.ids("test")):
            test = spine.node(test_id)
            if test_id not in spine.descendants(requirement):
                continue
            module = test["fields"].get("module", "")
            for name, path in sources.items():
                if name == module or name.endswith("/" + module):
                    entry = selected.setdefault(path.resolve(), {"name": name, "owners": set()})
                    entry["owners"].add(requirement)
    root = template.resolve()
    pending = list(selected)
    while pending:
        path = pending.pop(0)
        entry = selected[path]
        body = path.read_text(encoding="utf-8", errors="replace")
        entry["body"] = body
        if path.suffix != ".py":
            continue
        for candidate in python_import_paths(path, root, body):
            helper = selected.setdefault(
                candidate,
                {"name": candidate.relative_to(root).as_posix(), "owners": set()},
            )
            new_owners = entry["owners"] - helper["owners"]
            if new_owners:
                helper["owners"].update(new_owners)
                pending.append(candidate)
    return sorted(selected.values(), key=lambda item: (min(item["owners"]), item["name"]))
