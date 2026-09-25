# API reference

Public API is re-exported from `imagery` (`src/imagery/__init__.py`). Import from the top level: `from imagery import from_bbox, compute, run_monitor`.

## `imagery.aoi` — areas of interest

- `AOI(geometry, crs="EPSG:4326", name="", properties={})` — GeoJSON geometry + CRS. `.bounds_lonlat()`, `.to_geojson_feature()`, `.to_dict()` / `.from_dict()`.
- `from_bbox(west, south, east, north, crs="EPSG:4326", name="")`
- `from_geojson(obj, crs="EPSG:4326", name="")` — geometry, Feature, or FeatureCollection.
- `from_geojson_string(text, ...)` / `from_wkt(wkt, ...)` (WKT needs optional `shapely`).
- `reproject(aoi, crs)`, `buffer_degrees(aoi, degrees)`.
- `AOIError` on invalid input.

## `imagery.bands` — aliases, collections, sensors

- `BAND_ALIASES`, `COLLECTIONS`, `SENSORS`, `STAC_APIS` — registries.
- `list_collections()`, `list_aliases(collection=None)`, `collection_info(name)`.
- `asset_key(collection, alias)`, `assets_for(collection, aliases)`.
- `reflectance_scale_offset(collection)` → `(scale, offset)`.
- `asset_scale_offset(collection, alias)` → per-asset `(scale, offset)` honoring
  thermal overrides; `asset_unit(collection, alias)` → `"kelvin"`/`"reflectance"`.
- `native_resolution_m(collection, alias)`, `mask_alias(collection)`, `describe_alias(alias)`.
- `BandError` on unknown collections/aliases.

## `imagery.stac` — scene discovery

- `Scene` — `id`, `collection`, `datetime`, `cloud_cover`, `bbox`, `assets` (alias → href), `asset_scales` (alias → per-asset `(scale, offset)` harvested from `raster:bands`); `.href(alias)`, `.has_aliases([...])`, `.to_dict()`.
- `search_scenes(api_url, collections, aoi, start, end, max_cloud_cover=None, limit=100, require_aliases=None)` → sorted `Scene` list.
- `search(api_url, payload, ...)` — raw payload execution with `next`-link pagination.
- `build_search_payload(collections, bbox_lonlat, datetime_range, max_cloud_cover, limit)`.
- `sort_scenes(scenes, by="datetime"|"cloud_cover")`, `filter_max_cloud(scenes, max)`, `latest_per_date(scenes)`.
- `STACError` on HTTP/parse failures.

## `imagery.acquisition` — band reads

- `BandData` — `.data` (2D float32, NaN = nodata), `.transform`, `.crs`, `.shape`, `.width_m`/`.height_m`.
- `read_band(href, aoi=None, band_index=1, resampling="bilinear", signer=None)` — windowed AOI-clipped read.
- `read_stack(assets, aliases, aoi=None, target_resolution_m=None, collection=None, signer=None)` → `{alias: BandData}` on one grid.
- `estimate_read_size_mb(aoi, scene_crs, resolution_m, bands=1)` — plan large jobs.
- `AcquisitionError` on unreadable assets.

## `imagery.preprocessing`

- `to_reflectance(data, scale=0.0001, offset=-0.1)` — DN → [0, 1] surface reflectance (Sentinel-2 Collection 1 defaults).
- `to_kelvin(data, scale=0.00341802, offset=149.0)` — DN → Kelvin for thermal
  aliases (`tirs1`/`tirs2`); no [0, 1] clipping.
- `cloud_mask_scl(scl, clear_classes=SCL_CLEAR)` / `cloud_mask_qa_pixel(qa)` → boolean clear-sky masks; `mask_for_collection(collection, mask_band)` dispatches.
- `apply_mask(data, clear_mask)`, `clear_fraction(mask)`, `valid_fraction(data)`.
- SCL class constants (`SCL_VEGETATION`, `SCL_CLOUD_HIGH`, …).

## `imagery.indices`

- Functions: `ndvi`, `evi`, `savi`, `ndwi`, `ndmi`, `ndbi`, `nbr`, `bsi` — all NaN-propagating.
- `INDEX_REGISTRY`, `list_indices()`, `required_bands(name)`, `describe_index(name)`.
- `compute(name, {alias: array}, **kwargs)` — registry dispatch with shape checks.

## `imagery.composites`

- `temporal_composite(arrays, method="median"|"mean"|"max"|"min")` — NaN-aware stack collapse.
- `valid_pixel_count(arrays)`, `coverage_fraction(arrays)`, `best_scene_index(arrays)`.

## `imagery.signing` — SAS signers

- `planetary_computer_signer(href)` — sign one href via Planetary Computer's anonymous SAS endpoint. Handles both response shapes (`{"href": ...}` and legacy `{"token": ...}`), retries 429/5xx with exponential backoff, and caches signed URLs until just before their `se=` expiry. `SigningError` on failure.
- `resolve_signer(spec)` — `None` / callable / registered strategy name → callable. Raises `SigningError` for unknown names.
- `list_signers()` — registered strategy names (`["planetary-computer"]`).
- `clear_sign_cache()` — empty the SAS signature cache (tests).
- `SIGNERS` registry — add your own strategy: `SIGNERS["my-catalog"] = my_signer`.

## `imagery.timeseries`

- `SceneStats` dataclass — per-scene zonal stats + `to_dict()` / `from_dict()`.
- `zonal_stats(data, transform, crs, aoi, scene_id="", datetime="", index="", cloud_cover=None)`.
- `write_csv(records, path)`, `write_json(records, path)`, `summarize(records)`.
- `read_csv(path)`, `read_json(path)` — load previously written series files.
- `merge_records(previous, new)` — union keyed by `(scene_id, index)`; new records replace old ones with the same key; result sorted by `(datetime, index, scene_id)`.
- `series_fieldnames()` — canonical CSV column order.

## `imagery.monitor` — site monitor

- `MonitorConfig(name, aoi, collections, indices, output_dir, api_url=..., start=..., end=..., max_cloud_cover=40.0, target_resolution_m=None, composite=False, signer=None)` — `start` defaults to 90 days before `end` (today). `signer` accepts a callable, a registered strategy name (`"planetary-computer"`), or `None`. `.to_dict()` / `.from_dict()` round-trip strategy names (raw callables can't be JSON-serialised and are stored as `None`).
- `run_monitor(config, progress=None)` → `MonitorResult(scenes, records, rasters, report_path, timeseries_csv, summary, composites)`. Existing `timeseries.csv` is loaded, merged with new records (repeats replace, nothing duplicates), and rewritten. With `composite=True`, per-index median composites are written from the grid-compatible per-scene rasters.
- `process_scene(scene, config, out_dir)` — single-scene pipeline, usable standalone.
- `render_report(config, scenes, summary)` → Markdown.
- `MonitorError` when nothing is found or config is invalid.

## `imagery.io`

- `write_cog(path, data, transform, crs, nodata=nan)` — Cloud-Optimized GeoTIFF.
- `write_multiband_cog(path, {name: array}, transform, crs)` — band descriptions set.
- `write_geojson(path, obj)`, `write_text(path, text)`, `ensure_dir(path)`.

## `imagery.qgis`

- `style_qml(style="ndvi", band=1)`, `write_style_qml(path, style="ndvi")`, `list_styles()`, `qgis_usage_notes()`.

## `imagery.interop`

- `as_grid(data, transform, crs, nodata=nan)`, `check_grid(grid)` — the suite interchange format.
- `crs_from_any(crs)` — duck-typed CRS → string (survey-geodesy compatible).
- `to_survey_raster(grid)` / `from_survey_raster(obj)` — survey-raster conversion (lazy import).
- `grid_to_pointcloud_xyz(grid, band_value_name="value")` — XYZ sampling.
- `describe_interop()`.

## CLI — `survey-imagery`

- `search --bbox W S E N --collection … --start … --end … [--api …] [--max-cloud …] [--limit …]`
- `monitor --config config.json [--signer planetary-computer]` — `--signer` overrides the config file
- `indices`, `info`, `--version`
