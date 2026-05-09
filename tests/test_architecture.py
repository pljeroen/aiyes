"""Tests for architectural constraints: domain purity, port pattern, layer boundaries.

Requirements covered:
  R-ARCH-05: Hexagonal architecture (domain purity, ports as Protocol, layer boundaries)
  R-ARCH-06: Source layout (src/aiyes/ and tests/)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

import pytest

# The project root is expected to be the parent of the tests directory
PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aiyes"
DOMAIN_DIR = SRC_ROOT / "domain"
PORTS_DIR = SRC_ROOT / "ports"
ADAPTERS_DIR = SRC_ROOT / "adapters"


def _get_python_files(directory: Path) -> List[Path]:
    """Recursively find all .py files in a directory."""
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.py"))


def _get_imports_from_file(filepath: Path) -> List[str]:
    """Parse a Python file and extract all import module names."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# Known stdlib modules (representative subset).
# Used to determine if an import is stdlib or external.
_STDLIB_PREFIXES = frozenset(
    [
        "abc",
        "ast",
        "asyncio",
        "base64",
        "bisect",
        "builtins",
        "calendar",
        "cgi",
        "cmd",
        "code",
        "codecs",
        "collections",
        "colorsys",
        "compileall",
        "concurrent",
        "configparser",
        "contextlib",
        "copy",
        "copyreg",
        "cProfile",
        "csv",
        "ctypes",
        "curses",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "dis",
        "distutils",
        "doctest",
        "email",
        "encodings",
        "enum",
        "errno",
        "faulthandler",
        "fcntl",
        "filecmp",
        "fileinput",
        "fnmatch",
        "fractions",
        "ftplib",
        "functools",
        "gc",
        "getopt",
        "getpass",
        "gettext",
        "glob",
        "grp",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "idlelib",
        "imaplib",
        "importlib",
        "inspect",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "keyword",
        "lib2to3",
        "linecache",
        "locale",
        "logging",
        "lzma",
        "mailbox",
        "mailcap",
        "marshal",
        "math",
        "mimetypes",
        "mmap",
        "modulefinder",
        "multiprocessing",
        "netrc",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "pdb",
        "pickle",
        "pickletools",
        "pipes",
        "pkgutil",
        "platform",
        "plistlib",
        "poplib",
        "posixpath",
        "pprint",
        "profile",
        "pstats",
        "pty",
        "pwd",
        "py_compile",
        "pyclbr",
        "pydoc",
        "queue",
        "quopri",
        "random",
        "re",
        "readline",
        "reprlib",
        "resource",
        "rlcompleter",
        "runpy",
        "sched",
        "secrets",
        "select",
        "selectors",
        "shelve",
        "shlex",
        "shutil",
        "signal",
        "site",
        "smtpd",
        "smtplib",
        "sndhdr",
        "socket",
        "socketserver",
        "sqlite3",
        "ssl",
        "stat",
        "statistics",
        "string",
        "stringprep",
        "struct",
        "subprocess",
        "sunau",
        "symtable",
        "sys",
        "sysconfig",
        "syslog",
        "tabnanny",
        "tarfile",
        "telnetlib",
        "tempfile",
        "termios",
        "test",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "tkinter",
        "token",
        "tokenize",
        "trace",
        "traceback",
        "tracemalloc",
        "tty",
        "turtle",
        "turtledemo",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uu",
        "uuid",
        "venv",
        "warnings",
        "wave",
        "weakref",
        "webbrowser",
        "winreg",
        "winsound",
        "wsgiref",
        "xdrlib",
        "xml",
        "xmlrpc",
        "zipapp",
        "zipfile",
        "zipimport",
        "zlib",
        "_thread",
        "__future__",
    ]
)


def _is_stdlib(module_name: str) -> bool:
    """Check if a module name is part of the Python stdlib."""
    top = module_name.split(".")[0]
    return top in _STDLIB_PREFIXES


def _is_domain_import(module_name: str) -> bool:
    """Check if import is from our domain layer."""
    return module_name.startswith("aiyes.domain")


def _is_port_import(module_name: str) -> bool:
    """Check if import is from our ports layer."""
    return module_name.startswith("aiyes.ports")


def _is_adapter_import(module_name: str) -> bool:
    """Check if import is from our adapters layer."""
    return module_name.startswith("aiyes.adapters")


def _is_cli_import(module_name: str) -> bool:
    """Check if import is from our CLI layer."""
    return module_name.startswith("aiyes.cli")


def _is_internal_import(module_name: str) -> bool:
    """Check if import is from our project."""
    return module_name.startswith("aiyes")


def _is_entity_file(filepath: Path) -> bool:
    """Check if a file is a domain entity/VO (not a use case)."""
    return "use_cases" not in str(filepath)


def _is_use_case_file(filepath: Path) -> bool:
    """Check if a file is a domain use case."""
    return "use_cases" in str(filepath)


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-05 / FC-ARCH-08: Domain entity purity (stdlib only)
# ──────────────────────────────────────────────────────────────────────


class TestDomainEntityPurity:
    """Domain entities and value objects must only import stdlib and other domain entities."""

    def test_entity_files_import_only_stdlib_and_domain(self) -> None:
        """R-ARCH-05, FC-ARCH-08: Entity/VO files import only stdlib + domain entities."""
        entity_files = [
            f
            for f in _get_python_files(DOMAIN_DIR)
            if _is_entity_file(f) and f.name != "__init__.py"
        ]

        violations = []
        for filepath in entity_files:
            imports = _get_imports_from_file(filepath)
            for imp in imports:
                if (
                    not _is_stdlib(imp)
                    and not _is_domain_import(imp)
                    and _is_internal_import(imp)
                ):
                    # Domain entity importing port, adapter, or CLI
                    if (
                        _is_port_import(imp)
                        or _is_adapter_import(imp)
                        or _is_cli_import(imp)
                    ):
                        violations.append(
                            f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                        )

        assert violations == [], "Domain entity purity violations:\n" + "\n".join(
            violations
        )

    def test_entity_files_no_port_imports(self) -> None:
        """R-ARCH-05: Entity/VO files must NOT import from ports."""
        entity_files = [
            f
            for f in _get_python_files(DOMAIN_DIR)
            if _is_entity_file(f) and f.name != "__init__.py"
        ]

        violations = []
        for filepath in entity_files:
            imports = _get_imports_from_file(filepath)
            for imp in imports:
                if _is_port_import(imp):
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                    )

        assert violations == [], "Entity port import violations:\n" + "\n".join(
            violations
        )


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-05 / FC-ARCH-08 rewritten: Use case imports
# ──────────────────────────────────────────────────────────────────────


class TestUseCaseImports:
    """Use cases may import stdlib + domain + port Protocols."""

    def test_use_case_files_no_adapter_imports(self) -> None:
        """R-ARCH-05: Use case files must NOT import adapters."""
        uc_files = [
            f
            for f in _get_python_files(DOMAIN_DIR)
            if _is_use_case_file(f) and f.name != "__init__.py"
        ]

        violations = []
        for filepath in uc_files:
            imports = _get_imports_from_file(filepath)
            for imp in imports:
                if _is_adapter_import(imp):
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                    )

        assert violations == [], "Use case adapter import violations:\n" + "\n".join(
            violations
        )

    def test_use_case_files_no_cli_imports(self) -> None:
        """R-ARCH-05: Use case files must NOT import CLI layer."""
        uc_files = [
            f
            for f in _get_python_files(DOMAIN_DIR)
            if _is_use_case_file(f) and f.name != "__init__.py"
        ]

        violations = []
        for filepath in uc_files:
            imports = _get_imports_from_file(filepath)
            for imp in imports:
                if _is_cli_import(imp):
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                    )

        assert violations == [], "Use case CLI import violations:\n" + "\n".join(
            violations
        )


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-05 / FC-ARCH-10: One Protocol per port file
# ──────────────────────────────────────────────────────────────────────


class TestPortStructure:
    """Ports are Protocol classes, one per file."""

    def test_ports_use_protocol_not_abc(self) -> None:
        """R-ARCH-05: Port files define Protocol classes, not ABC."""
        port_files = [
            f for f in _get_python_files(PORTS_DIR) if f.name != "__init__.py"
        ]

        for filepath in port_files:
            source = filepath.read_text()
            if not source.strip():
                continue
            tree = ast.parse(source)

            classes = [
                node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            ]
            for cls in classes:
                # Check bases for Protocol
                base_names = []
                for base in cls.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        base_names.append(base.attr)

                assert "ABC" not in base_names, (
                    f"{filepath.name}: class {cls.name} inherits from ABC, should use Protocol"
                )

    def test_one_protocol_per_file(self) -> None:
        """R-ARCH-05, FC-ARCH-10: Each port file defines at most one Protocol class."""
        port_files = [
            f for f in _get_python_files(PORTS_DIR) if f.name != "__init__.py"
        ]

        for filepath in port_files:
            source = filepath.read_text()
            if not source.strip():
                continue
            tree = ast.parse(source)

            protocol_classes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if base_name == "Protocol":
                            protocol_classes.append(node.name)

            assert len(protocol_classes) <= 1, (
                f"{filepath.name}: defines {len(protocol_classes)} Protocols "
                f"({protocol_classes}), expected at most 1"
            )

    def test_no_duplicate_protocol_names(self) -> None:
        """R-ARCH-05, FC-ARCH-10: No two port files define Protocol with same name."""
        port_files = [
            f for f in _get_python_files(PORTS_DIR) if f.name != "__init__.py"
        ]

        seen: dict = {}
        for filepath in port_files:
            source = filepath.read_text()
            if not source.strip():
                continue
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if base_name == "Protocol":
                            if node.name in seen:
                                pytest.fail(
                                    f"Duplicate Protocol name '{node.name}' in "
                                    f"{filepath.name} and {seen[node.name]}"
                                )
                            seen[node.name] = filepath.name


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-05 / FC-ARCH-12, FC-ARCH-13: Adapter boundaries
# ──────────────────────────────────────────────────────────────────────


class TestAdapterBoundaries:
    """Adapters must not import CLI or other adapters."""

    def test_adapters_no_cli_imports(self) -> None:
        """R-ARCH-05, FC-ARCH-13: Adapters must not import CLI layer.

        Exemption (AC-10): mcp_server.py is allowed to access CLI modules
        (schema_gen, presenter, main) because it must generate tool schemas
        and format output. It uses importlib.import_module at runtime to
        keep AST-level imports clean, but the exemption is recorded here
        so that any future refactor to direct imports does not silently break.
        """
        # AC-10: mcp_server.py is an approved exception to adapter-no-CLI-imports.
        _MCP_SERVER_EXEMPTION = {"mcp_server.py"}

        adapter_files = [
            f for f in _get_python_files(ADAPTERS_DIR) if f.name != "__init__.py"
        ]

        violations = []
        for filepath in adapter_files:
            if filepath.name in _MCP_SERVER_EXEMPTION:
                continue
            imports = _get_imports_from_file(filepath)
            for imp in imports:
                if _is_cli_import(imp):
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                    )

        assert violations == [], "Adapter CLI import violations:\n" + "\n".join(
            violations
        )

    def test_adapters_no_cross_adapter_imports(self) -> None:
        """R-ARCH-05, FC-ARCH-13: Adapters must not import other adapters."""
        # Shared utility modules within adapters layer that any adapter may import
        _SHARED_ADAPTER_UTILS = {
            "aiyes.adapters.adb_path",
            "aiyes.adapters.adb_text",
            "aiyes.adapters.atspi_subprocess_worker",
            "aiyes.adapters.atspi_worker_connection",
        }

        adapter_files = [
            f for f in _get_python_files(ADAPTERS_DIR) if f.name != "__init__.py"
        ]

        violations = []
        for filepath in adapter_files:
            imports = _get_imports_from_file(filepath)
            adapter_module_name = f"aiyes.adapters.{filepath.stem}"
            for imp in imports:
                if _is_adapter_import(imp) and imp != adapter_module_name:
                    # Importing a different adapter module
                    if imp != "aiyes.adapters":  # __init__ import is OK
                        if imp in _SHARED_ADAPTER_UTILS:
                            continue  # shared utility, not a cross-adapter dep
                        violations.append(
                            f"{filepath.relative_to(PROJECT_ROOT)}: imports {imp}"
                        )

        assert violations == [], "Cross-adapter import violations:\n" + "\n".join(
            violations
        )


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-06: Source layout
# ──────────────────────────────────────────────────────────────────────


class TestSourceLayout:
    """Project source follows prescribed layout."""

    def test_source_under_src_aiyes(self) -> None:
        """R-ARCH-06: All production source under src/aiyes/."""
        assert SRC_ROOT.exists(), "src/aiyes/ directory does not exist"

    def test_tests_under_tests(self) -> None:
        """R-ARCH-06: All test files under tests/."""
        tests_dir = PROJECT_ROOT / "tests"
        assert tests_dir.exists(), "tests/ directory does not exist"

    def test_domain_directory_exists(self) -> None:
        """R-ARCH-05: Domain directory exists at src/aiyes/domain/."""
        assert DOMAIN_DIR.exists(), "src/aiyes/domain/ directory does not exist"

    def test_ports_directory_exists(self) -> None:
        """R-ARCH-05: Ports directory exists at src/aiyes/ports/."""
        assert PORTS_DIR.exists(), "src/aiyes/ports/ directory does not exist"

    def test_adapters_directory_exists(self) -> None:
        """R-ARCH-05: Adapters directory exists at src/aiyes/adapters/."""
        assert ADAPTERS_DIR.exists(), "src/aiyes/adapters/ directory does not exist"
