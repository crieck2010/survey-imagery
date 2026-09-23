"""Scene acquisition: windowed, AOI-clipped band reads.

Reads only the pixels a job needs. Each band is opened from its STAC asset
href, the AOI is reprojected into the scene CRS, and a single rasterio window
covering the AOI is read — a 10 m Sentinel-2 tile is ~1 GB uncompressed, so
whole-scene reads are never attempted. Multi-resolution stacks are aligned to
one common grid before indices are computed.

``signer`` is an optional callable mapping ``href -> href``. Providers such as
Microsoft Planetary Computer serve short-lived SAS URLs; pass
``planetary_computer.sign`` (from the optional ``planetary-computer`` package)
without adding a hard dependency here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import rasterio
    from rasterio import windows as _rio_windows
    from rasterio.enums import Resampling
    from rasterio.warp import reproject as _rio_reproject, transform_geom
except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
    raise ImportError(
        "survey-imagery acquisition requires the 'rasterio' package"
    ) from exc

from .aoi import AOI
from .bands import asset_key, native_resolution_m


class AcquisitionError(RuntimeError):
    """Raised when a band asset cannot be read."""


@dataclass
class BandData:
    """One band clipped to an AOI: float32 array, affine transform, CRS."""

    data: np.ndarray  # 2D float32, NaN = nodata/masked
    transform: Tuple[float, float, float, float, float, float]
    crs: str
    resolution_m: Optional[float] = None

    @property
    def shape(self) -> Tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    @property
    def width_m(self) -> float:
        return abs(self.transform[1]) * self.shape[1]

    @property
    def height_m(self) -> float:
        return abs(self.transform[5]) * self.shape[0]


def _affine_tuple(transform) -> Tuple[float, float, float, float, float, float]:
    return (transform.a, transform.b, transform.c, transform.d, transform.e, transform.f)


def read_band(
    href: str,
    aoi: Optional[AOI] = None,
    band_index: int = 1,
    resampling: str = "bilinear",
    signer: Optional[Callable[[str], str]] = None,
    masked: bool = True,
) -> BandData:
    """Read one band asset, optionally clipped to an AOI window.

    Returns float32 data with NaN for nodata. ``resampling`` is one of
    ``nearest`` / ``bilinear`` / ``cubic`` / ``average`` / ``mode``.
    """
    if signer is not None:
        href = signer(href)
    resampling_enum = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
        "average": Resampling.average,
        "mode": Resampling.mode,
    }.get(resampling)
    if resampling_enum is None:
        raise AcquisitionError(f"unknown resampling {resampling!r}")

    try:
        dataset = rasterio.open(href)
    except Exception as exc:
        raise AcquisitionError(f"cannot open band asset {href!r}: {exc}") from exc

    with dataset as src:
        window = None
        out_transform = src.transform
        if aoi is not None:
            geom = aoi.geometry
            if str(src.crs).upper() != str(aoi.crs).upper():
                geom = transform_geom(aoi.crs, src.crs, geom)
            bounds = _rio_windows.from_bounds(
                *_geometry_bounds(geom),
                transform=src.transform,
            )
            window = bounds.round_offsets().round_lengths()
            out_transform = src.window_transform(window)
        try:
            array = src.read(
                band_index,
                window=window,
                resampling=resampling_enum,
                masked=masked,
            )
        except Exception as exc:
            raise AcquisitionError(
                f"failed reading band {band_index} from {href!r}: {exc}"
            ) from exc

        if isinstance(array, np.ma.MaskedArray):
            data = array.filled(np.nan).astype(np.float32)
        else:
            data = np.asarray(array, dtype=np.float32)
            nodata = src.nodata
            if nodata is not None and not (isinstance(nodata, float) and np.isnan(nodata)):
                data[data == nodata] = np.nan

        # Mask pixels outside the AOI polygon when one was supplied.
        if aoi is not None:
            from rasterio.features import geometry_mask

            geom = aoi.geometry
            if str(src.crs).upper() != str(aoi.crs).upper():
                geom = transform_geom(aoi.crs, src.crs, geom)
            outside = geometry_mask(
                [geom],
                out_shape=data.shape,
                transform=out_transform,
                invert=False,
            )
            data[outside] = np.nan

        return BandData(
            data=data,
            transform=_affine_tuple(out_transform),
            crs=str(src.crs),
        )


def _geometry_bounds(geometry: dict) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []

    def walk(coords) -> None:
        if isinstance(coords, (list, tuple)) and coords and isinstance(
            coords[0], (int, float)
        ):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
        elif isinstance(coords, (list, tuple)):
            for part in coords:
                walk(part)

    walk(geometry["coordinates"])
    return (min(xs), min(ys), max(xs), max(ys))


def read_stack(
    assets: Dict[str, str],
    aliases: Sequence[str],
    aoi: Optional[AOI] = None,
    target_resolution_m: Optional[float] = None,
    collection: Optional[str] = None,
    signer: Optional[Callable[[str], str]] = None,
) -> Dict[str, BandData]:
    """Read several bands and align them to one common grid.

    The reference grid is the finest native resolution among the requested
    bands (or ``target_resolution_m`` when given); coarser bands are
    upsampled with bilinear resampling. All returned arrays share the same
    shape, transform, and CRS.
    """
    bands: Dict[str, BandData] = {}
    for alias in aliases:
        if alias not in assets:
            raise AcquisitionError(f"no asset href supplied for alias {alias!r}")
        bands[alias] = read_band(assets[alias], aoi=aoi, signer=signer)

    # Pick the reference grid.
    ref_alias = aliases[0]
    ref_res = target_resolution_m
    if ref_res is None and collection is not None:
        native = [
            native_resolution_m(collection, a)
            for a in aliases
            if native_resolution_m(collection, a)
        ]
        if native:
            ref_res = min(native)
    if ref_res is None:
        ref_res = abs(bands[ref_alias].transform[1])

    ref = bands[ref_alias]
    current_res = abs(ref.transform[1])
    if target_resolution_m is not None and not np.isclose(current_res, target_resolution_m):
        ref = _resample_band(ref, target_resolution_m)
        bands[ref_alias] = ref

    aligned: Dict[str, BandData] = {ref_alias: ref}
    for alias in aliases[1:]:
        band = bands[alias]
        band_res = abs(band.transform[1])
        if band.shape == ref.shape and np.isclose(band_res, abs(ref.transform[1])):
            aligned[alias] = band
        else:
            aligned[alias] = _reproject_to(band, ref)
    return aligned


def _resample_band(band: BandData, resolution_m: float) -> BandData:
    from rasterio.transform import Affine

    scale = abs(band.transform[1]) / resolution_m
    new_width = max(1, int(round(band.shape[1] * scale)))
    new_height = max(1, int(round(band.shape[0] * scale)))
    # Keep north-up orientation: rebuild from the origin with the new pixel size.
    new_transform = Affine(
        resolution_m if band.transform[1] > 0 else -resolution_m,
        0.0,
        band.transform[2],
        0.0,
        -resolution_m if band.transform[5] < 0 else resolution_m,
        band.transform[5],
    )
    out = np.empty((new_height, new_width), dtype=np.float32)
    _rio_reproject(
        band.data,
        out,
        src_transform=_tuple_to_affine(band.transform),
        src_crs=band.crs,
        dst_transform=new_transform,
        dst_crs=band.crs,
        resampling=Resampling.bilinear,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return BandData(
        data=out,
        transform=_affine_tuple(new_transform),
        crs=band.crs,
        resolution_m=resolution_m,
    )


def _reproject_to(band: BandData, ref: BandData) -> BandData:
    out = np.empty(ref.shape, dtype=np.float32)
    _rio_reproject(
        band.data,
        out,
        src_transform=_tuple_to_affine(band.transform),
        src_crs=band.crs,
        dst_transform=_tuple_to_affine(ref.transform),
        dst_crs=ref.crs,
        resampling=Resampling.bilinear,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return BandData(
        data=out, transform=ref.transform, crs=ref.crs, resolution_m=ref.resolution_m
    )


def _tuple_to_affine(t: Tuple[float, ...]):
    from rasterio.transform import Affine

    return Affine(t[0], t[1], t[2], t[3], t[4], t[5])


def estimate_read_size_mb(
    aoi: AOI,
    scene_crs: str,
    resolution_m: float,
    bands: int = 1,
    dtype_bytes: int = 4,
) -> float:
    """Rough AOI-clipped read size in MB, for planning large jobs.

    Reprojects the AOI bounds into the scene CRS, computes pixel dimensions at
    ``resolution_m``, and multiplies out. Used by the monitor to warn before
    very large reads.
    """
    geom = aoi.geometry
    if str(scene_crs).upper() != str(aoi.crs).upper():
        geom = transform_geom(aoi.crs, scene_crs, geom)
    west, south, east, north = _geometry_bounds(geom)
    width_px = abs(east - west) / resolution_m
    height_px = abs(north - south) / resolution_m
    return width_px * height_px * bands * dtype_bytes / 1e6
