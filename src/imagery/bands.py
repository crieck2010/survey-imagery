"""Band aliases, STAC collection registry, and sensor metadata.

Working with canonical band *aliases* (``nir``, ``red``, ``swir1``) instead of
mission-specific asset names (``B08``, ``SR_B5``) keeps every downstream
algorithm mission-agnostic: an NDVI routine written once runs on Sentinel-2,
Landsat 8/9, or any future collection added here.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

# Canonical alias -> human description. Aliases not present in a given
# collection simply have no asset mapping there.
BAND_ALIASES: Dict[str, str] = {
    "coastal": "Coastal aerosol (~443 nm)",
    "blue": "Blue (~490 nm)",
    "green": "Green (~560 nm)",
    "red": "Red (~665 nm)",
    "rededge1": "Red edge 1 (~705 nm, Sentinel-2)",
    "rededge2": "Red edge 2 (~740 nm, Sentinel-2)",
    "rededge3": "Red edge 3 (~783 nm, Sentinel-2)",
    "nir": "Near infrared (~842 nm)",
    "narrow_nir": "Narrow NIR (~865 nm, Sentinel-2)",
    "cirrus": "Cirrus (~1375-1373 nm)",
    "swir1": "Short-wave infrared 1 (~1610 nm)",
    "swir2": "Short-wave infrared 2 (~2190 nm)",
    "tirs1": "Thermal infrared 1 (~10.9 um, Landsat)",
    "tirs2": "Thermal infrared 2 (~12.0 um, Landsat)",
    "qa_pixel": "Landsat Collection 2 QA_PIXEL bitmask",
    "scl": "Sentinel-2 L2A scene classification layer",
    "visual": "True-colour rendered preview",
}

# STAC API endpoints known to serve the collections below.
STAC_APIS: Dict[str, str] = {
    "planetary-computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "copernicus-dataspace": "https://stac.dataspace.copernicus.eu/v1",
    "usgs-landsatlook": "https://landsatlook.usgs.gov/stac-server",
}

# Per-collection metadata. ``assets`` maps canonical alias -> STAC asset key.
# ``scale``/``offset`` convert stored digital numbers to surface reflectance.
COLLECTIONS: Dict[str, Dict] = {
    "sentinel-2-l2a": {
        "title": "Sentinel-2 MSI Level-2A (surface reflectance)",
        "mission": "Sentinel-2A/2B",
        "assets": {
            "coastal": "B01",
            "blue": "B02",
            "green": "B03",
            "red": "B04",
            "rededge1": "B05",
            "rededge2": "B06",
            "rededge3": "B07",
            "nir": "B08",
            "narrow_nir": "B8A",
            "cirrus": "B10",
            "swir1": "B11",
            "swir2": "B12",
            "scl": "SCL",
            "visual": "visual",
        },
        "resolution_m": {
            "B01": 60, "B02": 10, "B03": 10, "B04": 10, "B05": 20,
            "B06": 20, "B07": 20, "B08": 10, "B8A": 20, "B10": 60,
            "B11": 20, "B12": 20, "SCL": 20,
        },
        "scale": 0.0001,
        "offset": 0.0,
        "cloud_cover_field": "eo:cloud_cover",
        "mask_alias": "scl",
    },
    "landsat-c2-l2": {
        "title": "Landsat Collection 2 Level-2 (surface reflectance)",
        "mission": "Landsat 8/9",
        "assets": {
            "coastal": "SR_B1",
            "blue": "SR_B2",
            "green": "SR_B3",
            "red": "SR_B4",
            "nir": "SR_B5",
            "swir1": "SR_B6",
            "swir2": "SR_B7",
            "tirs1": "ST_B10",
            "qa_pixel": "QA_PIXEL",
            "visual": "visual",
        },
        "resolution_m": {
            "SR_B1": 30, "SR_B2": 30, "SR_B3": 30, "SR_B4": 30,
            "SR_B5": 30, "SR_B6": 30, "SR_B7": 30, "ST_B10": 100,
            "QA_PIXEL": 30,
        },
        "scale": 0.0000275,
        "offset": -0.2,
        "cloud_cover_field": "eo:cloud_cover",
        "mask_alias": "qa_pixel",
    },
}

# Sensor cheat-sheet: revisit cadence and ground resolution, used by the
# monitor planner and the docs. Values are approximate nadir figures.
SENSORS: Dict[str, Dict] = {
    "sentinel-2": {
        "collections": ["sentinel-2-l2a"],
        "revisit_days": 5,
        "resolution_m": "10/20/60",
        "swath_km": 290,
        "notes": "Free and open via Copernicus. Best cadence for per-pass monitoring.",
    },
    "landsat-8-9": {
        "collections": ["landsat-c2-l2"],
        "revisit_days": 8,
        "resolution_m": "30 (15 pan)",
        "swath_km": 185,
        "notes": "Free and open via USGS. Long archive (1980s+) for baseline change work.",
    },
}


class BandError(ValueError):
    """Raised for unknown collections, aliases, or assets."""


def list_collections() -> List[str]:
    return sorted(COLLECTIONS)


def collection_info(collection: str) -> Dict:
    try:
        return COLLECTIONS[collection]
    except KeyError as exc:
        raise BandError(
            f"unknown collection {collection!r}; known: {', '.join(list_collections())}"
        ) from exc


def list_aliases(collection: Optional[str] = None) -> List[str]:
    if collection is None:
        return sorted(BAND_ALIASES)
    info = collection_info(collection)
    return sorted(info["assets"])


def asset_key(collection: str, alias: str) -> str:
    """STAC asset key for a canonical band alias in a collection."""
    info = collection_info(collection)
    try:
        return info["assets"][alias]
    except KeyError as exc:
        raise BandError(
            f"collection {collection!r} has no asset for alias {alias!r}"
        ) from exc


def assets_for(collection: str, aliases: Sequence[str]) -> Dict[str, str]:
    """Map each requested alias to its STAC asset key (alias -> asset key)."""
    return {alias: asset_key(collection, alias) for alias in aliases}


def reflectance_scale_offset(collection: str) -> Tuple[float, float]:
    info = collection_info(collection)
    return (float(info["scale"]), float(info["offset"]))


def native_resolution_m(collection: str, alias: str) -> Optional[float]:
    info = collection_info(collection)
    res = info.get("resolution_m", {}).get(asset_key(collection, alias))
    return float(res) if res is not None else None


def mask_alias(collection: str) -> Optional[str]:
    """Canonical alias of the collection's cloud-mask asset, if any."""
    return collection_info(collection).get("mask_alias")


def describe_alias(alias: str) -> str:
    return BAND_ALIASES.get(alias, "unknown band alias")


def collection_cloud_cover_field(collection: str) -> str:
    return collection_info(collection).get("cloud_cover_field", "eo:cloud_cover")
