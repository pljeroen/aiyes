"""ImageMagickCropAdapter — implements CropPort via ImageMagick.

Prefers `magick` (ImageMagick 7) with fallback to `convert` (IM6/legacy).
When source and destination are the same path, crops to a temp file first
and replaces the original on success to avoid data loss on failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def _resolve_imagemagick_binary() -> str:
    """Return the ImageMagick binary: 'magick' (preferred) or 'convert' (fallback).

    Raises RuntimeError if neither is found.
    """
    magick = shutil.which("magick")
    if magick is not None:
        return magick
    convert = shutil.which("convert")
    if convert is not None:
        return convert
    raise RuntimeError("ImageMagick not found: neither 'magick' nor 'convert' in PATH")


class ImageMagickCropAdapter:
    """Crops images using ImageMagick."""

    def crop(
        self, source_path: str, x: int, y: int, w: int, h: int, dest_path: str
    ) -> str:
        """Crop source image to region (x, y, w, h).

        Uses ImageMagick geometry format: WxH+X+Y
        Prefers `magick` (IM7), falls back to `convert` (IM6).

        When source_path == dest_path, crops to a temp file first and
        replaces the original on success.  This avoids destroying the
        original if the crop subprocess fails.
        """
        binary = _resolve_imagemagick_binary()
        geometry = f"{w}x{h}+{x}+{y}"

        in_place = os.path.abspath(source_path) == os.path.abspath(dest_path)
        if in_place:
            dir_name = os.path.dirname(os.path.abspath(dest_path))
            fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=dir_name)
            os.close(fd)
            try:
                subprocess.run(
                    [binary, source_path, "-crop", geometry, "+repage", tmp_path],
                    check=True,
                )
                os.replace(tmp_path, dest_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        else:
            subprocess.run(
                [binary, source_path, "-crop", geometry, "+repage", dest_path],
                check=True,
            )
        return dest_path
