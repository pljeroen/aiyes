"""Shared text escaping for adb shell input commands.

Used by android_action_adapter and android_input_adapter.
Uses only stdlib.

S-04 security audit: handles all shell metacharacters plus whitespace
control characters (tab, newline, carriage return), null bytes, and
the percent sign (which conflicts with adb's %s space encoding).
"""

from __future__ import annotations

from typing import List


def escape_text_for_adb(text: str) -> str:
    """Escape text for safe passage through adb shell.

    Characters that have special meaning in shell are escaped with backslash.
    Spaces are replaced with %s for ``adb shell input text``.
    Control characters (tab, newline, carriage return) are escaped.
    Null bytes are stripped (cannot be typed via adb input text).
    Percent is escaped (raw % conflicts with %s space encoding).
    """
    result: List[str] = []
    for char in text:
        if char == "\x00":
            # Null byte: strip (cannot be represented in adb input text)
            continue
        if char == " ":
            result.append("%s")
        elif char == "\t":
            # Tab: adb shell input text does not natively support tabs;
            # escape as backslash-t which adb passes through
            result.append("\\t")
        elif char == "\n":
            # Newline: escape for shell safety
            result.append("\\n")
        elif char == "\r":
            # Carriage return: escape for shell safety
            result.append("\\r")
        elif char == "%":
            # Percent: must be escaped to avoid conflict with %s space encoding
            result.append("\\%")
        elif char in (
            "'",
            '"',
            "\\",
            "&",
            "|",
            ";",
            "(",
            ")",
            "<",
            ">",
            "`",
            "$",
            "!",
            "~",
            "{",
            "}",
            "[",
            "]",
            "#",
            "*",
            "?",
        ):
            result.append("\\" + char)
        else:
            result.append(char)
    return "".join(result)
