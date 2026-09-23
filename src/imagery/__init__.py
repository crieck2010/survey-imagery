"""survey-imagery: satellite imagery engine for surveying.

STAC scene discovery, windowed AOI-clipped acquisition, cloud masking,
spectral indices, temporal composites, per-pass time series, and a site
monitor pipeline — with QGIS-ready Cloud-Optimized GeoTIFF outputs and
adapters for the rest of the surveying + remote sensing suite.
"""

from .acquisition import BandData, read_band, read_stack
from .aoi import AOI, buffer_degrees, from_bbox, from_geojson, from_geojson_string
from .bands import (
    BAND_ALIASES,
    COLLECTIONS,
    SENSORS,
    STAC_APIS,
    asset_key,
    assets_for,
    collection_info,
    list_aliases,
    list_collections,
)
from .composites import (
    best_scene_index,
    coverage_fraction,
    temporal_composite,
    valid_pixel_count,
)
from .indices import INDEX_REGISTRY, compute, describe_index, list_indices, required_bands
from .interop import (
    as_grid,
    check_grid,
    crs_from_any,
    describe_interop,
    from_survey_raster,
    grid_to_pointcloud_xyz,
    to_survey_raster,
)
from .io import ensure_dir, write_cog, write_geojson, write_multiband_cog, write_text
from .monitor import MonitorConfig, MonitorResult, render_report, run_monitor
from .preprocessing import (
    apply_mask,
    clear_fraction,
    cloud_mask_qa_pixel,
    cloud_mask_scl,
    mask_for_collection,
    to_reflectance,
    valid_fraction,
)
from .qgis import list_styles, qgis_usage_notes, style_qml, write_style_qml
from .stac import (
    Scene,
    filter_max_cloud,
    latest_per_date,
    search,
    search_scenes,
    sort_scenes,
)
from .timeseries import SceneStats, summarize, write_csv, write_json, zonal_stats

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # aoi
    "AOI", "buffer_degrees", "from_bbox", "from_geojson", "from_geojson_string",
    # bands
    "BAND_ALIASES", "COLLECTIONS", "SENSORS", "STAC_APIS",
    "asset_key", "assets_for", "collection_info", "list_aliases", "list_collections",
    # acquisition
    "BandData", "read_band", "read_stack",
    # preprocessing
    "apply_mask", "clear_fraction", "cloud_mask_qa_pixel", "cloud_mask_scl",
    "mask_for_collection", "to_reflectance", "valid_fraction",
    # indices
    "INDEX_REGISTRY", "compute", "describe_index", "list_indices", "required_bands",
    # composites
    "best_scene_index", "coverage_fraction", "temporal_composite", "valid_pixel_count",
    # timeseries
    "SceneStats", "summarize", "write_csv", "write_json", "zonal_stats",
    # stac
    "Scene", "filter_max_cloud", "latest_per_date", "search", "search_scenes",
    "sort_scenes",
    # io
    "ensure_dir", "write_cog", "write_geojson", "write_multiband_cog", "write_text",
    # qgis
    "list_styles", "qgis_usage_notes", "style_qml", "write_style_qml",
    # monitor
    "MonitorConfig", "MonitorResult", "render_report", "run_monitor",
    # interop
    "as_grid", "check_grid", "crs_from_any", "describe_interop",
    "from_survey_raster", "grid_to_pointcloud_xyz", "to_survey_raster",
]
