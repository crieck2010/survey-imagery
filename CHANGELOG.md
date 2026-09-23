# Changelog

All notable changes to `survey-imagery`. Follows semantic versioning.

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
