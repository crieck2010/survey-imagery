# Changelog

All notable changes to `survey-imagery`. Follows semantic versioning.

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
