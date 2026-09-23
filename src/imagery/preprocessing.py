"""Radiometric preprocessing: reflectance scaling and cloud masking.

Raw satellite assets are stored as scaled integers. This module converts them
to unitless surface reflectance and builds clear-sky masks from the missions'
own quality layers, so every index and composite downstream works on
comparable, cloud-free pixels.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

# Sentinel-2 L2A scene classification (SCL) codes.
# https://sentinels.copernicus.eu/web/sentinel/technical-guides/sentinel-2-msi/level-2a/algorithm-overview
SCL_NO_DATA = 0
SCL_SATURATED = 1
SCL_DARK = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_BARE_SOIL = 5
SCL_WATER = 6
SCL_CLOUD_LOW = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_THIN_CIRRUS = 10
SCL_SNOW = 11

#: SCL classes treated as clear sky by default.
SCL_CLEAR = frozenset({SCL_VEGETATION, SCL_BARE_SOIL, SCL_WATER, SCL_CLOUD_LOW})
#: SCL classes treated as cloud contamination by default.
SCL_CLOUD = frozenset(
    {SCL_CLOUD_SHADOW, SCL_CLOUD_MEDIUM, SCL_CLOUD_HIGH, SCL_THIN_CIRRUS}
)


def to_reflectance(
    data: np.ndarray, scale: float = 0.0001, offset: float = 0.0
) -> np.ndarray:
    """Convert stored digital numbers to surface reflectance (float32).

    NaN input stays NaN. Values are clipped to the physical [0, 1] range
    after scaling, which also discards saturated-sensor artefacts.
    """
    arr = np.asarray(data, dtype=np.float32)
    with np.errstate(invalid="ignore"):
        refl = arr * np.float32(scale) + np.float32(offset)
    refl = np.clip(refl, 0.0, 1.0)
    refl[~np.isfinite(arr)] = np.nan
    return refl.astype(np.float32)


def cloud_mask_scl(
    scl: np.ndarray,
    clear_classes: frozenset = SCL_CLEAR,
) -> np.ndarray:
    """Clear-sky mask from a Sentinel-2 L2A SCL band.

    Returns a boolean array: True where the pixel is usable. NaN SCL codes
    (outside the AOI window) count as unusable.
    """
    arr = np.asarray(scl)
    mask = np.zeros(arr.shape, dtype=bool)
    finite = np.isfinite(arr)
    mask[finite] = np.isin(arr[finite].astype(np.int64), list(clear_classes))
    return mask


def cloud_mask_qa_pixel(qa: np.ndarray) -> np.ndarray:
    """Clear-sky mask from a Landsat Collection 2 QA_PIXEL band.

    QA_PIXEL bit layout (C2): 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud,
    4 cloud shadow, 5 snow, 6 clear, 7 water, 8-9 cloud confidence,
    10-11 cloud-shadow confidence, 12-13 snow/ice confidence, 14-15 cirrus
    confidence. A pixel is usable when the clear bit is set and none of the
    contamination bits (dilated cloud, cirrus, cloud, cloud shadow) are set.
    """
    arr = np.asarray(qa)
    mask = np.zeros(arr.shape, dtype=bool)
    finite = np.isfinite(arr)
    q = arr[finite].astype(np.int64)
    clear = ((q >> 6) & 1) == 1
    contaminated = (
        ((q >> 1) & 1) | ((q >> 2) & 1) | ((q >> 3) & 1) | ((q >> 4) & 1)
    ).astype(bool)
    mask[finite] = clear & ~contaminated
    return mask


def mask_for_collection(collection: str, mask_band: np.ndarray) -> np.ndarray:
    """Dispatch to the right cloud-mask routine for a collection."""
    if collection == "sentinel-2-l2a":
        return cloud_mask_scl(mask_band)
    if collection == "landsat-c2-l2":
        return cloud_mask_qa_pixel(mask_band)
    raise ValueError(f"no cloud-mask routine for collection {collection!r}")


def apply_mask(data: np.ndarray, clear_mask: np.ndarray) -> np.ndarray:
    """Set masked-out pixels to NaN (float32 output)."""
    arr = np.asarray(data, dtype=np.float32)
    if clear_mask.shape != arr.shape:
        raise ValueError(
            f"mask shape {clear_mask.shape} != data shape {arr.shape}"
        )
    out = arr.copy()
    out[~np.asarray(clear_mask, dtype=bool)] = np.nan
    return out


def clear_fraction(clear_mask: np.ndarray) -> float:
    """Fraction of pixels flagged clear (0..1); NaN when the mask is empty."""
    mask = np.asarray(clear_mask, dtype=bool)
    if mask.size == 0:
        return float("nan")
    return float(mask.mean())


def valid_fraction(data: np.ndarray) -> float:
    """Fraction of finite pixels in an array."""
    arr = np.asarray(data)
    if arr.size == 0:
        return float("nan")
    return float(np.isfinite(arr).mean())
