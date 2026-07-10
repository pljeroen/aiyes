"""AIYES-111 — screenshot result carries the captured image width/height.

RED tests (authored before implementation). They pin the bound design:

  * ScreenshotStorePort.read_dimensions(path) -> Optional[Tuple[int, int]]
    (None = unavailable, never raises for expected degrade).
  * ScreenshotResult gains width/height: Optional[int] = None, read from the
    port against the EXACT returned file (post-crop) in each execute() branch.
  * format_screenshot OMITS width/height keys when None (never null / never 0).
  * FileScreenshotStore.read_dimensions content-sniffs magic bytes: PNG (sig +
    IHDR) and JPEG (SOI + SOFn walk), stdlib only; anything else -> None.

Fixtures are minimal but standards-conformant image HEADERS built with stdlib
struct — the parser only needs the header, not valid pixel data.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Tuple

import pytest

from aiyes.adapters.file_screenshot_store import FileScreenshotStore
from aiyes.cli.presenter import format_screenshot
from aiyes.domain.use_cases.screenshot import ScreenshotResult, ScreenshotUseCase

from tests.conftest import FakeScreenshot, FakeScreenshotStore
from tests.test_aiyes48_screenshot_mcp import (
    SpySessionRepo,
    StubTreeStore,
    _make_session,
    _make_use_case,
)


# ──────────────────────────────────────────────────────────────────────
# Stdlib fixture builders — header-only PNG / JPEG encoding known dims.
# ──────────────────────────────────────────────────────────────────────


def _png_bytes(width: int, height: int) -> bytes:
    """A well-formed PNG signature + IHDR chunk encoding (width, height).

    Layout the parser reads: bytes[0:8] == signature; struct.unpack(">II",
    bytes[16:24]) == (width, height).
    """
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_length = struct.pack(">I", 13)  # IHDR data is 13 bytes
    ihdr_type = b"IHDR"
    dims = struct.pack(">II", width, height)  # offsets 16:20 (w), 20:24 (h)
    # bit depth, color type, compression, filter, interlace + a placeholder CRC.
    ihdr_tail = bytes([8, 2, 0, 0, 0]) + b"\x00\x00\x00\x00"
    return signature + ihdr_length + ihdr_type + dims + ihdr_tail


def _jpeg_bytes(width: int, height: int) -> bytes:
    """SOI + APP0 + SOF0 encoding (width, height).

    A preceding APP0 segment forces the parser to walk marker lengths before
    reaching the Start-Of-Frame. SOF0 segment layout:
    [marker(2) | length(2) | precision(1) | height(2) | width(2) | components].
    """
    soi = b"\xff\xd8"
    app0_payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", 2 + len(app0_payload)) + app0_payload
    sof_payload = (
        bytes([8])  # sample precision
        + struct.pack(">H", height)  # height (big-endian uint16)
        + struct.pack(">H", width)  # width (big-endian uint16)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"  # 3 component descriptors
    )
    sof0 = b"\xff\xc0" + struct.pack(">H", 2 + len(sof_payload)) + sof_payload
    return soi + app0 + sof0


def _png_bytes_with_invalid_ihdr() -> bytes:
    """PNG signature followed by a chunk that is NOT a valid IHDR.

    The 8-byte signature is intact (so the PNG branch is entered), but the
    chunk following it is corrupt: the chunk-length field is wrong (999, not
    13) AND the chunk-type is not b"IHDR" (it is b"gAMA"). The blob is a full
    24 bytes, so the current parser still reaches
    struct.unpack(">II", data[16:24]) at the fixed IHDR-dims offset and
    fabricates dims from whatever bytes sit there (b"garbage!" ->
    (1734439522, 1634166049)). A correct parser validates the chunk
    length/type BEFORE trusting that offset and degrades to None.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    wrong_length = struct.pack(">I", 999)  # a real IHDR length field is 13
    wrong_type = b"gAMA"  # not b"IHDR"
    dims_offset_bytes = b"garbage!"  # the 8 bytes the buggy parser unpacks
    return signature + wrong_length + wrong_type + dims_offset_bytes


def _jpeg_bytes_with_sof_length_past_eof() -> bytes:
    """JPEG SOI + SOFn whose declared segment length runs past end-of-file.

    SOI + SOF0 marker + a 2-byte length field claiming a 255-byte segment,
    but the file is truncated immediately after the height/width fields (11
    bytes total). The declared segment does NOT fit in the file. The current
    parser reads height/width at the fixed SOF offset without checking that
    the declared segment fits, so it fabricates (640, 480). A correct parser
    rejects a segment length that overruns the buffer and degrades to None.
    """
    soi = b"\xff\xd8"
    sof_marker = b"\xff\xc0"  # SOF0
    seg_length = struct.pack(">H", 255)  # claims 255 bytes; file is only 11
    precision = bytes([8])
    height = struct.pack(">H", 480)
    width = struct.pack(">H", 640)
    return soi + sof_marker + seg_length + precision + height + width


def _jpeg_bytes_with_sof_length_too_short() -> bytes:
    """JPEG SOI + SOFn whose DECLARED segment length is too short to contain
    the precision+height+width fields, followed by MORE bytes.

    Distinct from ``_jpeg_bytes_with_sof_length_past_eof`` (rev-1): there the
    declared segment overran EOF. Here the declared segment length is 4 — it
    claims only 2 payload bytes (precision + one spare) and it FITS entirely
    inside the file, so the rev-1 overrun guard is satisfied. But a SOFn needs
    a declared length of at least 7 (2 length + 1 precision + 2 height + 2
    width) to actually CONTAIN the dimension fields. With a declared length of
    4 the fixed-offset height/width read (data[offset+5:offset+9]) lands
    OUTSIDE the declared segment, on the trailing bytes 0xAD/0xBE/0xEF, so the
    current parser fabricates (0xBEEF, 0xDEAD) == (48879, 57005). The correct
    never-lie result is None: a segment that declares itself too short to hold
    the fields has no dimensions to report.
    """
    soi = b"\xff\xd8"
    sof_marker = b"\xff\xc0"  # SOF0
    seg_length = struct.pack(">H", 4)  # declares 2 payload bytes: TOO SHORT
    declared_payload = bytes([8, 0xDE])  # precision + 1 byte inside the segment
    # These sit OUTSIDE the declared 4-byte segment; the buggy fixed read grabs
    # them as height/width (0xDEAD, 0xBEEF) instead of degrading to None.
    trailing = bytes([0xAD, 0xBE, 0xEF, 0x00, 0x00, 0x00])
    return soi + sof_marker + seg_length + declared_payload + trailing


def _write(tmp_path: Path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _full_capture_use_case(
    dimensions,
) -> Tuple[ScreenshotUseCase, FakeScreenshotStore]:
    """A ScreenshotUseCase whose store reports the given dims for any path."""
    store = FakeScreenshotStore(dimensions=dimensions)
    uc = ScreenshotUseCase(
        screenshot=FakeScreenshot(),
        session_repo=SpySessionRepo(_make_session()),
        screenshot_store=store,
    )
    return uc, store


def _assert_dims_none_end_to_end(store: FileScreenshotStore, raw: str) -> None:
    """Shared never-lie assertion for a file whose header STRUCTURE is valid but
    whose declared dimension VALUES are out of the plausible range.

    Asserts the full C3 floor: (1) the adapter returns None (not the fabricated
    dim), (2) end-to-end through the REAL ScreenshotUseCase.execute + REAL
    FileScreenshotStore the result carries width/height None, and (3) the
    presenter OMITS both keys (never null, never 0) while "path" stays present.
    Mirrors the three-part end-to-end check used by the corrupt-header C3 tests.
    """
    # (1) The adapter itself must never lie — None, not an out-of-range dim.
    assert store.read_dimensions(raw) is None

    # (2) End-to-end through the REAL use case + REAL adapter: dims None.
    uc = ScreenshotUseCase(
        screenshot=FakeScreenshot(path=raw),
        session_repo=SpySessionRepo(_make_session()),
        screenshot_store=store,
    )
    result = uc.execute(session_id="test-s")
    assert result.width is None
    assert result.height is None

    # (3) The presenter OMITS the keys entirely (never null, never 0).
    payload = json.loads(
        format_screenshot(path=result.path, width=result.width, height=result.height)
    )
    assert "width" not in payload
    assert "height" not in payload
    assert payload["path"] == result.path


# ──────────────────────────────────────────────────────────────────────
# R1 — width/height present end-to-end (domain result + presenter dict).
# ──────────────────────────────────────────────────────────────────────


class TestDimensionsPresent:
    def test_full_capture_result_carries_known_dims(self) -> None:
        # RED: ScreenshotResult has no width/height and execute() does not yet
        # read them from the port, so `result.width` raises AttributeError.
        uc, _ = _full_capture_use_case((1280, 800))

        result = uc.execute(session_id="test-s")

        assert result.width == 1280
        assert result.height == 800

    def test_presenter_dict_includes_dims_for_present_capture(self) -> None:
        # RED: format_screenshot has no width/height parameters yet.
        uc, _ = _full_capture_use_case((1280, 800))

        result = uc.execute(session_id="test-s")
        payload = json.loads(
            format_screenshot(
                path=result.path,
                width=result.width,
                height=result.height,
            )
        )

        assert payload["width"] == 1280
        assert payload["height"] == 800
        assert payload["path"] == result.path


# ──────────────────────────────────────────────────────────────────────
# R2 — dims reflect the POST-crop file, not the pre-crop capture (incl. C1a).
# ──────────────────────────────────────────────────────────────────────


class TestDimensionsPostCrop:
    def test_region_crop_reports_cropped_dims_not_precrop(self) -> None:
        # RED: `result.width` raises AttributeError (no field / no port read).
        # The store reports dims of the CURRENT bytes at the returned path; the
        # crop rewrites those bytes, so a post-crop read yields (30, 40), while
        # a pre-crop read would (wrongly) yield (100, 200).
        uc, _, store, crop = _make_use_case()
        store.dims_by_bytes[store.initial_bytes] = (100, 200)  # pre-crop
        store.dims_by_bytes[crop.cropped_bytes] = (30, 40)  # post-crop

        result = uc.execute(session_id="test-s", region=(1, 2, 30, 40))

        assert (result.width, result.height) == (30, 40)
        assert (result.width, result.height) != (100, 200)

    def test_node_crop_reports_cropped_dims_not_precrop(self) -> None:
        # node_id resolves to a region (StubTreeStore bounds (7,8,30,40)) and
        # crops; dims must reflect the post-crop file, not the pre-crop capture.
        # RED: `result.width` raises AttributeError.
        uc, _, store, crop = _make_use_case(tree_store=StubTreeStore())
        store.dims_by_bytes[store.initial_bytes] = (100, 200)  # pre-crop
        store.dims_by_bytes[crop.cropped_bytes] = (30, 40)  # post-crop

        result = uc.execute(session_id="test-s", node_id="n_002")

        assert (result.width, result.height) == (30, 40)
        assert (result.width, result.height) != (100, 200)

    def test_base64_branch_dims_tied_to_encoded_file(self) -> None:
        # C1a: base64 returns path=None, so dims must be bound to the exact file
        # whose bytes were encoded (the post-crop store file).
        # RED: `result.width` raises AttributeError.
        uc, _, store, crop = _make_use_case()
        store.dims_by_bytes[store.initial_bytes] = (100, 200)
        store.dims_by_bytes[crop.cropped_bytes] = (30, 40)

        result = uc.execute(session_id="test-s", base64=True, region=(1, 2, 30, 40))

        assert result.path is None
        assert (result.width, result.height) == (30, 40)


# ──────────────────────────────────────────────────────────────────────
# R3 — additive / back-compat: {path,data} untouched, dims are ADDED keys.
# ──────────────────────────────────────────────────────────────────────


class TestAdditiveBackCompat:
    def test_existing_path_data_consumer_unchanged(self) -> None:
        # GREEN guard (must stay green after implementation): a caller passing
        # only path/data still gets exactly {path, data} — no width/height keys.
        payload = json.loads(format_screenshot(path="/x/shot.png", data="ZGF0YQ=="))

        assert payload == {"path": "/x/shot.png", "data": "ZGF0YQ=="}
        assert "width" not in payload
        assert "height" not in payload

    def test_dims_are_additive_keys_not_replacing_existing(self) -> None:
        # RED: format_screenshot has no width/height parameters yet (TypeError).
        payload = json.loads(
            format_screenshot(path="/x/shot.png", width=800, height=600)
        )

        # Existing key present with unchanged value AND type.
        assert payload["path"] == "/x/shot.png"
        assert isinstance(payload["path"], str)
        # New keys ADDED alongside it.
        assert payload["width"] == 800
        assert payload["height"] == 600

    def test_screenshot_result_accepts_optional_dims_defaulting_none(self) -> None:
        # RED: ScreenshotResult has no width/height fields yet (TypeError on the
        # keyword construction; AttributeError on the default-None read).
        assert ScreenshotResult(path="/x/shot.png").width is None
        assert ScreenshotResult(path="/x/shot.png").height is None

        enriched = ScreenshotResult(path="/x/shot.png", width=800, height=600)
        assert enriched.width == 800
        assert enriched.height == 600
        assert enriched.path == "/x/shot.png"  # existing field intact


# ──────────────────────────────────────────────────────────────────────
# C3 — never-lie degrade: unavailable dims -> None -> keys OMITTED.
# ──────────────────────────────────────────────────────────────────────


class TestNeverLieDegrade:
    def test_unreadable_file_yields_none_dims_and_omitted_keys(self) -> None:
        # RED: `result.width` raises AttributeError. Store reports None (dims
        # unavailable); the result must carry None and the presenter must OMIT
        # the keys entirely — never null, never 0.
        uc, _ = _full_capture_use_case(None)

        result = uc.execute(session_id="test-s")

        assert result.width is None
        assert result.height is None

        payload = json.loads(
            format_screenshot(
                path=result.path, width=result.width, height=result.height
            )
        )
        assert "width" not in payload
        assert "height" not in payload
        assert payload["path"] == result.path  # existing key still present

    def test_presenter_omits_keys_on_none_dims(self) -> None:
        # RED: format_screenshot has no width/height parameters yet (TypeError).
        payload = json.loads(
            format_screenshot(path="/x/shot.png", width=None, height=None)
        )
        assert "width" not in payload
        assert "height" not in payload

    def test_adapter_returns_none_for_garbage_and_truncated(
        self, tmp_path: Path
    ) -> None:
        # RED: FileScreenshotStore has no read_dimensions method yet
        # (AttributeError). None (not a fabricated dim) is the never-lie result.
        store = FileScreenshotStore(base_dir=str(tmp_path))

        garbage = _write(tmp_path, "garbage.png", b"not-an-image-at-all-really")
        truncated_png = _write(tmp_path, "trunc_png.png", _png_bytes(640, 480)[:12])
        truncated_jpeg = _write(tmp_path, "trunc_jpg.png", _jpeg_bytes(640, 480)[:4])

        assert store.read_dimensions(garbage) is None
        assert store.read_dimensions(truncated_png) is None
        assert store.read_dimensions(truncated_jpeg) is None

    def test_png_magic_but_invalid_ihdr_chunk_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): a file with a valid 8-byte PNG signature but a
        # following chunk that is NOT a valid IHDR (wrong chunk-length AND
        # chunk-type != b"IHDR"), long enough that the current parser reaches
        # the data[16:24] unpack. The buggy parser FABRICATES
        # (1734439522, 1634166049) from those offset bytes instead of degrading.
        # MUST return None (unavailable is fine; WRONG is a hard C3 violation),
        # and end-to-end the result dims are None with the presenter OMITTING
        # the keys.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "raw_bad_ihdr.png", _png_bytes_with_invalid_ihdr())

        # (1) The adapter itself must never lie — None, not a fabricated dim.
        assert store.read_dimensions(raw) is None

        # (2) End-to-end through the REAL use case + REAL adapter: dims None.
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(path=raw),
            session_repo=SpySessionRepo(_make_session()),
            screenshot_store=store,
        )
        result = uc.execute(session_id="test-s")
        assert result.width is None
        assert result.height is None

        # (3) The presenter OMITS the keys entirely (never null, never 0).
        payload = json.loads(
            format_screenshot(
                path=result.path, width=result.width, height=result.height
            )
        )
        assert "width" not in payload
        assert "height" not in payload
        assert payload["path"] == result.path

    def test_jpeg_sof_segment_length_past_eof_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): a JPEG (SOI 0xFFD8) whose SOF0 marker declares a
        # segment length (255) larger than the remaining bytes — the file is
        # truncated right after the height/width fields (11 bytes total). The
        # buggy parser reads width/height at the fixed offset WITHOUT checking
        # the declared segment fits, FABRICATING (640, 480) from a truncated
        # stream. MUST degrade to None end-to-end, keys OMITTED.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(
            tmp_path, "raw_bad_sof.jpg", _jpeg_bytes_with_sof_length_past_eof()
        )

        # (1) The adapter itself must never lie — None, not a fabricated dim.
        assert store.read_dimensions(raw) is None

        # (2) End-to-end through the REAL use case + REAL adapter: dims None.
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(path=raw),
            session_repo=SpySessionRepo(_make_session()),
            screenshot_store=store,
        )
        result = uc.execute(session_id="test-s")
        assert result.width is None
        assert result.height is None

        # (3) The presenter OMITS the keys entirely (never null, never 0).
        payload = json.loads(
            format_screenshot(
                path=result.path, width=result.width, height=result.height
            )
        )
        assert "width" not in payload
        assert "height" not in payload
        assert payload["path"] == result.path

    def test_jpeg_sof_declared_length_too_short_yields_none(
        self, tmp_path: Path
    ) -> None:
        # RED (never-lie C3): a JPEG (SOI 0xFFD8) whose SOF0 marker declares a
        # segment length (4) too small to contain precision+height+width. The
        # declared segment FITS in the file (so the rev-1 overrun guard passes),
        # but the fixed height/width read lands OUTSIDE the declared segment, on
        # the trailing bytes. The buggy parser FABRICATES (48879, 57005) from
        # those out-of-segment bytes instead of degrading. A too-short SOF has
        # no dimensions to report, so the never-lie result MUST be None
        # end-to-end, keys OMITTED. Distinct hole from
        # test_jpeg_sof_segment_length_past_eof_yields_none (that one overran
        # EOF; this one fits but under-declares).
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(
            tmp_path, "raw_short_sof.jpg", _jpeg_bytes_with_sof_length_too_short()
        )

        # (1) The adapter itself must never lie — None, not a fabricated dim.
        assert store.read_dimensions(raw) is None

        # (2) End-to-end through the REAL use case + REAL adapter: dims None.
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(path=raw),
            session_repo=SpySessionRepo(_make_session()),
            screenshot_store=store,
        )
        result = uc.execute(session_id="test-s")
        assert result.width is None
        assert result.height is None

        # (3) The presenter OMITS the keys entirely (never null, never 0).
        payload = json.loads(
            format_screenshot(
                path=result.path, width=result.width, height=result.height
            )
        )
        assert "width" not in payload
        assert "height" not in payload
        assert payload["path"] == result.path

    def test_truncation_sweep_never_lies(self, tmp_path: Path) -> None:
        # RED (never-lie C3, CLASS-CLOSING property test — deterministic, no
        # Hypothesis). The never-lie invariant: read_dimensions returns EITHER
        # the exact true dims OR None for EVERY member of a header-degradation
        # family — never a third (fabricated) value, and never raises. One test
        # closes the whole C3 class along two truncation axes:
        #
        #   (a) FILE-level truncation: every prefix length L (0..len) of a VALID
        #       PNG and a VALID JPEG of known dims. Guards the rev-1 SOF-past-EOF
        #       fix; a truncated header must never yield dims different from the
        #       true ones. (Green now — the rev-1 overrun guard already closed
        #       this axis; kept as a regression guard so no future edit reopens
        #       it.)
        #   (b) SEGMENT-level truncation: a JPEG SOF whose DECLARED segment
        #       length is swept while enough real trailing bytes remain that the
        #       fixed read stays in-buffer. A declared length below 7 (2 length +
        #       1 precision + 2 height + 2 width) is too short to CONTAIN the
        #       dimension fields, so the fields fall outside the declared segment
        #       and the never-lie result MUST be None. (RED now — the current
        #       parser fabricates dims for every under-declared length.)
        #
        # Axis (b) is what fails today; axis (a) proves the sweep also covers the
        # already-closed file-truncation hole. After the fix, both axes are green.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        probe = tmp_path / "sweep_probe.bin"

        def _read(data: bytes):
            probe.write_bytes(data)
            return store.read_dimensions(str(probe))

        # (a) FILE-level truncation of a valid PNG and a valid JPEG.
        for label, valid, correct in (
            ("png", _png_bytes(1366, 768), (1366, 768)),
            ("jpeg", _jpeg_bytes(1024, 576), (1024, 576)),
        ):
            for length in range(0, len(valid) + 1):
                result = _read(valid[:length])
                assert result in (None, correct), (
                    f"never-lie violated: {label} prefix L={length} returned "
                    f"{result!r} (allowed: None or {correct})"
                )

        # (b) SEGMENT-level truncation: sweep the SOF declared length. The
        # height/width bytes sit at the fixed read offset (data[7:11]); only the
        # 2-byte declared-length field varies. A declared length >= 7 legitimately
        # contains the fields, so V or None is acceptable; a declared length < 7
        # cannot contain them, so ONLY None is never-lie-consistent (returning V
        # would be reading bytes the segment never claimed).
        true_dims = (4321, 9876)  # (width, height) as the parser returns them
        soi = b"\xff\xd8"
        sof_marker = b"\xff\xc0"  # SOF0
        # precision(1) + height(2) + width(2) — placed so the fixed read at
        # data[7:11] lands exactly on (height, width).
        payload = (
            bytes([8])
            + struct.pack(">H", true_dims[1])  # height
            + struct.pack(">H", true_dims[0])  # width
        )
        trailing = b"\x00" * 16  # keeps the fixed read in-buffer for every length
        saw_short_member = False
        for declared in range(0, len(payload) + 2 + 3):  # 0 .. full length + margin
            blob = soi + sof_marker + struct.pack(">H", declared) + payload + trailing
            result = _read(blob)
            if declared < 7:
                saw_short_member = True
                # Never-lie FLOOR: a segment declaring fewer than 7 bytes cannot
                # hold the dimension fields, so ANY non-None dim is a fabrication.
                assert result is None, (
                    f"never-lie violated: SOF declared_length={declared} "
                    f"(too short to hold height/width) returned {result!r}; "
                    f"MUST be None"
                )
            else:
                # The fields ARE inside the declared segment: the true dims or a
                # conservative None are both acceptable — but never a third value.
                assert result in (None, true_dims), (
                    f"never-lie violated: SOF declared_length={declared} "
                    f"returned {result!r} (allowed: None or {true_dims})"
                )
        assert saw_short_member  # the RED-driving family was actually exercised

    # ── C3-degrade (never-lie), FIX-CYCLE rev-3 : VALID header STRUCTURE but
    #    out-of-range dimension VALUES must degrade to None (never lie). A
    #    returned (width, height) is a real dimension only if
    #    1 <= width <= 2**31 - 1 AND 1 <= height <= 2**31 - 1; anything else — 0
    #    (illegal per the PNG spec and a divide-by-zero for a coordinate
    #    rescaler), or a value exceeding the PNG maximum 2**31 - 1, or exactly
    #    the top-bit-set 2**31 — is a fabrication no real image can carry. This
    #    is a DISTINCT axis from the truncation family above: those fixtures are
    #    structurally corrupt; these are structurally VALID (every existing
    #    IHDR/SOF guard passes) but semantically impossible, so the current
    #    parser returns the raw field VALUE instead of degrading.

    def test_png_zero_width_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): a VALID PNG signature + VALID IHDR chunk
        # (length == 13, type b"IHDR") whose width field is 0. The structure
        # passes every existing guard, so the current parser returns (0, 480) —
        # a lie: 0 is not a legal PNG dimension (width/height are >= 1) and a 0
        # width would make a consumer's coordinate rescale divide by zero.
        # Never-lie requires None end-to-end, keys OMITTED. Real failure now:
        # `assert (0, 480) is None`.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "png_zero_w.png", _png_bytes(0, 480))
        _assert_dims_none_end_to_end(store, raw)

    def test_png_zero_height_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): symmetric to zero-width — a VALID PNG/IHDR whose
        # height field is 0. Current parser returns (640, 0); never-lie requires
        # None. Real failure now: `assert (640, 0) is None`.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "png_zero_h.png", _png_bytes(640, 0))
        _assert_dims_none_end_to_end(store, raw)

    def test_png_absurd_width_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): a VALID PNG/IHDR whose width field is 0xFFFFFFFF
        # (4294967295), far above the PNG maximum legal dimension of 2**31 - 1.
        # The IHDR structure is intact (length 13, type b"IHDR"), so the current
        # parser returns (4294967295, 1) — a fabricated dimension no real image
        # can have. Never-lie requires None. Real failure now:
        # `assert (4294967295, 1) is None`.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "png_absurd_w.png", _png_bytes(0xFFFFFFFF, 1))
        _assert_dims_none_end_to_end(store, raw)

    def test_png_topbit_width_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): the exact upper boundary — a VALID PNG/IHDR whose
        # width field is 0x80000000 == 2**31 (the top bit set). The PNG spec
        # caps dimensions at 2**31 - 1, so 2**31 is one past the legal maximum
        # and MUST degrade, not be returned. Current parser returns
        # (2147483648, 1); never-lie requires None. Real failure now:
        # `assert (2147483648, 1) is None`. Pins the >= 2**31 boundary distinctly
        # from the 0xFFFFFFFF absurd case.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "png_topbit_w.png", _png_bytes(0x80000000, 1))
        _assert_dims_none_end_to_end(store, raw)

    def test_jpeg_zero_dimension_yields_none(self, tmp_path: Path) -> None:
        # RED (never-lie C3): the same dimension-validity floor applies AFTER a
        # well-formed JPEG parse (consistency across formats). A JPEG whose SOF0
        # segment is structurally valid (declared length correct, fits the
        # buffer) but whose width field is 0 — a real image cannot be 0-wide.
        # Current parser returns (0, 480); never-lie requires None end-to-end,
        # keys OMITTED. Real failure now: `assert (0, 480) is None`.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        raw = _write(tmp_path, "jpeg_zero_w.jpg", _jpeg_bytes(0, 480))
        _assert_dims_none_end_to_end(store, raw)


# ──────────────────────────────────────────────────────────────────────
# R4 — dimension source is the encoded header, content-sniffed (not extension).
# ──────────────────────────────────────────────────────────────────────


class TestFormatSourceContentSniffed:
    def test_png_header_parses_to_exact_dims(self, tmp_path: Path) -> None:
        # RED: FileScreenshotStore.read_dimensions does not exist yet.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        path = _write(tmp_path, "shot.png", _png_bytes(1366, 768))

        assert store.read_dimensions(path) == (1366, 768)

    def test_jpeg_header_parses_to_exact_dims(self, tmp_path: Path) -> None:
        # RED: FileScreenshotStore.read_dimensions does not exist yet.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        path = _write(tmp_path, "shot.jpg", _jpeg_bytes(1024, 576))

        assert store.read_dimensions(path) == (1024, 576)

    def test_detection_is_by_content_not_extension(self, tmp_path: Path) -> None:
        # JPEG bytes in a .png-named file must parse as JPEG dims, and PNG bytes
        # in a .jpg-named file must parse as PNG dims — proves content-sniffing.
        # RED: FileScreenshotStore.read_dimensions does not exist yet.
        store = FileScreenshotStore(base_dir=str(tmp_path))
        jpeg_in_png = _write(tmp_path, "mislabeled.png", _jpeg_bytes(300, 150))
        png_in_jpg = _write(tmp_path, "mislabeled.jpg", _png_bytes(400, 250))

        assert store.read_dimensions(jpeg_in_png) == (300, 150)
        assert store.read_dimensions(png_in_jpg) == (400, 250)

    def test_adapter_imports_no_heavy_imaging_library(self) -> None:
        # C6 / R4 no-heavy-dep guard (GREEN now, MUST stay GREEN): the header
        # parse is stdlib-only. AST-scan the REAL adapter source and assert it
        # imports no PIL/Pillow/cv2/numpy/imageio/skimage/wand.
        import ast

        import aiyes.adapters.file_screenshot_store as mod

        source = Path(mod.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))

        banned = {"PIL", "Pillow", "cv2", "numpy", "imageio", "skimage", "wand"}
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        assert banned.isdisjoint(imported_roots), (
            f"heavy imaging dependency imported: {banned & imported_roots}"
        )
