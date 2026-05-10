#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

missing_tool() {
  printf 'missing required release tool: %s\n' "$1" >&2
  printf 'install maintainer tooling with: python -m pip install -e ".[dev]"\n' >&2
  exit 127
}

command -v python >/dev/null 2>&1 || missing_tool python

rm -rf dist build src/aiyes.egg-info

python -m ruff check src tests
python -m mypy src/aiyes
python -m pytest -q
python -m build
python -m twine check dist/*

AUDIT_VENV="$(mktemp -d)"
trap 'rm -rf "$AUDIT_VENV"' EXIT

python -m venv "$AUDIT_VENV"
"$AUDIT_VENV/bin/python" -m pip install --upgrade pip
"$AUDIT_VENV/bin/python" -m pip install pip-audit cyclonedx-bom 'dist/'*.whl'[mcp]'
"$AUDIT_VENV/bin/pip-audit" --strict
"$AUDIT_VENV/bin/cyclonedx-py" environment \
  --output-format JSON \
  --output-file dist/aiyes-sbom.cdx.json

printf 'release gate passed; artifacts are in dist/\n'
