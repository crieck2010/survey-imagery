"""Temporal compositing: collapse a stack of scenes into one clean image.

Optical satellites revisit every 5-16 days, but any single pass can be
clouded out. Compositing takes the per-pixel best (median/mean/max) across
the stack, ignoring NaN (masked) pixels, so a month of passes becomes one
cloud-free product for mapping and cartography.
"""

from __future__ import annotations

from typing import List, Sequence
import warnings

import numpy as np


def temporal_composite(
    arrays: Sequence[np.ndarray], method: str = "median"
) -> np.ndarray:
    """Composite a time stack (all 2D, same shape) into one 2D array.

    ``method`` is one of ``median`` (default, robust to undetected cloud),
    ``mean``, ``max`` (greenest-pixel for vegetation), or ``min``.
    NaN pixels are ignored; a pixel NaN in every scene stays NaN.
    """
    if not arrays:
        raise ValueError("temporal_composite needs at least one array")
    shapes = {np.shape(a) for a in arrays}
    if len(shapes) != 1:
        raise ValueError(f"all arrays must share a shape, got {shapes}")
    stack = np.stack([np.asarray(a, dtype=np.float32) for a in arrays], axis=0)
    # All-NaN pixels are documented behaviour (stay NaN); silence NumPy's
    # per-slice RuntimeWarning so batch jobs stay quiet.
    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        if method == "median":
            out = np.nanmedian(stack, axis=0)
        elif method == "mean":
            out = np.nanmean(stack, axis=0)
        elif method == "max":
            out = np.nanmax(stack, axis=0)
        elif method == "min":
            out = np.nanmin(stack, axis=0)
        else:
            raise ValueError(
                f"unknown composite method {method!r}; "
                "use median, mean, max, or min"
            )
    # Ensure float32 output (numpy < 1.22 nanmax on all-NaN raises; guarded
    # above by the warning filter on supported versions).
    result = np.asarray(out, dtype=np.float32)
    return result


def valid_pixel_count(arrays: Sequence[np.ndarray]) -> np.ndarray:
    """Per-pixel count of valid (finite) observations across the stack."""
    if not arrays:
        raise ValueError("valid_pixel_count needs at least one array")
    stack = np.stack([np.isfinite(np.asarray(a, dtype=np.float32)) for a in arrays])
    return stack.sum(axis=0).astype(np.int32)


def coverage_fraction(arrays: Sequence[np.ndarray]) -> float:
    """Fraction of pixels with at least one valid observation."""
    counts = valid_pixel_count(arrays)
    if counts.size == 0:
        return float("nan")
    return float((counts > 0).mean())


def best_scene_index(arrays: Sequence[np.ndarray]) -> np.ndarray:
    """Per-pixel index of the scene with the maximum value (argmax, NaN-safe).

    Useful for "greenest pixel" composites where the source date matters:
    pair with the scene datetimes to build a date-of-max raster.
    """
    if not arrays:
        raise ValueError("best_scene_index needs at least one array")
    stack = np.stack([np.asarray(a, dtype=np.float32) for a in arrays], axis=0)
    filled = np.where(np.isfinite(stack), stack, -np.inf)
    idx = np.argmax(filled, axis=0).astype(np.int32)
    idx[~np.isfinite(stack).any(axis=0)] = -1
    return idx
