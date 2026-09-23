"""Spectral indices on surface-reflectance arrays.

All functions take float32 2D arrays (NaN = nodata) and return float32 2D
arrays with NaN propagated: any input pixel that is NaN, or whose
denominator is zero, yields NaN. This keeps index rasters honest — a masked
cloud never becomes a fake NDVI value.

The registry (:data:`INDEX_REGISTRY`) maps index names to their band
requirements so pipelines can request assets automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np


def _norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, np.nan, (a - b) / denom)
    out[~np.isfinite(a) | ~np.isfinite(b)] = np.nan
    return out.astype(np.float32)


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Normalised Difference Vegetation Index: (NIR - red) / (NIR + red)."""
    return _norm_diff(nir, red)


def ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Normalised Difference Water Index (McFeeters): (green - NIR)/(green + NIR)."""
    return _norm_diff(green, nir)


def ndmi(nir: np.ndarray, swir1: np.ndarray) -> np.ndarray:
    """Normalised Difference Moisture Index: (NIR - SWIR1)/(NIR + SWIR1)."""
    return _norm_diff(nir, swir1)


def ndbi(swir1: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Normalised Difference Built-up Index: (SWIR1 - NIR)/(SWIR1 + NIR)."""
    return _norm_diff(swir1, nir)


def nbr(nir: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """Normalised Burn Ratio: (NIR - SWIR2)/(NIR + SWIR2)."""
    return _norm_diff(nir, swir2)


def evi(nir: np.ndarray, red: np.ndarray, blue: np.ndarray) -> np.ndarray:
    """Enhanced Vegetation Index: 2.5*(NIR-red)/(NIR+6*red-7.5*blue+1)."""
    nir = np.asarray(nir, dtype=np.float32)
    red = np.asarray(red, dtype=np.float32)
    blue = np.asarray(blue, dtype=np.float32)
    denom = nir + 6.0 * red - 7.5 * blue + 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, np.nan, 2.5 * (nir - red) / denom)
    out[~np.isfinite(nir) | ~np.isfinite(red) | ~np.isfinite(blue)] = np.nan
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def savi(nir: np.ndarray, red: np.ndarray, L: float = 0.5) -> np.ndarray:
    """Soil-Adjusted Vegetation Index: ((NIR-red)/(NIR+red+L))*(1+L)."""
    nir = np.asarray(nir, dtype=np.float32)
    red = np.asarray(red, dtype=np.float32)
    denom = nir + red + L
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, np.nan, ((nir - red) / denom) * (1.0 + L))
    out[~np.isfinite(nir) | ~np.isfinite(red)] = np.nan
    return out.astype(np.float32)


def bsi(
    swir1: np.ndarray, red: np.ndarray, nir: np.ndarray, blue: np.ndarray
) -> np.ndarray:
    """Bare Soil Index: ((SWIR1+red)-(NIR+blue))/((SWIR1+red)+(NIR+blue))."""
    swir1 = np.asarray(swir1, dtype=np.float32)
    red = np.asarray(red, dtype=np.float32)
    nir = np.asarray(nir, dtype=np.float32)
    blue = np.asarray(blue, dtype=np.float32)
    denom = (swir1 + red) + (nir + blue)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(
            denom == 0, np.nan, ((swir1 + red) - (nir + blue)) / denom
        )
    out[
        ~np.isfinite(swir1) | ~np.isfinite(red) | ~np.isfinite(nir) | ~np.isfinite(blue)
    ] = np.nan
    return out.astype(np.float32)


@dataclass(frozen=True)
class IndexSpec:
    """Registry entry: callable, required band aliases, and documentation."""

    func: Callable[..., np.ndarray]
    bands: Tuple[str, ...]
    description: str
    typical_range: Tuple[float, float] = (-1.0, 1.0)


INDEX_REGISTRY: Dict[str, IndexSpec] = {
    "ndvi": IndexSpec(
        ndvi,
        ("nir", "red"),
        "Vegetation vigour; >0.6 dense canopy, <0.2 bare/disturbed.",
    ),
    "evi": IndexSpec(
        evi,
        ("nir", "red", "blue"),
        "Vegetation vigour, less saturation than NDVI over dense canopy.",
    ),
    "savi": IndexSpec(
        savi,
        ("nir", "red"),
        "Vegetation vigour with soil-brightness correction (sparse cover).",
    ),
    "ndwi": IndexSpec(
        ndwi,
        ("green", "nir"),
        "Open water delineation; water > 0.",
    ),
    "ndmi": IndexSpec(
        ndmi,
        ("nir", "swir1"),
        "Canopy/soil moisture stress; falling values flag drying.",
    ),
    "ndbi": IndexSpec(
        ndbi,
        ("swir1", "nir"),
        "Built-up / impervious surfaces; useful for disturbance tracking.",
    ),
    "nbr": IndexSpec(
        nbr,
        ("nir", "swir2"),
        "Burn severity and bare-earth exposure mapping.",
    ),
    "bsi": IndexSpec(
        bsi,
        ("swir1", "red", "nir", "blue"),
        "Bare soil exposure; complements NDVI for cut/fill monitoring.",
    ),
}


def list_indices() -> List[str]:
    return sorted(INDEX_REGISTRY)


def required_bands(index_name: str) -> Tuple[str, ...]:
    try:
        return INDEX_REGISTRY[index_name].bands
    except KeyError as exc:
        raise ValueError(
            f"unknown index {index_name!r}; known: {', '.join(list_indices())}"
        ) from exc


def compute(index_name: str, bands: Dict[str, np.ndarray], **kwargs) -> np.ndarray:
    """Compute a registered index from a dict of alias -> reflectance array.

    Extra keyword arguments are forwarded to the index function (e.g.
    ``L`` for SAVI). Raises ``ValueError`` for unknown indices and
    ``KeyError``-style ``ValueError`` when a required band is missing.
    """
    spec = INDEX_REGISTRY.get(index_name)
    if spec is None:
        raise ValueError(
            f"unknown index {index_name!r}; known: {', '.join(list_indices())}"
        )
    missing = [b for b in spec.bands if b not in bands]
    if missing:
        raise ValueError(
            f"index {index_name!r} needs bands {missing} which were not supplied"
        )
    args = [np.asarray(bands[b], dtype=np.float32) for b in spec.bands]
    shapes = {a.shape for a in args}
    if len(shapes) != 1:
        raise ValueError(
            f"band arrays for {index_name!r} have mismatched shapes: {shapes}"
        )
    return spec.func(*args, **kwargs)


def describe_index(index_name: str) -> str:
    spec = INDEX_REGISTRY.get(index_name)
    if spec is None:
        raise ValueError(f"unknown index {index_name!r}")
    lo, hi = spec.typical_range
    return (
        f"{index_name.upper()}: {spec.description} "
        f"(bands: {', '.join(spec.bands)}; typical range {lo}..{hi})"
    )
