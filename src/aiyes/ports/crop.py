"""Crop port — Protocol for cropping images to a rectangular region."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CropPort(Protocol):
    """Port for cropping an image to a rectangular region."""

    def crop(
        self, source_path: str, x: int, y: int, w: int, h: int, dest_path: str
    ) -> str:
        """Crop source image to region (x, y, w, h), write to dest_path.

        Returns the destination path.
        """
        ...
