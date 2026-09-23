"""Area-of-interest (AOI) model for satellite imagery workflows.

An AOI is a GeoJSON geometry plus a CRS identifier. Everything downstream
(search, acquisition, masking, statistics) works from this one object, so a
site is defined once and reused on every satellite pass.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

try:  # rasterio is a hard dependency of this package; guard anyway for clarity
    from rasterio.warp import transform_geom
except Exception:  # pragma: no cover - import-time fallback only
    transform_geom = None  # type: ignore[assignment]


class AOIError(ValueError):
    """Raised when an AOI cannot be built or is invalid."""


_VALID_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}


def _validate_geometry(geometry: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(geometry, dict):
        raise AOIError("geometry must be a GeoJSON geometry dict")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype not in _VALID_TYPES:
        raise AOIError(f"unsupported geometry type: {gtype!r}")
    if not isinstance(coords, (list, tuple)) or len(coords) == 0:
        raise AOIError("geometry has no coordinates")
    return geometry


def _validate_lonlat(lon: float, lat: float) -> None:
    if not (isinstance(lon, (int, float)) and isinstance(lat, (int, float))):
        raise AOIError("longitude/latitude must be numbers")
    if math.isnan(lon) or math.isnan(lat):
        raise AOIError("longitude/latitude must not be NaN")
    if not -180.0 <= lon <= 180.0:
        raise AOIError(f"longitude {lon} out of range [-180, 180]")
    if not -90.0 <= lat <= 90.0:
        raise AOIError(f"latitude {lat} out of range [-90, 90]")


@dataclass
class AOI:
    """A site footprint: GeoJSON geometry + CRS identifier.

    The geometry is stored as given; :meth:`bounds_lonlat` always returns
    WGS84 bounds for STAC queries regardless of the stored CRS.
    """

    geometry: Dict[str, Any]
    crs: str = "EPSG:4326"
    name: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.geometry = _validate_geometry(self.geometry)
        if not isinstance(self.crs, str) or not self.crs:
            raise AOIError("crs must be a non-empty string")

    def bounds_lonlat(self) -> Tuple[float, float, float, float]:
        """(west, south, east, north) in EPSG:4326."""
        if self.crs.upper().replace(" ", "") in ("EPSG:4326", "WGS84"):
            geom = self.geometry
        else:
            if transform_geom is None:  # pragma: no cover
                raise AOIError("rasterio is required to reproject AOI bounds")
            geom = transform_geom(self.crs, "EPSG:4326", self.geometry)
        return _geometry_bounds(geom)

    def to_geojson_feature(self) -> Dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": {"name": self.name, "crs": self.crs, **self.properties},
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "geometry": self.geometry,
            "crs": self.crs,
            "name": self.name,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AOI":
        return cls(
            geometry=data["geometry"],
            crs=data.get("crs", "EPSG:4326"),
            name=data.get("name", ""),
            properties=data.get("properties", {}),
        )


def _geometry_bounds(geometry: Dict[str, Any]) -> Tuple[float, float, float, float]:
    xs: list = []
    ys: list = []

    def walk(coords: Any) -> None:
        if isinstance(coords, (list, tuple)) and coords and isinstance(
            coords[0], (int, float)
        ):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
        elif isinstance(coords, (list, tuple)):
            for part in coords:
                walk(part)

    walk(geometry["coordinates"])
    if not xs:
        raise AOIError("geometry has no coordinates")
    return (min(xs), min(ys), max(xs), max(ys))


def from_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
    crs: str = "EPSG:4326",
    name: str = "",
) -> AOI:
    """Build an AOI from bounding-box corners."""
    _validate_lonlat(west, south)
    _validate_lonlat(east, north)
    if east <= west:
        raise AOIError("east must be greater than west")
    if north <= south:
        raise AOIError("north must be greater than south")
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }
    return AOI(geometry=geometry, crs=crs, name=name)


def _as_geometry(obj: Dict[str, Any]) -> Dict[str, Any]:
    otype = obj.get("type")
    if otype == "Feature":
        geom = obj.get("geometry")
        if not isinstance(geom, dict):
            raise AOIError("Feature has no geometry")
        return geom
    if otype == "FeatureCollection":
        features = obj.get("features") or []
        if not features:
            raise AOIError("FeatureCollection has no features")
        geoms = []
        for feat in features:
            if isinstance(feat, dict) and isinstance(feat.get("geometry"), dict):
                geoms.append(feat["geometry"])
        if not geoms:
            raise AOIError("FeatureCollection has no usable geometries")
        if len(geoms) == 1:
            return geoms[0]
        # Merge into a MultiPolygon when possible; otherwise keep first.
        polys = [g for g in geoms if g.get("type") in ("Polygon", "MultiPolygon")]
        if polys and len(polys) == len(geoms):
            coords = []
            for g in polys:
                if g["type"] == "Polygon":
                    coords.append(g["coordinates"])
                else:
                    coords.extend(g["coordinates"])
            return {"type": "MultiPolygon", "coordinates": coords}
        return geoms[0]
    if otype in _VALID_TYPES:
        return obj
    raise AOIError(f"cannot interpret object of type {otype!r} as an AOI")


def from_geojson(
    obj: Dict[str, Any], crs: str = "EPSG:4326", name: str = ""
) -> AOI:
    """Build an AOI from a GeoJSON geometry, Feature, or FeatureCollection."""
    if not isinstance(obj, dict):
        raise AOIError("GeoJSON object must be a dict")
    geometry = _as_geometry(obj)
    if name == "" and obj.get("type") == "Feature":
        props = obj.get("properties") or {}
        if isinstance(props, dict) and isinstance(props.get("name"), str):
            name = props["name"]
    return AOI(geometry=geometry, crs=crs, name=name)


def from_geojson_string(text: str, crs: str = "EPSG:4326", name: str = "") -> AOI:
    """Build an AOI from a GeoJSON string."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AOIError(f"invalid GeoJSON: {exc}") from exc
    return from_geojson(obj, crs=crs, name=name)


def from_wkt(wkt: str, crs: str = "EPSG:4326", name: str = "") -> AOI:
    """Build an AOI from a WKT string (requires shapely if installed)."""
    try:
        from shapely import wkt as _wkt
        from shapely.geometry import mapping
    except ImportError as exc:
        raise AOIError(
            "from_wkt requires the optional 'shapely' package"
        ) from exc
    try:
        geom = _wkt.loads(wkt)
    except Exception as exc:
        raise AOIError(f"invalid WKT: {exc}") from exc
    return AOI(geometry=mapping(geom), crs=crs, name=name)


def reproject(aoi: AOI, crs: str) -> AOI:
    """Return a copy of the AOI expressed in another CRS."""
    if transform_geom is None:  # pragma: no cover
        raise AOIError("rasterio is required to reproject an AOI")
    geom = transform_geom(aoi.crs, crs, aoi.geometry)
    return AOI(
        geometry=geom, crs=crs, name=aoi.name, properties=dict(aoi.properties)
    )


def buffer_degrees(aoi: AOI, degrees: float) -> AOI:
    """Expand a lon/lat AOI's bounding box by ``degrees`` on each side."""
    if degrees < 0:
        raise AOIError("buffer degrees must be non-negative")
    west, south, east, north = aoi.bounds_lonlat()
    return from_bbox(
        max(-180.0, west - degrees),
        max(-90.0, south - degrees),
        min(180.0, east + degrees),
        min(90.0, north + degrees),
        name=aoi.name,
    )
