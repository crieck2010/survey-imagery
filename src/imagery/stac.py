"""STAC API search client for satellite scene discovery.

Speaks the STAC API ``/search`` endpoint with plain HTTP (``requests``), so no
STAC-specific client library is required. Responses are normalised into
:class:`Scene` objects keyed by canonical band alias, hiding the differences
between providers (Planetary Computer, Copernicus Data Space, USGS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from .aoi import AOI
from .bands import (
    BandError,
    asset_key,
    collection_cloud_cover_field,
    collection_info,
)

DEFAULT_TIMEOUT = 60
DEFAULT_LIMIT = 100


class STACError(RuntimeError):
    """Raised when a STAC API request fails or returns unusable data."""


@dataclass
class Scene:
    """One satellite acquisition normalised across providers."""

    id: str
    collection: str
    datetime: str
    cloud_cover: Optional[float]
    bbox: Sequence[float]
    assets: Dict[str, str]  # canonical alias -> href
    properties: Dict[str, Any] = field(default_factory=dict)

    def href(self, alias: str) -> str:
        try:
            return self.assets[alias]
        except KeyError as exc:
            raise BandError(
                f"scene {self.id!r} has no asset for alias {alias!r}"
            ) from exc

    def has_aliases(self, aliases: Sequence[str]) -> bool:
        return all(a in self.assets for a in aliases)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "collection": self.collection,
            "datetime": self.datetime,
            "cloud_cover": self.cloud_cover,
            "bbox": list(self.bbox),
            "assets": dict(self.assets),
            "properties": dict(self.properties),
        }


def build_search_payload(
    collections: Sequence[str],
    bbox_lonlat: Sequence[float],
    datetime_range: str,
    max_cloud_cover: Optional[float] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Build a STAC API ``/search`` POST body."""
    if len(bbox_lonlat) != 4:
        raise STACError("bbox must be (west, south, east, north)")
    for collection in collections:
        collection_info(collection)  # validates the name early
    payload: Dict[str, Any] = {
        "collections": list(collections),
        "bbox": [float(v) for v in bbox_lonlat],
        "datetime": datetime_range,
        "limit": int(limit),
    }
    if max_cloud_cover is not None:
        # STAC API filter extension; most catalogues also honour it as a
        # plain query parameter. We send the portable query form.
        payload["query"] = {
            "eo:cloud_cover": {"lt": float(max_cloud_cover)},
        }
    return payload


def _item_to_scene(item: Dict[str, Any]) -> Scene:
    collection = item.get("collection", "")
    info = collection_info(collection) if collection else {}
    asset_map: Dict[str, str] = info.get("assets", {}) if info else {}
    assets: Dict[str, str] = {}
    for alias, key in asset_map.items():
        entry = (item.get("assets") or {}).get(key) or {}
        href = entry.get("href")
        if href:
            assets[alias] = href
    props = item.get("properties") or {}
    cloud_field = (
        collection_cloud_cover_field(collection) if collection else "eo:cloud_cover"
    )
    cloud = props.get(cloud_field)
    try:
        cloud_cover = float(cloud) if cloud is not None else None
    except (TypeError, ValueError):
        cloud_cover = None
    return Scene(
        id=str(item.get("id", "")),
        collection=collection,
        datetime=str(props.get("datetime", "")),
        cloud_cover=cloud_cover,
        bbox=item.get("bbox") or [],
        assets=assets,
        properties=dict(props),
    )


def _post_search(
    url: str, payload: Dict[str, Any], timeout: int
) -> Dict[str, Any]:
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise STACError(f"STAC search request failed: {exc}") from exc
    if response.status_code >= 400:
        raise STACError(
            f"STAC search failed with HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise STACError(f"STAC search returned invalid JSON: {exc}") from exc


def search(
    api_url: str,
    payload: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    max_pages: int = 10,
) -> List[Scene]:
    """Execute a search payload, following ``next`` pagination links."""
    api_url = api_url.rstrip("/")
    scenes: List[Scene] = []
    body = dict(payload)
    for _ in range(max_pages):
        data = _post_search(f"{api_url}/search", body, timeout)
        for item in data.get("features") or []:
            try:
                scenes.append(_item_to_scene(item))
            except BandError:
                continue  # skip items from unknown collections
        next_link = next(
            (
                link
                for link in data.get("links") or []
                if link.get("rel") == "next"
            ),
            None,
        )
        if not next_link:
            break
        # STAC ``next`` links may be GET (href) or POST (body+merge).
        if next_link.get("method", "GET").upper() == "POST":
            body = next_link.get("body") or {}
            merge = next_link.get("merge", False)
            if merge:
                body = {**payload, **body}
        else:
            # GET-style next links carry a full URL; fetch directly.
            try:
                resp = requests.get(next_link["href"], timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                raise STACError(f"failed to follow STAC next link: {exc}") from exc
            for item in data.get("features") or []:
                try:
                    scenes.append(_item_to_scene(item))
                except BandError:
                    continue
            next_link = next(
                (
                    link
                    for link in data.get("links") or []
                    if link.get("rel") == "next"
                ),
                None,
            )
            if not next_link:
                break
    return scenes


def search_scenes(
    api_url: str,
    collections: Sequence[str],
    aoi: AOI,
    start: str,
    end: str,
    max_cloud_cover: Optional[float] = None,
    limit: int = DEFAULT_LIMIT,
    timeout: int = DEFAULT_TIMEOUT,
    require_aliases: Optional[Sequence[str]] = None,
) -> List[Scene]:
    """Find scenes intersecting ``aoi`` between two ISO-8601 dates/times.

    ``start``/``end`` accept ``YYYY-MM-DD`` or full ISO-8601 datetimes.
    Scenes missing any alias in ``require_aliases`` are dropped.
    """
    datetime_range = f"{start}/{end}"
    payload = build_search_payload(
        collections,
        aoi.bounds_lonlat(),
        datetime_range,
        max_cloud_cover=max_cloud_cover,
        limit=limit,
    )
    scenes = search(api_url, payload, timeout=timeout)
    if require_aliases:
        scenes = [s for s in scenes if s.has_aliases(require_aliases)]
    return sort_scenes(scenes, by="datetime")


def sort_scenes(scenes: List[Scene], by: str = "datetime") -> List[Scene]:
    if by == "datetime":
        return sorted(scenes, key=lambda s: s.datetime)
    if by == "cloud_cover":
        return sorted(
            scenes,
            key=lambda s: s.cloud_cover if s.cloud_cover is not None else 999.0,
        )
    raise STACError(f"unknown sort key {by!r}; use 'datetime' or 'cloud_cover'")


def filter_max_cloud(scenes: List[Scene], max_cloud_cover: float) -> List[Scene]:
    """Keep scenes at or below a cloud-cover threshold (None counts as pass)."""
    return [
        s
        for s in scenes
        if s.cloud_cover is None or s.cloud_cover <= max_cloud_cover
    ]


def latest_per_date(scenes: List[Scene]) -> List[Scene]:
    """Deduplicate to one scene per calendar date (clearest wins)."""
    best: Dict[str, Scene] = {}
    for scene in scenes:
        day = scene.datetime[:10]
        current = best.get(day)
        if current is None:
            best[day] = scene
            continue
        cur_cloud = current.cloud_cover if current.cloud_cover is not None else 999.0
        new_cloud = scene.cloud_cover if scene.cloud_cover is not None else 999.0
        if new_cloud < cur_cloud:
            best[day] = scene
    return sort_scenes(list(best.values()), by="datetime")
