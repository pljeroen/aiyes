"""Shared adb binary path resolver for all Android adapters.

Resolution order:
1. shutil.which("adb") -- check PATH first
2. ~/android-sdk/platform-tools/adb -- common manual install
3. ~/Android/Sdk/platform-tools/adb -- Android Studio default
4. $ANDROID_HOME/platform-tools/adb -- Android SDK environment root
5. $ANDROID_SDK_ROOT/platform-tools/adb -- Android SDK environment root
6. /usr/local/bin/adb -- system install

Returns the first executable path that exists. Raises RuntimeError with helpful
message listing checked locations if none found.

Uses only stdlib: shutil, os.
"""

from __future__ import annotations

import os
import shutil


def _fallback_locations() -> list[str]:
    locations = [
        os.path.expanduser("~/android-sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    ]
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk_root = os.environ.get(env_var)
        if sdk_root:
            locations.append(os.path.join(sdk_root, "platform-tools", "adb"))

    locations.append("/usr/local/bin/adb")
    return list(dict.fromkeys(locations))


def resolve_adb_path() -> str:
    """Resolve the adb binary path.

    Returns the first valid path found. Raises RuntimeError if none found.
    """
    # 1. Check PATH via shutil.which
    which_result = shutil.which("adb")
    if which_result is not None:
        return which_result

    fallback_locations = _fallback_locations()

    for path in fallback_locations:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    checked = "\n".join(f"  - {path}" for path in fallback_locations)
    raise RuntimeError(
        "adb not found. Checked:\n"
        "  - PATH (shutil.which)\n"
        f"{checked}\n"
        "Install Android SDK platform-tools and ensure adb is accessible."
    )
