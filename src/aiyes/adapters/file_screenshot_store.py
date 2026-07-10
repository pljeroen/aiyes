"""FileScreenshotStore — implements ScreenshotStorePort.

Screenshots are stored as ~/.aieyes/<session-id>/screenshot.png.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path
from typing import Optional, Tuple

from aiyes.domain.session import validate_session_id

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"

# The PNG spec caps each dimension at 2**31 - 1 (a signed-int-safe uint32), and
# both PNG and JPEG require a real image to be at least 1x1. A field VALUE
# outside 1..2**31-1 is not a dimension any real image can carry — 0 is illegal
# (and a divide-by-zero for a downstream coordinate rescaler); anything above
# 2**31-1 (including the top-bit-set 2**31 that the PNG spec prohibits) is a
# fabrication. Structurally valid headers can still encode such values, so this
# semantic floor is applied at BOTH return points before dims are trusted.
_MAX_DIM = 2**31 - 1


def _valid_dims(width: int, height: int) -> bool:
    """True only if (width, height) is a dimension a real image can carry.

    A value is valid ONLY if 1 <= value <= 2**31 - 1 on BOTH axes. Anything
    else — a zero axis, or a value above the PNG maximum (incl. exactly 2**31,
    the top-bit-set case the PNG spec prohibits) — is out of range, so the
    caller degrades to None (never lie) instead of returning the raw field.
    """
    return 1 <= width <= _MAX_DIM and 1 <= height <= _MAX_DIM


def _parse_jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """Walk JPEG marker segments to the first SOFn; return (width, height).

    Returns None on a truncated/unwalkable stream. SOFn markers are
    0xFFC0..0xFFCF EXCLUDING 0xC4 (DHT), 0xC8 (JPG) and 0xCC (DAC); the SOF
    segment is [marker(2) | length(2) | precision(1) | height(2) | width(2)].
    """
    offset = 2  # skip the SOI marker
    length = len(data)
    while offset + 1 < length:
        if data[offset] != 0xFF:
            return None
        marker = data[offset + 1]
        # Skip 0xFF fill/padding bytes before a marker code.
        if marker == 0xFF:
            offset += 1
            continue
        # Standalone markers carry no length: SOI, EOI, RSTn, TEM.
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        # Every other marker is followed by a 2-byte segment length.
        if offset + 4 > length:
            return None
        (seg_length,) = struct.unpack(">H", data[offset + 2 : offset + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            # The declared SOF segment must be large enough to CONTAIN the
            # dimension fields: precision(1) + height(2) + width(2) sit at
            # data[offset+5:offset+9], i.e. the declared segment
            # [offset+2 : offset+2+seg_length) must reach offset+9, so
            # seg_length must be at least 7. A shorter declared length cannot
            # hold the fields — reading the fixed offset would grab bytes the
            # segment never claimed (the next segment's), fabricating dims — so
            # degrade to None instead.
            if seg_length < 7:
                return None
            # The declared SOF segment must also fully fit in the buffer: a
            # length field that overruns EOF marks a truncated/corrupt stream,
            # so degrade to None instead of fabricating dims from short bytes.
            if offset + 2 + seg_length > length:
                return None
            if offset + 9 > length:
                return None
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            # Semantic dimension-value floor: a structurally valid SOF can still
            # declare width/height 0, which is not a real image — degrade rather
            # than lie. (JPEG uint16 fields are naturally <= 65535, so only the
            # lower bound bites here.)
            if not _valid_dims(width, height):
                return None
            return (width, height)
        offset += 2 + seg_length
    return None


class FileScreenshotStore:
    """File-based screenshot persistence."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir)

    def _safe_session_dir(self, session_id: str) -> Path:
        """Validate session_id and return the session directory path.

        Raises ValueError if the session_id contains path traversal characters.
        """
        validate_session_id(session_id)
        return self._base_dir / session_id

    def save_screenshot(self, session_id: str, source_path: str) -> str:
        """Copy screenshot to session directory. Returns destination path."""
        session_dir = self._safe_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(session_dir), 0o700)

        dest = session_dir / "screenshot.png"
        shutil.copy2(source_path, str(dest))
        os.chmod(str(dest), 0o600)
        return str(dest)

    def get_screenshot_path(self, session_id: str) -> str:
        """Get the screenshot path for a session."""
        return str(self._safe_session_dir(session_id) / "screenshot.png")

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        """Read the saved screenshot file as raw bytes."""
        path = self._safe_session_dir(session_id) / "screenshot.png"
        return path.read_bytes()

    def read_dimensions(self, path: str) -> Optional[Tuple[int, int]]:
        """Read the image's (width, height) from its encoded bytes.

        Format is content-sniffed from the file's magic bytes (NOT its
        filename extension): PNG signature + IHDR, or JPEG SOI + SOFn walk.
        Returns None for anything else — unrecognized magic, a
        truncated/corrupt header, or a missing file. Never lies (never
        returns a fabricated dimension) and never raises for expected
        degrade. Stdlib only; no imaging library.
        """
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return None

        # PNG: 8-byte signature, then the IHDR chunk carries width/height as
        # two big-endian uint32 at byte offset 16:24.
        if data[:8] == _PNG_SIGNATURE:
            if len(data) < 24:
                return None
            # Validate the first chunk is a real IHDR before trusting the
            # fixed dims offset: its chunk-length field (data[8:12]) must be
            # 13 AND its chunk-type (data[12:16]) must be b"IHDR". A corrupt
            # header degrades to None instead of fabricating dims.
            (ihdr_length,) = struct.unpack(">I", data[8:12])
            if ihdr_length != 13 or data[12:16] != b"IHDR":
                return None
            width, height = struct.unpack(">II", data[16:24])
            # Semantic dimension-value floor: a structurally valid IHDR can still
            # declare a zero axis or a value above the PNG maximum (2**31 - 1,
            # incl. the prohibited top-bit-set 2**31) — degrade rather than lie.
            if not _valid_dims(width, height):
                return None
            return (width, height)

        # JPEG: SOI marker, then walk marker segments to the first SOFn.
        if data[:2] == _JPEG_SOI:
            return _parse_jpeg_dimensions(data)

        return None

    def delete_temp(self, path: str) -> None:
        """Delete a temporary screenshot file."""
        os.remove(path)
