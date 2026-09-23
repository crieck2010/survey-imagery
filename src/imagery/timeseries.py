"""Time-series extraction: per-scene zonal statistics over an AOI.

The "refreshed with each pass" story needs numbers, not just rasters: mean
NDVI of the site on every acquisition date, plotted or alarmed on. This
module reduces each scene's index raster to a statistics record inside the
AOI polygon and serialises the series to CSV/JSON for dashboards, QGIS
attribute tables, or spreadsheets.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import rasterio
    from rasterio.features import geometry_mask
except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
    raise ImportError("survey-imagery timeseries requires 'rasterio'") from exc

from .aoi import AOI


@dataclass
class SceneStats:
    """Zonal statistics for one scene's index raster inside an AOI."""

    scene_id: str
    datetime: str
    index: str
    mean: float
    median: float
    std: float
    minimum: float
    maximum: float
    p10: float
    p90: float
    valid_pixels: int
    total_pixels: int
    cloud_cover: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def zonal_stats(
    data: np.ndarray,
    transform: Tuple[float, float, float, float, float, float],
    crs: str,
    aoi: AOI,
    scene_id: str = "",
    datetime: str = "",
    index: str = "",
    cloud_cover: Optional[float] = None,
) -> SceneStats:
    """Compute statistics of ``data`` inside the AOI polygon.

    ``transform`` is a GDAL-style affine tuple ``(a, b, c, d, e, f)``.
    """
    from rasterio.transform import Affine

    arr = np.asarray(data, dtype=np.float32)
    affine = Affine(transform[0], transform[1], transform[2],
                    transform[3], transform[4], transform[5])
    geom = aoi.geometry
    if str(crs).upper() != str(aoi.crs).upper():
        from rasterio.warp import transform_geom

        geom = transform_geom(aoi.crs, crs, geom)
    inside = geometry_mask([geom], out_shape=arr.shape, transform=affine,
                           invert=True)
    values = arr[inside]
    values = values[np.isfinite(values)]
    total = int(inside.sum())
    valid = int(values.size)

    def _stat(func, default=float("nan")) -> float:
        if valid == 0:
            return default
        with np.errstate(invalid="ignore"):
            return float(func(values))

    return SceneStats(
        scene_id=scene_id,
        datetime=datetime,
        index=index,
        mean=_stat(np.mean),
        median=_stat(np.median),
        std=_stat(np.std),
        minimum=_stat(np.min),
        maximum=_stat(np.max),
        p10=_stat(lambda v: np.percentile(v, 10)),
        p90=_stat(lambda v: np.percentile(v, 90)),
        valid_pixels=valid,
        total_pixels=total,
        cloud_cover=cloud_cover,
    )


def write_csv(records: Sequence[SceneStats], path: str) -> str:
    """Write a time series to CSV. Returns the path written."""
    rows = [r.to_dict() for r in records]
    if not rows:
        raise ValueError("no records to write")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_json(records: Sequence[SceneStats], path: str) -> str:
    """Write a time series to JSON. Returns the path written."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([r.to_dict() for r in records], handle, indent=2)
    return path


def summarize(records: Sequence[SceneStats]) -> Dict[str, Any]:
    """Headline numbers for a finished series: span, scenes, latest value."""
    rows = [r for r in records if r.valid_pixels > 0]
    if not rows:
        return {"scenes": 0, "valid_scenes": 0}
    ordered = sorted(rows, key=lambda r: r.datetime)
    first, last = ordered[0], ordered[-1]
    return {
        "scenes": len(records),
        "valid_scenes": len(rows),
        "index": rows[0].index,
        "start": first.datetime,
        "end": last.datetime,
        "first_mean": first.mean,
        "last_mean": last.mean,
        "delta_mean": last.mean - first.mean,
        "min_mean": min(r.mean for r in rows),
        "max_mean": max(r.mean for r in rows),
    }
