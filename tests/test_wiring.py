"""Composition root / wiring tests — AIYES-02 scope.

Traceability:
  WIRE-01: Composition root instantiates all 13 adapters, sole production importer
"""

from __future__ import annotations

import ast
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════
# WIRE-01: Composition root structure
# ═══════════════════════════════════════════════════════════════════════


class TestCompositionRootExists:
    def test_file_exists(self) -> None:
        path = Path("src/aiyes/cli/composition_root.py")
        assert path.exists(), "composition_root.py does not exist"


class TestCompositionRootImports:
    """composition_root.py imports from adapters and domain use_cases."""

    def test_imports_from_adapters(self) -> None:
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        tree = ast.parse(source)

        adapter_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("aiyes.adapters."):
                    adapter_imports.append(node.module)

        # Must import from adapters
        assert len(adapter_imports) >= 13, (
            f"Expected >= 13 adapter imports, got {len(adapter_imports)}: {adapter_imports}"
        )

    def test_imports_from_use_cases(self) -> None:
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        tree = ast.parse(source)

        uc_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("aiyes.domain.use_cases."):
                    uc_imports.append(node.module)

        assert len(uc_imports) > 0, "composition_root.py must import from use_cases"

    def test_does_not_import_cli_main(self) -> None:
        """No circular imports: must not import from cli.main."""
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "aiyes.cli.main", (
                    "composition_root must not import from cli.main"
                )

    def test_re_exports_presenter(self) -> None:
        """composition_root re-exports presenter functions for main.py boundary."""
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        tree = ast.parse(source)

        presenter_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "aiyes.cli.presenter":
                    for alias in node.names:
                        presenter_imports.append(alias.name)

        # Must re-export key presenter functions
        expected = [
            "format_session_start",
            "format_system_error",
            "format_do",
            "mask_node_dict",
        ]
        for name in expected:
            assert name in presenter_imports, (
                f"composition_root must re-export {name} from presenter"
            )

    def test_does_not_import_click(self) -> None:
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "click", (
                        "composition_root must not import click"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "click", (
                    "composition_root must not import from click"
                )


class TestCompositionRootInstantiation:
    """Composition root instantiates all 13 adapters and use cases via __init__."""

    def test_all_13_adapter_modules_imported(self) -> None:
        source = Path("src/aiyes/cli/composition_root.py").read_text()

        expected_adapters = [
            "xvfb_adapter",
            "display_allocator_adapter",
            "atspi_bus_adapter",
            "atspi_tree_adapter",
            "atspi_action_adapter",
            "scrot_adapter",
            "xdotool_adapter",
            "subprocess_adapter",
            "file_session_repository",
            "file_tree_store",
            "file_screenshot_store",
            "system_clock",
            "system_dependency_check",
        ]

        for adapter in expected_adapters:
            assert adapter in source, (
                f"composition_root.py missing adapter import: {adapter}"
            )

    def test_no_factory_functions(self) -> None:
        """Use cases must be instantiated via __init__, not factory functions."""
        source = Path("src/aiyes/cli/composition_root.py").read_text()
        # No make_* factory function references for use cases
        assert "make_session_start" not in source
        assert "factory_fn" not in source


class TestAdapterImportBoundary:
    """Only composition_root.py may import from aiyes.adapters in production code."""

    def test_sole_production_importer(self) -> None:
        """No production module besides composition_root.py imports from adapters."""
        production_dirs = [
            Path("src/aiyes/cli"),
            Path("src/aiyes/domain"),
            Path("src/aiyes/ports"),
        ]

        violations = []
        for prod_dir in production_dirs:
            if not prod_dir.exists():
                continue
            for py_file in prod_dir.rglob("*.py"):
                # composition_root.py is the sole allowed importer
                if py_file.name == "composition_root.py":
                    continue

                source = py_file.read_text()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith("aiyes.adapters"):
                            violations.append(f"{py_file}: imports {node.module}")

        assert violations == [], "Adapter import boundary violated:\n" + "\n".join(
            violations
        )


class TestResolveSessionId:
    """WIRE-01/PC-02: resolve_session_id helper."""

    def test_resolve_session_id_callable_exists(self) -> None:
        from aiyes.cli.composition_root import resolve_session_id

        assert callable(resolve_session_id)
