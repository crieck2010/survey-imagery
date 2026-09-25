"""Site monitor: per-pass satellite monitoring for a defined AOI.

Define the site once (AOI + collections + indices + output folder); every run
searches for new scenes in the date window, reads each scene's bands clipped
to the AOI, masks clouds, computes the requested indices, writes
Cloud-Optimized GeoTIFFs + QML styles, appends per-scene statistics to
``timeseries.csv``, and renders a Markdown report. Re-running with a later
end date is the "refresh with each pass" loop.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .acquisition import AcquisitionError, BandData, read_band, read_stack
from .aoi import AOI
from .bands import (
    asset_key,
    asset_unit,
    assets_for,
    collection_info,
    mask_alias,
)
from .composites import temporal_composite
from .indices import compute, list_indices, required_bands
from .io import ensure_dir, write_cog, write_geojson, write_text
from .preprocessing import (
    apply_mask,
    clear_fraction,
    mask_for_collection,
    to_kelvin,
    to_reflectance,
    valid_fraction,
)
from .qgis import list_styles, write_style_qml
from .signing import SignerSpec, SigningError, resolve_signer
from .stac import (
    Scene,
    asset_scale_offset_from_scene,
    filter_max_cloud,
    latest_per_date,
    search_scenes,
)
from .timeseries import (
    SceneStats,
    merge_records,
    read_csv,
    series_fieldnames,
    summarize,
    write_csv,
    write_json,
    zonal_stats,
)


class MonitorError(RuntimeError):
    """Raised when a monitor run cannot proceed."""


@dataclass
class MonitorConfig:
    """Everything a site monitor needs, serialisable to JSON.

    ``signer`` accepts a callable, a registered strategy name (see
    :mod:`imagery.signing`, e.g. ``"planetary-computer"``), or ``None``.
    Strategy names round-trip through :meth:`to_dict` / :meth:`from_dict`,
    so a JSON config file can drive SAS-protected catalogs.
    """

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
    signer: SignerSpec = None

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
            # Named signer strategies serialise; raw callables cannot.
            "signer": self.signer if isinstance(self.signer, str) else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonitorConfig":
        data = dict(data)
        data["aoi"] = AOI.from_dict(data["aoi"])
        signer = data.get("signer")
        if signer is not None and not isinstance(signer, str):
            raise MonitorError(
                "signer in a JSON config must be a strategy name "
                f"(e.g. 'planetary-computer'), got {type(signer).__name__}"
            )
        return cls(**data)


@dataclass
class MonitorResult:
    config_name: str
    scenes: List[Scene]
    records: List[SceneStats]
    rasters: List[str]
    report_path: str
    timeseries_csv: str
    summary: Dict[str, Any]
    composites: List[str] = field(default_factory=list)


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
        signer=resolve_signer(config.signer),
    )
    # Reflectance-vs-Kelvin routing per alias. Thermal aliases (Landsat
    # tirs1/tirs2) convert DN -> K via to_kelvin with NO [0, 1] clip;
    # reflectance aliases keep to_reflectance. Per-asset scale / offset come
    # from the STAC item's own raster:bands metadata when present, else the
    # bands registry.
    conv: Dict[str, np.ndarray] = {}
    for a, b in stack.items():
        scale, offset = asset_scale_offset_from_scene(scene, a)
        if asset_unit(collection, a) == "kelvin":
            conv[a] = to_kelvin(b.data, scale, offset)
        else:
            conv[a] = to_reflectance(b.data, scale, offset)
    refl = conv

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
    per_index_rasters: Dict[str, List[str]] = {idx: [] for idx in config.indices}
    for i, scene in enumerate(scenes, start=1):
        log(f"processing scene {i}/{len(scenes)}: {scene.id}")
        try:
            records = process_scene(scene, config, out_dir)
        except (MonitorError, AcquisitionError, SigningError) as exc:
            # One bad scene (unreadable asset, expired signature, ...) must
            # not abort the whole refresh; it is logged and skipped.
            log(f"  skipped: {exc}")
            continue
        all_records.extend(records)
        date_tag = scene.datetime[:10]
        for idx in config.indices:
            path = os.path.join(
                out_dir, "rasters", f"{config.name}_{idx}_{date_tag}.tif"
            )
            rasters.append(path)
            per_index_rasters[idx].append(path)

    composites: List[str] = []
    if config.composite:
        composites = _write_composites(out_dir, config, per_index_rasters, log)
        rasters.extend(composites)

    csv_path = os.path.join(out_dir, "timeseries.csv")
    json_path = os.path.join(out_dir, "timeseries.json")
    previous = read_csv(csv_path) if os.path.exists(csv_path) else []
    merged = merge_records(previous, all_records)
    if merged:
        write_csv(merged, csv_path)
    else:
        _write_header_only_csv(csv_path)
    write_json(merged, json_path)
    summary = summarize(merged)
    summary["composites"] = composites
    report_path = os.path.join(out_dir, "report.md")
    write_text(report_path, render_report(config, scenes, summary))

    return MonitorResult(
        config_name=config.name,
        scenes=scenes,
        records=merged,
        rasters=rasters,
        report_path=report_path,
        timeseries_csv=csv_path,
        summary=summary,
        composites=composites,
    )


def _write_header_only_csv(path: str) -> str:
    """Write a valid-but-empty series file so tooling always sees a CSV."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(series_fieldnames())
    return path


def _grid_key(band: BandData) -> Tuple[Tuple[int, int], Tuple[float, ...], str]:
    t = band.transform
    coeffs = (t.a, t.b, t.c, t.d, t.e, t.f) if hasattr(t, "a") else tuple(t)
    return (
        (int(band.data.shape[0]), int(band.data.shape[1])),
        tuple(float(v) for v in coeffs),
        str(band.crs),
    )


def _write_composites(
    out_dir: str,
    config: MonitorConfig,
    per_index_rasters: Dict[str, List[str]],
    log: Callable[[str], None],
) -> List[str]:
    """Median temporal composite per index from the per-scene rasters.

    Only rasters sharing an identical grid are composited together (scenes
    from neighbouring UTM tiles can differ); the largest grid-compatible
    group wins and the rest are logged and skipped.
    """
    paths: List[str] = []
    for index_name, raster_paths in per_index_rasters.items():
        existing = [p for p in raster_paths if os.path.exists(p)]
        if len(existing) < 2:
            log(f"  composite {index_name}: need 2+ scenes, have {len(existing)}")
            continue
        bands = [read_band(p) for p in existing]
        groups: Dict[
            Tuple[Tuple[int, int], Tuple[float, ...], str], List[BandData]
        ] = {}
        for band in bands:
            groups.setdefault(_grid_key(band), []).append(band)
        key = max(groups, key=lambda k: len(groups[k]))
        group = groups[key]
        skipped = len(bands) - len(group)
        if skipped:
            log(
                f"  composite {index_name}: skipped {skipped} scene(s) on "
                "a different grid"
            )
        if len(group) < 2:
            log(f"  composite {index_name}: no grid-compatible pair found")
            continue
        composite = temporal_composite(
            [b.data for b in group], method="median"
        )
        ref = group[0]
        fname = (
            f"{config.name}_{index_name}_composite_"
            f"{config.start}_{config.end}.tif"
        )
        out_path = os.path.join(out_dir, "rasters", fname)
        write_cog(out_path, composite, ref.transform, ref.crs)
        style = index_name if index_name in list_styles() else "ndvi"
        write_style_qml(out_path.replace(".tif", ".qml"), style=style)
        log(f"  composite {index_name}: {fname} from {len(group)} scenes")
        paths.append(out_path)
    return paths


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
    ]
    composites = summary.get("composites") or []
    if composites:
        lines += [
            "",
            "## Temporal composites",
            "",
            "Median composites across the grid-compatible scenes:",
            "",
        ]
        lines += [f"- `{os.path.basename(p)}`" for p in composites]
    lines += [
        "",
        "_Generated by survey-imagery site monitor._",
    ]
    return "\n".join(lines) + "\n"
