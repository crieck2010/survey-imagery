"""Site monitor: per-pass satellite monitoring for a defined AOI.

Define the site once (AOI + collections + indices + output folder); every run
searches for new scenes in the date window, reads each scene's bands clipped
to the AOI, masks clouds, computes the requested indices, writes
Cloud-Optimized GeoTIFFs + QML styles, appends per-scene statistics to
``timeseries.csv``, and renders a Markdown report. Re-running with a later
end date is the "refresh with each pass" loop.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from .acquisition import BandData, read_stack
from .aoi import AOI
from .bands import (
    asset_key,
    assets_for,
    collection_info,
    mask_alias,
    reflectance_scale_offset,
)
from .indices import compute, list_indices, required_bands
from .io import ensure_dir, write_cog, write_geojson, write_text
from .preprocessing import (
    apply_mask,
    clear_fraction,
    mask_for_collection,
    to_reflectance,
    valid_fraction,
)
from .qgis import list_styles, write_style_qml
from .stac import Scene, filter_max_cloud, latest_per_date, search_scenes
from .timeseries import SceneStats, summarize, write_csv, write_json, zonal_stats


class MonitorError(RuntimeError):
    """Raised when a monitor run cannot proceed."""


@dataclass
class MonitorConfig:
    """Everything a site monitor needs, serialisable to JSON."""

    name: str
    aoi: AOI
    collections: List[str]
    indices: List[str]
    output_dir: str
    api_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    start: str = ""  # ISO date; defaults to 90 days before end
    end: str = ""  # ISO date; defaults to today
    max_cloud_cover: Optional[float] = 40.0
    target_resolution_m: Optional[float] = None
    composite: bool = False
    signer: Optional[Callable[[str], str]] = None  # not serialised

    def __post_init__(self) -> None:
        if not self.collections:
            raise MonitorError("monitor needs at least one collection")
        for collection in self.collections:
            collection_info(collection)
        unknown = [i for i in self.indices if i not in list_indices()]
        if unknown:
            raise MonitorError(f"unknown indices: {unknown}")
        if not self.end:
            self.end = _dt.date.today().isoformat()
        if not self.start:
            end_d = _dt.date.fromisoformat(self.end[:10])
            self.start = (end_d - _dt.timedelta(days=90)).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "aoi": self.aoi.to_dict(),
            "collections": list(self.collections),
            "indices": list(self.indices),
            "output_dir": self.output_dir,
            "api_url": self.api_url,
            "start": self.start,
            "end": self.end,
            "max_cloud_cover": self.max_cloud_cover,
            "target_resolution_m": self.target_resolution_m,
            "composite": self.composite,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonitorConfig":
        data = dict(data)
        data["aoi"] = AOI.from_dict(data["aoi"])
        return cls(**{k: v for k, v in data.items() if k != "signer"})


@dataclass
class MonitorResult:
    config_name: str
    scenes: List[Scene]
    records: List[SceneStats]
    rasters: List[str]
    report_path: str
    timeseries_csv: str
    summary: Dict[str, Any]


def _scene_assets(scene: Scene) -> Dict[str, str]:
    return dict(scene.assets)


def process_scene(
    scene: Scene,
    config: MonitorConfig,
    out_dir: str,
) -> List[SceneStats]:
    """Process one scene: bands -> reflectance -> mask -> indices -> COGs."""
    collection = scene.collection
    needed = sorted({b for idx in config.indices for b in required_bands(idx)})
    mask = mask_alias(collection)
    aliases = needed + ([mask] if mask and mask not in needed else [])
    assets = {a: scene.href(a) for a in aliases if a in scene.assets}
    missing = [a for a in aliases if a not in assets]
    if missing:
        raise MonitorError(
            f"scene {scene.id} is missing assets for aliases: {missing}"
        )

    stack = read_stack(
        assets,
        aliases,
        aoi=config.aoi,
        target_resolution_m=config.target_resolution_m,
        collection=collection,
        signer=config.signer,
    )
    scale, offset = reflectance_scale_offset(collection)
    refl = {a: to_reflectance(b.data, scale, offset) for a, b in stack.items()}

    clear = None
    if mask and mask in refl:
        clear = mask_for_collection(collection, stack[mask].data)
        for a in needed:
            refl[a] = apply_mask(refl[a], clear)

    ref_band = stack[needed[0]]
    records: List[SceneStats] = []
    date_tag = scene.datetime[:10]
    for index_name in config.indices:
        values = compute(index_name, {a: refl[a] for a in required_bands(index_name)})
        fname = f"{config.name}_{index_name}_{date_tag}.tif"
        raster_path = write_cog(
            os.path.join(out_dir, "rasters", fname),
            values,
            ref_band.transform,
            ref_band.crs,
        )
        style = index_name if index_name in list_styles() else "ndvi"
        write_style_qml(
            os.path.join(out_dir, "rasters", fname.replace(".tif", ".qml")),
            style=style,
        )
        records.append(
            zonal_stats(
                values,
                ref_band.transform,
                ref_band.crs,
                config.aoi,
                scene_id=scene.id,
                datetime=scene.datetime,
                index=index_name,
                cloud_cover=scene.cloud_cover,
            )
        )
    return records


def run_monitor(
    config: MonitorConfig,
    progress: Optional[Callable[[str], None]] = None,
) -> MonitorResult:
    """Run the full monitor pipeline. Returns paths, records, and a summary."""
    log = progress or (lambda _msg: None)
    out_dir = ensure_dir(config.output_dir)
    ensure_dir(os.path.join(out_dir, "rasters"))
    write_geojson(
        os.path.join(out_dir, "aoi.geojson"), config.aoi.to_geojson_feature()
    )

    log(f"searching {config.collections} for {config.start}..{config.end}")
    scenes: List[Scene] = []
    for collection in config.collections:
        found = search_scenes(
            config.api_url,
            [collection],
            config.aoi,
            config.start,
            config.end,
            max_cloud_cover=config.max_cloud_cover,
        )
        log(f"  {collection}: {len(found)} scenes")
        scenes.extend(found)
    scenes = latest_per_date(scenes)
    if config.max_cloud_cover is not None:
        scenes = filter_max_cloud(scenes, config.max_cloud_cover)
    if not scenes:
        raise MonitorError("no scenes found for the given AOI/date window")

    all_records: List[SceneStats] = []
    rasters: List[str] = []
    for i, scene in enumerate(scenes, start=1):
        log(f"processing scene {i}/{len(scenes)}: {scene.id}")
        try:
            records = process_scene(scene, config, out_dir)
        except MonitorError as exc:
            log(f"  skipped: {exc}")
            continue
        all_records.extend(records)
        date_tag = scene.datetime[:10]
        rasters.extend(
            os.path.join(out_dir, "rasters", f"{config.name}_{idx}_{date_tag}.tif")
            for idx in config.indices
        )

    csv_path = write_csv(all_records, os.path.join(out_dir, "timeseries.csv"))
    write_json(all_records, os.path.join(out_dir, "timeseries.json"))
    summary = summarize(all_records)
    report_path = os.path.join(out_dir, "report.md")
    write_text(report_path, render_report(config, scenes, summary))

    return MonitorResult(
        config_name=config.name,
        scenes=scenes,
        records=all_records,
        rasters=rasters,
        report_path=report_path,
        timeseries_csv=csv_path,
        summary=summary,
    )


def render_report(
    config: MonitorConfig,
    scenes: List[Scene],
    summary: Dict[str, Any],
) -> str:
    lines = [
        f"# Site monitor report: {config.name}",
        "",
        f"- AOI: {config.aoi.name or 'unnamed'} ({config.aoi.crs})",
        f"- Window: {config.start} .. {config.end}",
        f"- Collections: {', '.join(config.collections)}",
        f"- Indices: {', '.join(config.indices)}",
        f"- Scenes processed: {len(scenes)}",
        "",
        "## Time-series summary",
        "",
    ]
    if summary.get("valid_scenes"):
        lines += [
            f"- Valid scenes: {summary['valid_scenes']} of {summary['scenes']}",
            f"- Index: {summary['index']}",
            f"- Span: {summary['start']} .. {summary['end']}",
            f"- Mean {summary['index'].upper()} first scene: {summary['first_mean']:.4f}",
            f"- Mean {summary['index'].upper()} last scene: {summary['last_mean']:.4f}",
            f"- Change in mean: {summary['delta_mean']:+.4f}",
            f"- Min/Max scene mean: {summary['min_mean']:.4f} / {summary['max_mean']:.4f}",
        ]
    else:
        lines.append("- No valid scenes produced statistics.")
    lines += [
        "",
        "## Scenes",
        "",
        "| Date | Scene ID | Cloud % |",
        "|------|----------|---------|",
    ]
    for scene in scenes:
        cloud = (
            f"{scene.cloud_cover:.1f}" if scene.cloud_cover is not None else "n/a"
        )
        lines.append(f"| {scene.datetime[:10]} | {scene.id} | {cloud} |")
    lines += [
        "",
        "## Outputs",
        "",
        "- `rasters/`: per-scene Cloud-Optimized GeoTIFFs + .qml styles for QGIS",
        "- `timeseries.csv` / `timeseries.json`: per-scene zonal statistics",
        "- `aoi.geojson`: the monitored site footprint",
        "",
        "_Generated by survey-imagery site monitor._",
    ]
    return "\n".join(lines) + "\n"
