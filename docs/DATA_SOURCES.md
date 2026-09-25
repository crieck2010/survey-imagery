# Data sources

All imagery used by this engine is **free and open**, processed locally. No Earth Engine account, no API key, no commercial-use restriction.

## Catalogs (STAC APIs)

| Name | URL | Auth | Notes |
|---|---|---|---|
| Microsoft Planetary Computer | `https://planetarycomputer.microsoft.com/api/stac/v1` | None for search; SAS signature needed for asset reads | Default in the monitor. Use the built-in signer strategy: `"signer": "planetary-computer"` in the monitor config (or `--signer planetary-computer` on the CLI). The signer handles the SAS endpoint's response shape, retries 429/5xx with backoff, and caches signatures until they expire. |
| Copernicus Data Space | `https://stac.dataspace.copernicus.eu/v1` | None for search; token needed for downloads | The authoritative Sentinel source; register for a free token at dataspace.copernicus.eu. |
| USGS LandsatLook | `https://landsatlook.usgs.gov/stac-server` | None | Landsat Collection 2; simplest anonymous access. |

Pass any of these as `--api` / `api_url`. The `Scene.assets` hrefs are used verbatim, so provider quirks (like SAS signing) are handled by the `signer` — a callable, or a named strategy from `imagery.signing` (`list_signers()`), serialisable in a JSON config — rather than baked into the engine.

## Collections

| Collection | Mission | Resolution | Revisit | Archive |
|---|---|---|---|---|
| `sentinel-2-l2a` | Sentinel-2A/2B MSI | 10/20/60 m | ~5 days | 2015– |
| `landsat-c2-l2` | Landsat 8/9 OLI/TIRS | 30 m (15 m pan) | ~8 days combined | 2013– (program back to the 1980s) |

Both are **Level-2 surface reflectance** — atmospherically corrected, analysis-ready. The engine applies each collection's documented scale/offset (`bands.reflectance_scale_offset`), with per-asset overrides for thermal bands and Collection-1 offsets (`bands.asset_scale_offset`). Where a STAC item carries its own `raster:bands` scale/offset, the item metadata wins (`stac.asset_scale_offset_from_scene`).

## Why not Google Earth Engine?

GEE is excellent, but its free tier is limited to research, education, and nonprofit use — commercial use requires a paid license. This engine's "marketable outputs" goal is incompatible with that constraint, so it goes straight to the source archives instead. Same pixels, no licensing trap, and the processing runs locally where the rest of the survey suite lives.

## Honest limits for surveying work

- **Resolution.** 10 m (Sentinel-2) / 30 m (Landsat) pixels monitor *sites*, not *points*. Use this for disturbance tracking, vegetation/water change, and between-visit monitoring — not for anything needing survey-grade positions.
- **Weather.** Optical satellites cannot see through cloud. The monitor's `max_cloud_cover` filter, per-scene cloud masks, and temporal composites mitigate this; persistent overcast (a Maine November) will still leave gaps. Sentinel-1 SAR is the all-weather complement and is on the roadmap.
- **Revisit ≠ usable pass.** A 5-day revisit with 60 % cloud cover is not a 5-day monitoring cadence. `latest_per_date` + `filter_max_cloud` encode this reality; the report's scene table shows what actually got used.
