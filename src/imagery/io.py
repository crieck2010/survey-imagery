"""Output writers: Cloud-Optimized GeoTIFFs, GeoJSON, and reports.

Every raster product is written as a Cloud-Optimized GeoTIFF (COG): tiled,
compressed, with internal overviews. COGs open directly in QGIS, stream over
HTTP with range requests, and are the standard interchange format for the
rest of the survey suite.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Sequence, Tuple

import numpy as np

try:
    import rasterio
    from rasterio.transform import Affine
except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
    raise ImportError("survey-imagery io requires 'rasterio'") from exc


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def write_cog(
    path: str,
    data: np.ndarray,
    transform: Tuple[float, float, float, float, float, float],
    crs: str,
    nodata: float = float("nan"),
    compress: str = "deflate",
) -> str:
    """Write a single-band float32 Cloud-Optimized GeoTIFF. Returns the path."""
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"write_cog expects a 2D array, got shape {arr.shape}")
    affine = Affine(transform[0], transform[1], transform[2],
                    transform[3], transform[4], transform[5])
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    # Write via an in-memory GTiff first, then translate to COG, so the
    # output is a valid COG even on rasterio builds whose COG driver lacks
    # direct-create support.
    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": affine,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": compress,
    }
    tmp_path = path + ".tmp.tif"
    with rasterio.open(tmp_path, "w", **profile) as dst:
        dst.write(arr, 1)
        # Overview levels that fit the raster: each level must leave at
        # least a few pixels per side, otherwise small AOI clips fail.
        min_dim = min(arr.shape)
        levels = [2 ** i for i in range(1, 6) if min_dim // (2 ** i) >= 16]
        if levels:
            dst.build_overviews(levels, rasterio.enums.Resampling.average)
            dst.update_tags(ns="rio_overview", resampling="average")
    # Translate to COG layout.
    with rasterio.open(tmp_path) as src:
        cog_profile = src.profile.copy()
        cog_profile.update(driver="COG", compress=compress)
        with rasterio.open(path, "w", **cog_profile) as dst:
            dst.write(src.read())
            dst.update_tags(**src.tags())
    os.remove(tmp_path)
    return path


def write_multiband_cog(
    path: str,
    bands: Dict[str, np.ndarray],
    transform: Tuple[float, float, float, float, float, float],
    crs: str,
    nodata: float = float("nan"),
) -> str:
    """Write several same-shaped bands as one multiband COG (band order kept)."""
    names = list(bands.keys())
    arrays = [np.asarray(bands[n], dtype=np.float32) for n in names]
    shapes = {a.shape for a in arrays}
    if len(shapes) != 1:
        raise ValueError(f"bands have mismatched shapes: {shapes}")
    stacked = np.stack(arrays, axis=0)
    affine = Affine(transform[0], transform[1], transform[2],
                    transform[3], transform[4], transform[5])
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    profile = {
        "driver": "GTiff",
        "height": stacked.shape[1],
        "width": stacked.shape[2],
        "count": stacked.shape[0],
        "dtype": "float32",
        "crs": crs,
        "transform": affine,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
    }
    tmp_path = path + ".tmp.tif"
    with rasterio.open(tmp_path, "w", **profile) as dst:
        dst.write(stacked)
        for i, name in enumerate(names, start=1):
            dst.set_band_description(i, name)
    with rasterio.open(tmp_path) as src:
        cog_profile = src.profile.copy()
        cog_profile.update(driver="COG", compress="deflate")
        with rasterio.open(path, "w", **cog_profile) as dst:
            dst.write(src.read())
            for i, name in enumerate(names, start=1):
                dst.set_band_description(i, name)
    os.remove(tmp_path)
    return path


def write_geojson(path: str, obj: Dict) -> str:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
    return path


def write_text(path: str, text: str) -> str:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path
