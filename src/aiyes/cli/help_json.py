"""help-json CLI command — machine-readable command listing.

Session-less command that outputs JSON describing all CLI commands
and their parameter schemas. Uses schema_gen as single source of truth.

The actual Click command is registered in main.py. This module provides
the build_help_json function.
"""

from __future__ import annotations

from typing import Any, Dict

import click

from aiyes.cli.schema_gen import enumerate_commands


def build_help_json(cli_group: click.Group) -> Dict[str, Any]:
    """Build the help-json output dict from the CLI group."""
    from aiyes import __version__

    commands = enumerate_commands(cli_group)
    return {
        "name": "aieyes",
        "version": __version__,
        "commands": [
            {
                "name": ci.cli_name,
                "tool_name": ci.tool_name,
                "description": ci.description,
                "parameters": ci.json_schema,
            }
            for ci in commands
        ],
    }
