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
from dataclasses import asdict, dataclass, fields
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneStats":
        """Rebuild a record from :meth:`to_dict` output (CSV/JSON rows).

        Numeric fields are cast back from their serialised strings; an empty
        cloud-cover cell becomes ``None``.
        """
        def _float(key: str) -> float:
            return float(data[key])

        def _int(key: str) -> int:
            return int(float(data[key]))

        cloud = data.get("cloud_cover")
        return cls(
            scene_id=str(data["scene_id"]),
            datetime=str(data["datetime"]),
            index=str(data["index"]),
            mean=_float("mean"),
            median=_float("median"),
            std=_float("std"),
            minimum=_float("minimum"),
            maximum=_float("maximum"),
            p10=_float("p10"),
            p90=_float("p90"),
            valid_pixels=_int("valid_pixels"),
            total_pixels=_int("total_pixels"),
            cloud_cover=None if cloud in (None, "") else float(cloud),
        )


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


def read_csv(path: str) -> List[SceneStats]:
    """Read a time series written by :func:`write_csv`."""
    with open(path, newline="", encoding="utf-8") as handle:
        return [SceneStats.from_dict(row) for row in csv.DictReader(handle)]


def read_json(path: str) -> List[SceneStats]:
    """Read a time series written by :func:`write_json`."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return [SceneStats.from_dict(row) for row in data]


def merge_records(
    existing: Sequence[SceneStats], new: Sequence[SceneStats]
) -> List[SceneStats]:
    """Merge a fresh monitor run into a stored series.

    Records are keyed by ``(scene_id, index)``; a reprocessed scene replaces
    its earlier record, so re-running a monitor over the same date window
    never duplicates rows. The merged series is sorted by
    ``(datetime, index, scene_id)``.
    """
    merged: Dict[Tuple[str, str], SceneStats] = {
        (r.scene_id, r.index): r for r in existing
    }
    for record in new:
        merged[(record.scene_id, record.index)] = record
    return sorted(
        merged.values(), key=lambda r: (r.datetime, r.index, r.scene_id)
    )


def series_fieldnames() -> List[str]:
    """CSV column order for :class:`SceneStats` (used for header-only files)."""
    return [f.name for f in fields(SceneStats)]


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
