# Interoperability

`survey-imagery` is the satellite-data module of the surveying + remote sensing suite. It works standalone, but it is designed to slot into pipelines with its siblings.

## The interchange grid

Band and index rasters move between modules as plain dicts:

```python
{
    "data": np.ndarray,      # 2D float32, NaN = nodata
    "transform": (a, b, c, d, e, f),  # GDAL-style affine tuple
    "crs": "EPSG:32618",     # any string CRS
    "nodata": float("nan"),
}
```

Build and validate with `imagery.interop.as_grid` / `check_grid`. The dict is JSON-unfriendly by design (it holds arrays) — it is the *in-memory* contract; the *on-disk* contract is the Cloud-Optimized GeoTIFF.

## Sibling adapters

| Sibling | Adapter | Notes |
|---|---|---|
| `survey-raster` | `to_survey_raster(grid)`, `from_survey_raster(obj)` | Converts to the sibling `Raster` model (nodata sentinel ↔ NaN). Sibling imported lazily; `ImportError` carries an install hint when absent. |
| `survey-geodesy` | `crs_from_any(crs)` | Accepts geodesy CRS objects (`to_wkt`/`to_proj4`/`to_epsg`), `pyproj.CRS`, or plain strings anywhere a CRS is expected. |
| `survey-pointcloud` | `grid_to_pointcloud_xyz(grid)` | Samples an index raster to `(x, y, value)` pixel-centre points for small-AOI analysis. Full scenes stay rasters. |
| `survey-cogo` / `survey-levels` / `survey-adjust` / `survey-gnss` | via QGIS/GeoJSON | Field observations join imagery products spatially in QGIS; no direct API coupling needed. |
| `survey-suite` (meta) | dependency entry | Add `survey-imagery` to the meta-package's module list to surface it in the suite CLI/desktop app. |

## QGIS

- Every raster product is a **Cloud-Optimized GeoTIFF**: drag into QGIS, streams over HTTP, overviews included.
- Every index raster ships a **`.qml` style sidecar** (`imagery.qgis.write_style_qml`) with a sensible classification ramp.
- `aoi.geojson` + `timeseries.csv` join on `scene_id` for per-pass attribute mapping.

## Forward compatibility: survey-change (planned)

The next module, change detection, is designed to consume this engine's outputs without modification:

- index stacks (`{alias: BandData}`) feed bi-temporal differencing,
- `timeseries.csv` feeds breakpoint/statistics-based alarming,
- the `Scene` model already carries `datetime` + `cloud_cover` for pass selection.

When `survey-change` lands, `survey-imagery` needs no changes — that was a design requirement, not an accident.
