"""Domain-layer environment availability check.

Pure predicate function using only stdlib (shutil.which).
Returns True IFF all three binary classes are present on PATH:
  1. Xvfb
  2. xdotool
  3. scrot OR import (ImageMagick)
"""

from __future__ import annotations

import shutil


def available() -> bool:
    """Check whether the GUI runtime prerequisites are available on PATH.

    Returns True if and only if Xvfb, xdotool, and at least one screenshot
    tool (scrot or import) are found via shutil.which().

    This is a pure predicate: no side effects, no process spawning,
    no state mutation. Idempotent for a given PATH state.
    """
    has_xvfb = shutil.which("Xvfb") is not None
    has_xdotool = shutil.which("xdotool") is not None
    has_screenshot = (
        shutil.which("scrot") is not None or shutil.which("import") is not None
    )
    return bool(has_xvfb and has_xdotool and has_screenshot)
