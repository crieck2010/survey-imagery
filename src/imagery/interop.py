"""Interoperability with the rest of the surveying + remote sensing suite.

Every adapter here is duck-typed and lazily imported: this package never
imports a sibling at module load time, so ``survey-imagery`` stays usable
standalone while slotting into pipelines that already use the other modules.

Interchange format
------------------
Band/index rasters move between modules as plain dicts::

    {"data": np.ndarray (2D float32, NaN = nodata),
     "transform": (a, b, c, d, e, f) GDAL-style affine tuple,
     "crs": "EPSG:32618",
     "nodata": float("nan")}

``survey-raster``'s ``Raster`` objects convert to and from this format;
``survey-geodesy`` CRS objects are accepted anywhere a CRS string is expected.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

GRID_KEYS = ("data", "transform", "crs", "nodata")


def as_grid(
    data: np.ndarray,
    transform: Tuple[float, float, float, float, float, float],
    crs: Any,
    nodata: float = float("nan"),
) -> Dict[str, Any]:
    """Build the suite interchange grid dict from array + georeferencing."""
    return {
        "data": np.asarray(data, dtype=np.float32),
        "transform": tuple(transform),
        "crs": crs_from_any(crs),
        "nodata": nodata,
    }


def check_grid(grid: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an interchange grid dict; raises ``ValueError`` if invalid."""
    missing = [k for k in GRID_KEYS if k not in grid]
    if missing:
        raise ValueError(f"grid is missing keys: {missing}")
    data = np.asarray(grid["data"], dtype=np.float32)
    if data.ndim != 2:
        raise ValueError(f"grid data must be 2D, got shape {data.shape}")
    transform = tuple(grid["transform"])
    if len(transform) != 6:
        raise ValueError("grid transform must be a 6-element affine tuple")
    return {
        "data": data,
        "transform": transform,
        "crs": crs_from_any(grid["crs"]),
        "nodata": grid["nodata"],
    }


def crs_from_any(crs: Any) -> str:
    """Best-effort CRS -> string, accepting survey-geodesy CRS objects.

    Accepts plain strings (returned unchanged), objects with ``to_wkt()`` /
    ``to_proj4()`` / ``to_epsg()`` methods, ``pyproj.CRS``-likes, and anything
    with a ``crs`` or ``wkt`` attribute.
    """
    if isinstance(crs, str):
        return crs
    for method in ("to_wkt", "to_proj4", "to_epsg", "to_string"):
        func = getattr(crs, method, None)
        if callable(func):
            try:
                value = func()
                return str(value)
            except Exception:
                continue
    for attr in ("wkt", "crs", "srs"):
        value = getattr(crs, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(crs)


def to_survey_raster(grid: Dict[str, Any]):
    """Convert an interchange grid to a ``survey_raster.Raster`` object.

    Requires the ``survey-raster`` package to be installed; raises
    ``ImportError`` with an install hint otherwise.
    """
    grid = check_grid(grid)
    try:
        from raster import Raster  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "to_survey_raster needs the 'survey-raster' package "
            "(pip install survey-raster)"
        ) from exc
    data = grid["data"]
    nodata = grid["nodata"]
    clean = np.where(np.isfinite(data), data, nodata)
    return Raster(
        clean.tolist(), transform=grid["transform"], crs=grid["crs"], nodata=nodata
    )


def from_survey_raster(raster_obj: Any) -> Dict[str, Any]:
    """Convert a ``survey_raster.Raster``-like object to an interchange grid.

    Reads ``data`` / ``transform`` / ``crs`` / ``nodata`` attributes; the
    sibling's nodata sentinel becomes NaN in the grid.
    """
    data = np.asarray(raster_obj.data, dtype=np.float32)
    nodata = getattr(raster_obj, "nodata", float("nan"))
    with np.errstate(invalid="ignore"):
        data = np.where(data == nodata, np.nan, data)
    return as_grid(
        data,
        tuple(raster_obj.transform),
        crs_from_any(getattr(raster_obj, "crs", "")),
        nodata=float("nan"),
    )


def grid_to_pointcloud_xyz(
    grid: Dict[str, Any], band_value_name: str = "value"
) -> Dict[str, Any]:
    """Express a grid as point records for ``survey-pointcloud``-style use.

    Returns ``{"points": [(x, y, value), ...], "crs": ...}`` using pixel
    centres, skipping NaN cells. Intended for small AOIs / sampled grids —
    full scenes stay rasters.
    """
    grid = check_grid(grid)
    data = grid["data"]
    a, b, c, d, e, f = grid["transform"]
    rows, cols = np.where(np.isfinite(data))
    points = [
        (c + (col + 0.5) * a + (row + 0.5) * b,
         f + (col + 0.5) * d + (row + 0.5) * e,
         float(data[row, col]))
        for row, col in zip(rows.tolist(), cols.tolist())
    ]
    return {"points": points, "crs": grid["crs"], "value_name": band_value_name}


def describe_interop() -> str:
    return (
        "survey-imagery <-> suite interchange\n"
        "------------------------------------\n"
        "- Grid dict {data, transform, crs, nodata}: shared with survey-raster\n"
        "  (to_survey_raster / from_survey_raster convert to Raster objects).\n"
        "- CRS strings: survey-geodesy CRS objects accepted via crs_from_any().\n"
        "- grid_to_pointcloud_xyz: sample an index raster to XYZ points for\n"
        "  survey-pointcloud style analysis on small AOIs.\n"
        "- COG outputs open directly in QGIS and in survey-raster readers.\n"
        "- timeseries.csv joins to AOI GeoJSON on scene_id for mapping.\n"
    )
