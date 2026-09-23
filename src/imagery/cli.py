"""Command-line interface for survey-imagery.

Subcommands:
    search    Find STAC scenes for an AOI and date window.
    monitor   Run a per-pass site monitor from a JSON config file.
    indices   List the spectral indices in the registry.
    info      Show collections, sensors, STAC endpoints, and QGIS notes.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .aoi import from_bbox
from .bands import COLLECTIONS, SENSORS, STAC_APIS, list_aliases, list_collections
from .indices import describe_index, list_indices
from .monitor import MonitorConfig, run_monitor
from .qgis import list_styles, qgis_usage_notes
from .signing import list_signers
from .stac import latest_per_date, search_scenes


def _add_aoi_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                        required=True, help="AOI bounding box (lon/lat)")
    parser.add_argument("--name", default="", help="AOI name")


def _add_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api", default=list(STAC_APIS.values())[0],
                        help="STAC API base URL")
    parser.add_argument("--collection", action="append", dest="collections",
                        default=[], help="STAC collection (repeatable)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--max-cloud", type=float, default=None,
                        help="Maximum eo:cloud_cover percent")
    parser.add_argument("--limit", type=int, default=100, help="Page size")


def cmd_search(args: argparse.Namespace) -> int:
    aoi = from_bbox(*args.bbox, name=args.name)
    collections = args.collections or list_collections()
    scenes = []
    for collection in collections:
        scenes.extend(
            search_scenes(args.api, [collection], aoi, args.start, args.end,
                          max_cloud_cover=args.max_cloud)
        )
    scenes = latest_per_date(scenes)
    print(f"found {len(scenes)} scenes")
    for scene in scenes:
        cloud = f"{scene.cloud_cover:.1f}%" if scene.cloud_cover is not None else "n/a"
        print(f"{scene.datetime[:10]}  {scene.collection}  cloud={cloud}  {scene.id}")
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    with open(args.config, encoding="utf-8") as handle:
        data = json.load(handle)
    config = MonitorConfig.from_dict(data)
    if args.signer:
        config.signer = args.signer
    result = run_monitor(config, progress=print)
    print(f"scenes: {len(result.scenes)}")
    print(f"records: {len(result.records)}")
    print(f"rasters: {len(result.rasters)}")
    if result.composites:
        print(f"composites: {len(result.composites)}")
    print(f"report: {result.report_path}")
    print(f"timeseries: {result.timeseries_csv}")
    return 0


def cmd_indices(_args: argparse.Namespace) -> int:
    for name in list_indices():
        print(describe_index(name))
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    print(f"survey-imagery {__version__}")
    print("\nSTAC endpoints:")
    for name, url in STAC_APIS.items():
        print(f"  {name}: {url}")
    print("\nCollections:")
    for collection in list_collections():
        info = COLLECTIONS[collection]
        aliases = ", ".join(list_aliases(collection)[:8])
        print(f"  {collection}: {info['title']} [{aliases}, ...]")
    print("\nSensors:")
    for name, sensor in SENSORS.items():
        print(f"  {name}: revisit ~{sensor['revisit_days']}d, {sensor['resolution_m']}")
    print(f"\nIndices: {', '.join(list_indices())}")
    print(f"\nQGIS styles: {', '.join(list_styles())}")
    print()
    print(qgis_usage_notes())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="survey-imagery",
        description="Satellite imagery engine for surveying: STAC discovery, "
                    "indices, composites, and per-pass site monitoring.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Find STAC scenes for an AOI")
    _add_aoi_args(p_search)
    _add_search_args(p_search)
    p_search.set_defaults(func=cmd_search)

    p_monitor = sub.add_parser("monitor", help="Run a site monitor from a JSON config")
    p_monitor.add_argument("--config", required=True, help="Monitor JSON config file")
    p_monitor.add_argument(
        "--signer",
        default=None,
        choices=list_signers(),
        help="URL signer strategy (overrides the config file); "
        "needed for SAS-protected catalogs such as Planetary Computer",
    )
    p_monitor.set_defaults(func=cmd_monitor)

    p_indices = sub.add_parser("indices", help="List spectral indices")
    p_indices.set_defaults(func=cmd_indices)

    p_info = sub.add_parser("info", help="Show collections, sensors, and QGIS notes")
    p_info.set_defaults(func=cmd_info)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI reports errors plainly
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
