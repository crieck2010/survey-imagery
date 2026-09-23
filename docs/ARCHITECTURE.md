# Architecture

## Layering

```
                    ┌─────────────┐
                    │     CLI     │  survey-imagery search/monitor/indices/info
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │        monitor.py       │  site-monitor pipeline (orchestration)
              └─┬────┬────┬────┬────┬──┘
                │    │    │    │    │
        ┌───────┘    │    │    │    └────────┐
        ▼            ▼    ▼    ▼             ▼
     stac.py  acquisition  indices/   timeseries    io.py / qgis.py
              .py      preprocessing/
                          composites.py
        │            │    │                │
        └────────────┴────┴────────────────┘
                         ▼
              ┌─────────────────────┐
              │  aoi.py, bands.py   │  shared vocabulary (no dependencies)
              │  interop.py         │  suite adapters (duck-typed, lazy)
              └─────────────────────┘
```

Rules:

- **No UI-framework imports anywhere.** The engine is importable by scripts, desktop apps, and (planned) QGIS Processing providers alike.
- **Dependencies point downward.** `monitor.py` orchestrates; `stac.py`, `acquisition.py`, `indices.py` never import each other. `aoi.py` and `bands.py` are imported by everything and import nothing in-package.
- **NaN is the nodata contract.** Every array flowing through the engine is float32 with NaN for nodata/masked/cloud. Indices propagate NaN; a clouded pixel can never become a fake value.
- **Canonical band aliases** (`nir`, `red`, …) decouple algorithms from missions. Adding a sensor means adding rows to `bands.COLLECTIONS`, not rewriting algorithms.

## Scaling

Satellite scenes are big (a Sentinel-2 tile is ~1 GB uncompressed). The engine is built so AOI jobs stay small:

1. **Windowed reads.** `acquisition.read_band` computes the AOI window in the scene CRS and reads only that rectangle. A 1 km² site at 10 m is ~40 KB per band, not 1 GB.
2. **Stack alignment is lazy-friendly.** Bands are read at native resolution, then aligned to the reference grid with a single reprojection each. `target_resolution_m` lets a monitor standardise the grid across passes.
3. **Chunking hook.** For AOIs too large for memory, process per-window: `read_band` accepts any AOI, so callers can tile the site polygon (e.g. with `imagery.aoi` sub-boxes) and reduce per tile. `estimate_read_size_mb` predicts a job's footprint before it runs.
4. **Temporal compositing is streaming-friendly.** `temporal_composite` takes a sequence; callers can accumulate per-scene index rasters from disk rather than holding the whole stack.
5. **Dask is deliberately absent.** The dependency budget (`numpy`, `rasterio`, `requests`) covers real workloads; a Dask-backed reader can be added behind the same `read_band` signature later without breaking callers.

## Error handling

- `AOIError`, `BandError`, `STACError`, `AcquisitionError`, `MonitorError` are all `ValueError`/`RuntimeError` subclasses with human-readable messages naming the offending value.
- The monitor **skips** scenes that fail processing (logged) rather than aborting the run — one corrupt asset must not lose the whole pass.
- Network failures in `stac.search` raise `STACError` immediately; retries and backoff are the caller's policy (the CLI surfaces the error plainly).

## Testing

75 tests under `tests/`, stdlib `unittest` only (no runner dependency):

```bash
python -m unittest discover -s tests
```

Synthetic rasters are built with `rasterio` in-memory/temp files; STAC HTTP is faked at the `requests` boundary; the monitor runs end-to-end against local GeoTIFF "scenes". No network access is needed for the suite.

## Adding a sensor

1. Add the collection to `bands.COLLECTIONS` (asset map, resolutions, scale/offset, mask alias).
2. Add a `mask_for_collection` branch in `preprocessing.py` if its QA layer is new.
3. Add the sensor to `bands.SENSORS` with revisit cadence.
4. Document it in `docs/DATA_SOURCES.md`.

No algorithm code changes. That is the point of the alias layer.
