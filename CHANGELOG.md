# Changelog

All notable changes to `survey-imagery`. Follows semantic versioning.

## [0.1.2] - 2026-09-25
### Fixed
- **Sentinel-2 L2A offset (data bug):** `sentinel-2-l2a` registry used
  offset `0.0`; current Collection 1 products use scale 0.0001,
  **offset -0.1** (verified against live STAC `raster:bands` metadata).
  Reflectance was off by 0.1 in absolute units — catastrophic for indices.
- **Landsat thermal handled as reflectance (data bug):** the monitor
  applied one scale/offset (0.0000275/-0.2) plus `[0, 1]` clipping to *all*
  assets. Thermal aliases `tirs1`/`tirs2` (ST_B10) are Kelvin and now route
  through the new `preprocessing.to_kelvin()` with scale 0.00341802,
  offset +149 K and no clipping.
### Added
- `bands.asset_scale_offset(collection, alias)` — per-asset override with
  collection-default fallback; `bands.asset_unit(collection, alias)` —
  `"kelvin"` for thermal aliases, `"reflectance"` otherwise.
- `preprocessing.to_kelvin(data, scale, offset)` — NaN-safe DN→K, no clip.
- `stac.asset_scale_offset_from_scene(scene, alias, prefer_metadata=True)` —
  reads per-asset `raster:bands` scale/offset from the STAC item JSON when
  present, else the registry; `Scene.asset_scales` carries the harvested
  metadata through the pipeline.
- Regression tests for the S2 offset, the ST_B10 thermal path, and
  metadata-vs-registry scaling precedence.

## [0.1.1] - 2026-09-23
### Added
- `signing`: named URL-signer strategies (`imagery.signing`). `planetary-computer`
  signs via the anonymous SAS endpoint, handling both the current `{"href": ...}`
  and legacy `{"token": ...}` response shapes; retries 429/5xx with exponential
  backoff; caches signatures until just before their `se=` expiry.
  `resolve_signer()`, `list_signers()`, `clear_sign_cache()`, `SIGNERS` registry.
- `MonitorConfig.signer` now accepts a registered strategy name
  (e.g. `"planetary-computer"`), which round-trips through `.to_dict()` /
  `.from_dict()` and the `monitor` CLI (`--signer` flag also overrides the config).
- Monitor temporal composites: `composite=True` writes a per-index median
  composite COG + QML over the grid-compatible per-scene rasters.
- `timeseries`: `SceneStats.from_dict()`, `read_csv()`, `read_json()`,
  `merge_records()`, `series_fieldnames()` — monitor re-runs merge new records
  into the existing series keyed by `(scene_id, index)`; repeats and reprocessed
  scenes replace their earlier rows instead of duplicating them.
### Fixed
- `MonitorConfig.composite` was previously ignored; it now produces composite
  COGs instead of silently doing nothing.
- Monitor re-runs previously discarded prior history by rewriting
  `timeseries.csv` from scratch; history is now preserved and merged.
### Verified
- Live smoke test against real Sentinel-2 L2A data from Planetary Computer:
  STAC search, windowed AOI read, SCL masking, NDVI, and a 3-scene monitor run
  with composites and series merging, using the built-in `planetary-computer`
  signer.

## [0.1.0] - 2026-09-23
### Added
- Initial release: satellite imagery engine for surveying + remote sensing.
- `aoi`: AOI model (bbox / GeoJSON / WKT), lon/lat bounds, reprojection, buffering.
- `bands`: canonical band aliases, STAC collection registry (Sentinel-2 L2A,
  Landsat C2 L2), sensor cheat-sheet, reflectance scale/offset metadata.
- `stac`: portable STAC API `/search` client with pagination, cloud-cover
  filtering, clearest-per-date deduplication, and sorting.
- `acquisition`: windowed AOI-clipped band reads, multi-resolution stack
  alignment, optional SAS URL signer hook, read-size estimation.
- `preprocessing`: DN to surface reflectance, Sentinel-2 SCL and Landsat
  QA_PIXEL cloud/shadow masks, NaN-honest masking.
- `indices`: NDVI, EVI, SAVI, NDWI, NDMI, NDBI, NBR, BSI with a registry
  declaring each index's band requirements.
- `composites`: NaN-aware median/mean/max/min temporal composites, observation
  counts, best-scene tracking.
- `timeseries`: per-scene zonal statistics over the AOI, CSV/JSON writers,
  series summaries.
- `monitor`: per-pass site monitor pipeline — search, process, Cloud-Optimized
  GeoTIFFs + QML styles, `timeseries.csv`, Markdown report.
- `io`: COG / multiband COG / GeoJSON / text writers.
- `qgis`: generated QML layer styles for every index plus usage notes.
- `interop`: suite interchange grid, duck-typed CRS acceptance
  (survey-geodesy compatible), survey-raster conversion, XYZ sampling for
  pointcloud-style analysis.
- `cli`: `survey-imagery search|monitor|indices|info` command line.
- 75 unit tests (stdlib `unittest`, no test-runner dependency).
