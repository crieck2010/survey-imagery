# survey-imagery

> **Part of [earthwatch-suite](https://github.com/crieck2010/earthwatch-suite)** —
> the remote-sensing project (satellite imagery, change detection, site
> monitoring). Terrestrial surveying lives in
> [survey-suite](https://github.com/crieck2010/survey-suite); the two stay
> compatible through the
> [cross-suite contracts](https://github.com/crieck2010/earthwatch-suite/blob/main/docs/CONTRACTS.md).

A pure-logic **satellite imagery engine** for surveying and remote sensing: STAC scene discovery, windowed AOI-clipped acquisition, cloud masking, spectral indices, temporal composites, per-pass time series, and an automated **site monitor** that refreshes its outputs with every satellite pass.

This is the satellite-data module of a larger surveying + remote sensing suite. It is engine-only on purpose — no UI framework imports anywhere — so it can be imported by scripts, wrapped in a desktop app, driven from the CLI, or (next step) exposed as QGIS Processing algorithms. Every raster product is a Cloud-Optimized GeoTIFF that opens directly in QGIS.

**Design note on data access:** this engine pulls free and open Sentinel-2 / Landsat imagery straight from public STAC catalogs (Microsoft Planetary Computer, Copernicus Data Space, USGS) and processes it locally. Google Earth Engine is deliberately *not* required — its free tier covers research/education/nonprofit use only, while these sources keep commercial and marketable use unencumbered. See `docs/DATA_SOURCES.md`.

## Features

- **AOI model** — define a site once (bbox, GeoJSON, or WKT) and reuse it on every pass; bounds always normalised to lon/lat for catalog queries
- **STAC discovery** — portable `/search` client with pagination, cloud-cover filtering, clearest-per-date deduplication, and sorting; no STAC client library needed
- **Band aliases** — algorithms speak canonical names (`nir`, `red`, `swir1`) instead of mission asset keys (`B08`, `SR_B5`), so one NDVI routine runs on Sentinel-2, Landsat, or any future collection
- **Acquisition** — windowed reads clipped to the AOI (never whole scenes), multi-resolution stack alignment to a common grid, named URL-signer strategies for SAS-protected catalogs (`planetary-computer` works straight from a JSON config)
- **Preprocessing** — DN→reflectance scaling, Sentinel-2 SCL and Landsat QA_PIXEL cloud/shadow masks, NaN-honest masking
- **Spectral indices** — NDVI, EVI, SAVI, NDWI, NDMI, NDBI, NBR, BSI via a registry that declares each index's band requirements
- **Composites** — NaN-aware median/mean/max/min temporal composites plus per-pixel observation counts and best-scene tracking
- **Time series** — per-scene zonal statistics inside the AOI → CSV/JSON for dashboards and QGIS joins
- **Site monitor** — one JSON config drives search → process → COG + QML style → `timeseries.csv` → Markdown report; re-runs merge new scenes into the same series (no duplicates) and can write per-index temporal composites
- **QGIS support** — generated `.qml` layer styles for every index, plus usage notes
- **Suite interop** — interchange grid dict shared with `survey-raster`, duck-typed CRS acceptance from `survey-geodesy`, XYZ sampling for `survey-pointcloud`-style analysis

## Installation

Requires Python 3.9+.

```bash
# From source
pip install .

# Editable install (for development)
pip install -e .

# Optional: SAS URL signing for Microsoft Planetary Computer
pip install planetary-computer
```

Runtime dependencies are intentionally light: `numpy`, `rasterio`, `requests`.

## Quickstart

```python
from imagery import from_bbox, search_scenes, read_stack, compute
from imagery.preprocessing import to_reflectance, mask_for_collection, apply_mask
from imagery.bands import reflectance_scale_offset

# 1. Define the site once
aoi = from_bbox(-70.12, 43.65, -70.02, 43.72, name="quarry-north")

# 2. Find recent Sentinel-2 scenes (Planetary Computer STAC)
scenes = search_scenes(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    ["sentinel-2-l2a"], aoi, "2026-08-01", "2026-09-01",
    max_cloud_cover=30.0,
)
scene = scenes[-1]

# 3. Read red/NIR/SCL clipped to the AOI (windowed — never the whole tile)
assets = {a: scene.href(a) for a in ("red", "nir", "scl")}
stack = read_stack(assets, ["red", "nir", "scl"], aoi=aoi,
                   collection="sentinel-2-l2a")

# 4. Reflectance + cloud mask + NDVI
scale, offset = reflectance_scale_offset("sentinel-2-l2a")
red = to_reflectance(stack["red"].data, scale, offset)
nir = to_reflectance(stack["nir"].data, scale, offset)
clear = mask_for_collection("sentinel-2-l2a", stack["scl"].data)
ndvi = compute("ndvi", {"nir": apply_mask(nir, clear),
                         "red": apply_mask(red, clear)})
print("mean NDVI:", float(ndvi[ndvi == ndvi].mean()))
```

### Site monitor (per-pass refresh)

```bash
# Write a config (see examples/monitor_config.json), then:
survey-imagery monitor --config examples/monitor_config.json

# For SAS-protected catalogs like Planetary Computer, either put
# "signer": "planetary-computer" in the config or pass it on the CLI:
survey-imagery monitor --config examples/monitor_config.json \
    --signer planetary-computer
```

Re-running a monitor with a later end date appends new scenes to the same
`timeseries.csv` — records are keyed by `(scene_id, index)`, so repeats and
reprocessed scenes replace their earlier rows instead of duplicating them.
Set `"composite": true` in the config to also write a per-index median
temporal composite (`<site>_<index>_composite_<start>_<end>.tif`) over the
grid-compatible scenes.

```bash
# Search scenes without processing:
survey-imagery search --bbox -70.12 43.65 -70.02 43.72 \
    --collection sentinel-2-l2a --start 2026-08-01 --end 2026-09-01 \
    --max-cloud 30

# Inspect what's available:
survey-imagery info
survey-imagery indices
```

Each monitor run writes to the output folder:

```
output/
├── aoi.geojson          # the monitored footprint
├── timeseries.csv       # per-scene zonal stats (QGIS-joinable on scene_id)
├── timeseries.json
├── report.md            # human-readable run summary
└── rasters/
    ├── <site>_ndvi_2026-08-15.tif   # Cloud-Optimized GeoTIFF
    ├── <site>_ndvi_2026-08-15.qml   # QGIS style sidecar
    ├── <site>_ndvi_composite_2026-06-01_2026-09-01.tif  # if "composite": true
    └── ...
```

## QGIS workflow

1. Drag a product `.tif` into the Layers panel (COGs stream fine).
2. Layer Properties → Symbology → Style → Load Style → pick the matching `.qml`.
3. Load `timeseries.csv` via Add Delimited Text Layer, or join it to `aoi.geojson` on `scene_id`.

Run `survey-imagery info` for the full QGIS quickstart notes.

## Interoperability

- **survey-raster**: `imagery.interop.as_grid` / `check_grid` produce the shared `{data, transform, crs, nodata}` interchange dict; `to_survey_raster` / `from_survey_raster` convert to the sibling `Raster` model when installed.
- **survey-geodesy**: any CRS object with `to_wkt()`/`to_proj4()` (or a plain string) is accepted anywhere a CRS is expected via `crs_from_any`.
- **survey-pointcloud**: `grid_to_pointcloud_xyz` samples an index raster to XYZ points for small-AOI analysis.
- **QGIS**: COG + QML + GeoJSON outputs need no conversion.
- **Next module**: `survey-change` (planned) will consume this engine's index stacks and time series for change detection.

See `docs/INTEROPERABILITY.md` and `docs/ARCHITECTURE.md`.

## Honest limits

- Sentinel-2 is 10 m, Landsat 30 m: this complements field survey (monitoring, change detection between visits) — it does not replace it.
- Optical passes are weather-dependent; the monitor's cloud filters and temporal composites exist for exactly this reason. SAR (Sentinel-1) is the all-weather answer and is on the roadmap.
- `target_resolution_m` assumes metre-based scene CRSs (Sentinel-2 UTM, Landsat UTM); pass nothing and the native grid is used.

## Project structure

```
src/imagery/
├── __init__.py       # public API
├── aoi.py            # AOI model (bbox/GeoJSON/WKT)
├── bands.py          # band aliases, collection + sensor registry
├── stac.py           # STAC API search client
├── acquisition.py    # windowed, AOI-clipped band reads + stack alignment
├── preprocessing.py  # reflectance scaling, SCL/QA_PIXEL cloud masks
├── indices.py        # spectral index registry (NDVI, EVI, …)
├── composites.py     # temporal compositing
├── signing.py        # named URL-signer strategies (Planetary Computer SAS)
├── timeseries.py     # zonal stats → CSV/JSON series
├── monitor.py        # per-pass site monitor pipeline
├── io.py             # Cloud-Optimized GeoTIFF / GeoJSON / report writers
├── qgis.py           # QML style generation
├── interop.py        # adapters for the survey suite
└── cli.py            # `survey-imagery` command line
docs/
├── ARCHITECTURE.md
├── INTEROPERABILITY.md
├── DATA_SOURCES.md
└── API.md
examples/
└── monitor_config.json
tests/
└── test_imagery.py   # 75 tests, stdlib unittest
```

## Versioning

Follows semantic versioning; see `CHANGELOG.md`. Current release: **0.1.0**.

## License

MIT — see `LICENSE`.
