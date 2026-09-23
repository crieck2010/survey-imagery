"""Unit tests for survey-imagery. Run with: python -m unittest discover -s tests"""
import csv
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import requests

import imagery
from imagery import (
    AOI,
    BandData,
    MonitorConfig,
    Scene,
    SceneStats,
    from_bbox,
    from_geojson,
)
from imagery import aoi as aoi_mod
from imagery import bands as bands_mod
from imagery import composites as composites_mod
from imagery import indices as indices_mod
from imagery import interop as interop_mod
from imagery import io as io_mod
from imagery import monitor as monitor_mod
from imagery import preprocessing as preprocessing_mod
from imagery import qgis as qgis_mod
from imagery import signing as signing_mod
from imagery import stac as stac_mod
from imagery import timeseries as timeseries_mod
from imagery.acquisition import estimate_read_size_mb, read_band, read_stack
from imagery.cli import main as cli_main


def _temp_tif(data, transform, crs="EPSG:4326", dtype=None, nodata=None):
    import rasterio
    from rasterio.transform import Affine

    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    tmp.close()
    arr = np.asarray(data)
    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": dtype or str(arr.dtype),
        "crs": crs,
        "transform": Affine(*transform),
        "tiled": True,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(tmp.name, "w", **profile) as dst:
        dst.write(arr, 1)
    return tmp.name


class TestAOI(unittest.TestCase):
    def test_from_bbox(self):
        a = from_bbox(-70.0, 43.0, -69.0, 44.0, name="test-site")
        self.assertEqual(a.bounds_lonlat(), (-70.0, 43.0, -69.0, 44.0))
        self.assertEqual(a.name, "test-site")

    def test_from_bbox_invalid(self):
        with self.assertRaises(aoi_mod.AOIError):
            from_bbox(-69.0, 43.0, -70.0, 44.0)  # east < west
        with self.assertRaises(aoi_mod.AOIError):
            from_bbox(-70.0, 44.0, -69.0, 43.0)  # north < south
        with self.assertRaises(aoi_mod.AOIError):
            from_bbox(-200.0, 43.0, -69.0, 44.0)  # lon out of range

    def test_from_geojson_feature(self):
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "properties": {"name": "poly"},
        }
        a = from_geojson(feat)
        self.assertEqual(a.name, "poly")
        self.assertEqual(a.bounds_lonlat(), (0.0, 0.0, 1.0, 1.0))

    def test_from_geojson_invalid(self):
        with self.assertRaises(aoi_mod.AOIError):
            from_geojson({"type": "Feature", "geometry": None})
        with self.assertRaises(aoi_mod.AOIError):
            from_geojson({"type": "Nope"})

    def test_reproject_bounds(self):
        # UTM 18N square; bounds_lonlat must come back in degrees.
        a = AOI(
            geometry={
                "type": "Polygon",
                "coordinates": [[
                    [500000, 4500000], [501000, 4500000],
                    [501000, 4501000], [500000, 4501000],
                    [500000, 4500000],
                ]],
            },
            crs="EPSG:32618",
        )
        w, s, e, n = a.bounds_lonlat()
        self.assertLess(w, e)
        self.assertLess(s, n)
        self.assertTrue(-80 < w < -60 and 40 < s < 42)

    def test_dict_roundtrip(self):
        a = from_bbox(-70, 43, -69, 44, name="rt")
        b = AOI.from_dict(a.to_dict())
        self.assertEqual(b.bounds_lonlat(), a.bounds_lonlat())
        self.assertEqual(b.name, "rt")

    def test_buffer_degrees(self):
        a = from_bbox(-70, 43, -69, 44)
        b = aoi_mod.buffer_degrees(a, 0.5)
        self.assertEqual(b.bounds_lonlat(), (-70.5, 42.5, -68.5, 44.5))
        with self.assertRaises(aoi_mod.AOIError):
            aoi_mod.buffer_degrees(a, -1.0)


class TestBands(unittest.TestCase):
    def test_collections_known(self):
        cols = bands_mod.list_collections()
        self.assertIn("sentinel-2-l2a", cols)
        self.assertIn("landsat-c2-l2", cols)

    def test_asset_keys(self):
        self.assertEqual(bands_mod.asset_key("sentinel-2-l2a", "nir"), "B08")
        self.assertEqual(bands_mod.asset_key("landsat-c2-l2", "nir"), "SR_B5")
        self.assertEqual(bands_mod.asset_key("landsat-c2-l2", "qa_pixel"), "QA_PIXEL")

    def test_unknown_collection(self):
        with self.assertRaises(bands_mod.BandError):
            bands_mod.asset_key("nope-1", "nir")

    def test_unknown_alias(self):
        with self.assertRaises(bands_mod.BandError):
            bands_mod.asset_key("sentinel-2-l2a", "tirs1")  # S2 has no thermal

    def test_scale_offset(self):
        scale, offset = bands_mod.reflectance_scale_offset("sentinel-2-l2a")
        self.assertAlmostEqual(scale, 0.0001)
        s2, o2 = bands_mod.reflectance_scale_offset("landsat-c2-l2")
        self.assertAlmostEqual(s2, 0.0000275)
        self.assertAlmostEqual(o2, -0.2)

    def test_mask_alias(self):
        self.assertEqual(bands_mod.mask_alias("sentinel-2-l2a"), "scl")
        self.assertEqual(bands_mod.mask_alias("landsat-c2-l2"), "qa_pixel")

    def test_native_resolution(self):
        self.assertEqual(bands_mod.native_resolution_m("sentinel-2-l2a", "nir"), 10.0)
        self.assertEqual(bands_mod.native_resolution_m("landsat-c2-l2", "red"), 30.0)


class TestIndices(unittest.TestCase):
    def test_ndvi_math(self):
        nir = np.array([[0.6, 0.5]], dtype=np.float32)
        red = np.array([[0.2, 0.2]], dtype=np.float32)
        out = indices_mod.ndvi(nir, red)
        self.assertAlmostEqual(float(out[0, 0]), 0.5, places=5)
        self.assertAlmostEqual(float(out[0, 1]), 0.428571, places=5)

    def test_nan_propagation(self):
        nir = np.array([[np.nan, 0.5]], dtype=np.float32)
        red = np.array([[0.2, 0.2]], dtype=np.float32)
        out = indices_mod.ndvi(nir, red)
        self.assertTrue(np.isnan(out[0, 0]))
        self.assertFalse(np.isnan(out[0, 1]))

    def test_zero_denominator(self):
        z = np.zeros((2, 2), dtype=np.float32)
        out = indices_mod.ndvi(z, z)
        self.assertTrue(np.isnan(out).all())

    def test_registry_compute(self):
        bands = {
            "nir": np.full((2, 2), 0.6, dtype=np.float32),
            "red": np.full((2, 2), 0.2, dtype=np.float32),
        }
        out = indices_mod.compute("ndvi", bands)
        self.assertTrue(np.allclose(out, 0.5))
        with self.assertRaises(ValueError):
            indices_mod.compute("ndvi", {"nir": bands["nir"]})  # missing red
        with self.assertRaises(ValueError):
            indices_mod.compute("nope", bands)

    def test_shape_mismatch(self):
        with self.assertRaises(ValueError):
            indices_mod.compute(
                "ndvi",
                {"nir": np.zeros((2, 2)), "red": np.zeros((3, 3))},
            )

    def test_all_registered(self):
        for name in indices_mod.list_indices():
            spec = indices_mod.INDEX_REGISTRY[name]
            bands = {
                b: np.full((2, 2), 0.4, dtype=np.float32) for b in spec.bands
            }
            out = indices_mod.compute(name, bands)
            self.assertEqual(out.shape, (2, 2))
            self.assertEqual(out.dtype, np.float32)

    def test_evi_known_value(self):
        nir = np.array([[0.6]], dtype=np.float32)
        red = np.array([[0.2]], dtype=np.float32)
        blue = np.array([[0.1]], dtype=np.float32)
        out = indices_mod.evi(nir, red, blue)
        expected = 2.5 * (0.6 - 0.2) / (0.6 + 6 * 0.2 - 7.5 * 0.1 + 1.0)
        self.assertAlmostEqual(float(out[0, 0]), expected, places=5)


class TestPreprocessing(unittest.TestCase):
    def test_to_reflectance(self):
        dn = np.array([[10000, 20000, 0]], dtype=np.float32)
        out = preprocessing_mod.to_reflectance(dn, scale=0.0001, offset=0.0)
        self.assertAlmostEqual(float(out[0, 0]), 1.0)
        self.assertAlmostEqual(float(out[0, 1]), 1.0)  # clipped
        self.assertAlmostEqual(float(out[0, 2]), 0.0)

    def test_to_reflectance_nan_stays(self):
        dn = np.array([[np.nan]], dtype=np.float32)
        out = preprocessing_mod.to_reflectance(dn)
        self.assertTrue(np.isnan(out[0, 0]))

    def test_cloud_mask_scl(self):
        scl = np.array([[4, 5, 6, 7, 3, 8, 9, 10, 11, 0, 1, 2]], dtype=np.uint8)
        mask = preprocessing_mod.cloud_mask_scl(scl)
        self.assertEqual(
            mask[0].tolist(),
            [True, True, True, True, False, False, False, False,
             False, False, False, False],
        )

    def test_cloud_mask_qa_pixel(self):
        qa = np.array([[64, 72, 0, 64 + 16]], dtype=np.uint16)  # clear, cloud, fill, shadow
        mask = preprocessing_mod.cloud_mask_qa_pixel(qa)
        self.assertEqual(mask[0].tolist(), [True, False, False, False])

    def test_mask_dispatch(self):
        scl = np.array([[4]], dtype=np.uint8)
        self.assertTrue(
            preprocessing_mod.mask_for_collection("sentinel-2-l2a", scl)[0, 0]
        )
        with self.assertRaises(ValueError):
            preprocessing_mod.mask_for_collection("nope-1", scl)

    def test_apply_mask(self):
        data = np.ones((2, 2), dtype=np.float32)
        mask = np.array([[True, False], [True, True]])
        out = preprocessing_mod.apply_mask(data, mask)
        self.assertTrue(np.isnan(out[0, 1]))
        self.assertEqual(float(out[0, 0]), 1.0)
        with self.assertRaises(ValueError):
            preprocessing_mod.apply_mask(data, np.ones((3, 3), dtype=bool))

    def test_clear_fraction(self):
        mask = np.array([[True, False], [True, True]])
        self.assertAlmostEqual(preprocessing_mod.clear_fraction(mask), 0.75)


class TestComposites(unittest.TestCase):
    def test_median_ignores_nan(self):
        a = np.array([[1.0, np.nan]], dtype=np.float32)
        b = np.array([[3.0, 4.0]], dtype=np.float32)
        out = composites_mod.temporal_composite([a, b], method="median")
        self.assertAlmostEqual(float(out[0, 0]), 2.0)
        self.assertAlmostEqual(float(out[0, 1]), 4.0)

    def test_all_nan_stays_nan(self):
        a = np.full((2, 2), np.nan, dtype=np.float32)
        out = composites_mod.temporal_composite([a, a], method="mean")
        self.assertTrue(np.isnan(out).all())

    def test_methods(self):
        a = np.array([[1.0]], dtype=np.float32)
        b = np.array([[3.0]], dtype=np.float32)
        self.assertAlmostEqual(
            float(composites_mod.temporal_composite([a, b], method="max")[0, 0]), 3.0)
        self.assertAlmostEqual(
            float(composites_mod.temporal_composite([a, b], method="min")[0, 0]), 1.0)

    def test_bad_method(self):
        with self.assertRaises(ValueError):
            composites_mod.temporal_composite([np.zeros((2, 2))], method="mode-ish")

    def test_shape_mismatch(self):
        with self.assertRaises(ValueError):
            composites_mod.temporal_composite([np.zeros((2, 2)), np.zeros((3, 3))])

    def test_valid_pixel_count(self):
        a = np.array([[1.0, np.nan]], dtype=np.float32)
        b = np.array([[np.nan, np.nan]], dtype=np.float32)
        counts = composites_mod.valid_pixel_count([a, b])
        self.assertEqual(counts.tolist(), [[1, 0]])
        self.assertAlmostEqual(composites_mod.coverage_fraction([a, b]), 0.5)

    def test_best_scene_index(self):
        a = np.array([[1.0, np.nan], [0.0, 5.0]], dtype=np.float32)
        b = np.array([[2.0, 3.0], [0.0, 1.0]], dtype=np.float32)
        idx = composites_mod.best_scene_index([a, b])
        self.assertEqual(idx.tolist(), [[1, 1], [0, 0]])


class TestSTAC(unittest.TestCase):
    def test_build_payload(self):
        payload = stac_mod.build_search_payload(
            ["sentinel-2-l2a"], (-70, 43, -69, 44),
            "2026-01-01/2026-02-01", max_cloud_cover=20.0,
        )
        self.assertEqual(payload["collections"], ["sentinel-2-l2a"])
        self.assertEqual(payload["query"], {"eo:cloud_cover": {"lt": 20.0}})

    def test_build_payload_bad_collection(self):
        with self.assertRaises(bands_mod.BandError):
            stac_mod.build_search_payload(["nope"], (-70, 43, -69, 44), "2026-01-01/2026-02-01")

    def _fake_item(self):
        return {
            "id": "S2A_test",
            "collection": "sentinel-2-l2a",
            "bbox": [-70, 43, -69, 44],
            "properties": {"datetime": "2026-06-01T10:00:00Z", "eo:cloud_cover": 12.5},
            "assets": {
                "B04": {"href": "https://example.com/B04.tif"},
                "B08": {"href": "https://example.com/B08.tif"},
                "SCL": {"href": "https://example.com/SCL.tif"},
            },
        }

    def test_search_parses_items(self):
        import requests as _requests

        item = self._fake_item()

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"features": [item], "links": []}

        orig = _requests.post
        _requests.post = lambda *a, **k: FakeResponse()
        try:
            scenes = stac_mod.search("https://example.com/stac", {"limit": 1})
        finally:
            _requests.post = orig
        self.assertEqual(len(scenes), 1)
        scene = scenes[0]
        self.assertEqual(scene.id, "S2A_test")
        self.assertAlmostEqual(scene.cloud_cover, 12.5)
        self.assertEqual(scene.href("red"), "https://example.com/B04.tif")
        self.assertTrue(scene.has_aliases(["red", "nir"]))
        self.assertFalse(scene.has_aliases(["tirs1"]))

    def test_search_http_error(self):
        import requests as _requests

        class FakeResponse:
            status_code = 500
            text = "boom"

        orig = _requests.post
        _requests.post = lambda *a, **k: FakeResponse()
        try:
            with self.assertRaises(stac_mod.STACError):
                stac_mod.search("https://example.com/stac", {})
        finally:
            _requests.post = orig

    def test_sort_filter_dedupe(self):
        s1 = Scene("a", "sentinel-2-l2a", "2026-06-01T10:00:00Z", 30.0, [], {})
        s2 = Scene("b", "sentinel-2-l2a", "2026-06-01T11:00:00Z", 5.0, [], {})
        s3 = Scene("c", "sentinel-2-l2a", "2026-06-02T10:00:00Z", None, [], {})
        self.assertEqual(
            [s.id for s in stac_mod.sort_scenes([s3, s1], by="cloud_cover")],
            ["a", "c"],
        )
        self.assertEqual(
            [s.id for s in stac_mod.filter_max_cloud([s1, s2, s3], 10.0)],
            ["b", "c"],
        )
        deduped = stac_mod.latest_per_date([s1, s2, s3])
        self.assertEqual([s.id for s in deduped], ["b", "c"])


class TestAcquisition(unittest.TestCase):
    def setUp(self):
        self.files = []
        data = np.arange(100, dtype=np.float32).reshape(10, 10)
        # transform: origin (0, 10), 1x1 degree pixels, north-up
        self.path = _temp_tif(data, (1.0, 0.0, 0.0, 0.0, -1.0, 10.0))
        self.files.append(self.path)

    def tearDown(self):
        for f in self.files:
            if os.path.exists(f):
                os.remove(f)

    def test_read_band_full(self):
        band = read_band(self.path)
        self.assertEqual(band.shape, (10, 10))
        self.assertAlmostEqual(float(band.data[0, 0]), 0.0)
        self.assertAlmostEqual(float(band.data[9, 9]), 99.0)

    def test_read_band_aoi_clip(self):
        a = from_bbox(2.0, 5.0, 5.0, 8.0)
        band = read_band(self.path, aoi=a)
        self.assertEqual(band.shape, (3, 3))
        # pixel (row 2, col 2) of the source == value 22
        self.assertAlmostEqual(float(band.data[0, 0]), 22.0)

    def test_read_band_outside_masked(self):
        # AOI polygon covering only part of the window bbox is not trivial
        # with a bbox AOI; instead check nodata handling via a nodata file.
        data = np.array([[1.0, -9999.0]], dtype=np.float32)
        p = _temp_tif(data, (1.0, 0.0, 0.0, 0.0, -1.0, 1.0), nodata=-9999.0)
        self.files.append(p)
        band = read_band(p)
        self.assertTrue(np.isnan(band.data[0, 1]))
        self.assertAlmostEqual(float(band.data[0, 0]), 1.0)

    def test_read_band_bad_href(self):
        from imagery.acquisition import AcquisitionError

        with self.assertRaises(AcquisitionError):
            read_band("/nonexistent/path.tif")

    def test_read_stack_alignment(self):
        p2 = _temp_tif(np.full((5, 5), 7.0, dtype=np.float32),
                       (2.0, 0.0, 0.0, 0.0, -2.0, 10.0))
        self.files.append(p2)
        a = from_bbox(0.0, 0.0, 10.0, 10.0)
        stack = read_stack({"fine": self.path, "coarse": p2},
                           ["fine", "coarse"], aoi=a)
        self.assertEqual(stack["fine"].shape, stack["coarse"].shape)
        self.assertEqual(stack["fine"].crs, stack["coarse"].crs)

    def test_estimate_read_size(self):
        a = from_bbox(-70, 43, -69, 44)
        mb = estimate_read_size_mb(a, "EPSG:4326", 10.0, bands=3)
        self.assertGreater(mb, 0)


class TestTimeseries(unittest.TestCase):
    def test_zonal_stats(self):
        data = np.arange(16, dtype=np.float32).reshape(4, 4)
        a = from_bbox(1.0, 1.0, 3.0, 3.0)  # middle 2x2 of a 0..4 grid
        stats = timeseries_mod.zonal_stats(
            data, (1.0, 0.0, 0.0, 0.0, -1.0, 4.0), "EPSG:4326", a,
            scene_id="s1", datetime="2026-06-01T00:00:00Z", index="ndvi",
        )
        # middle 2x2 values: rows 1-2, cols 1-2 -> 5,6,9,10
        self.assertEqual(stats.valid_pixels, 4)
        self.assertAlmostEqual(stats.mean, 7.5)
        self.assertAlmostEqual(stats.minimum, 5.0)
        self.assertAlmostEqual(stats.maximum, 10.0)

    def test_zonal_stats_empty(self):
        data = np.full((4, 4), np.nan, dtype=np.float32)
        a = from_bbox(0.0, 0.0, 4.0, 4.0)
        stats = timeseries_mod.zonal_stats(
            data, (1.0, 0.0, 0.0, 0.0, -1.0, 4.0), "EPSG:4326", a)
        self.assertEqual(stats.valid_pixels, 0)
        self.assertTrue(np.isnan(stats.mean))

    def test_csv_json_roundtrip(self):
        rec = SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.5, 0.5, 0.1,
                         0.3, 0.7, 0.35, 0.65, 100, 120, 5.0)
        with tempfile.TemporaryDirectory() as d:
            csv_path = timeseries_mod.write_csv([rec], os.path.join(d, "ts.csv"))
            with open(csv_path, newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["scene_id"], "s1")
            self.assertAlmostEqual(float(rows[0]["mean"]), 0.5)
            json_path = timeseries_mod.write_json([rec], os.path.join(d, "ts.json"))
            with open(json_path) as handle:
                payload = json.load(handle)
            self.assertEqual(payload[0]["index"], "ndvi")

    def test_from_dict_roundtrip(self):
        rec = SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.5, 0.5, 0.1,
                         0.3, 0.7, 0.35, 0.65, 100, 120, 5.0)
        self.assertEqual(SceneStats.from_dict(rec.to_dict()), rec)
        # empty cloud cover cell -> None
        row = rec.to_dict()
        row["cloud_cover"] = ""
        self.assertIsNone(SceneStats.from_dict(row).cloud_cover)

    def test_read_csv_roundtrip(self):
        recs = [
            SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.4, 0.4, 0.0,
                       0.4, 0.4, 0.4, 0.4, 10, 10, 2.5),
            SceneStats("s2", "2026-06-06T00:00:00Z", "ndvi", 0.6, 0.6, 0.0,
                       0.6, 0.6, 0.6, 0.6, 10, 10, None),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = timeseries_mod.write_csv(recs, os.path.join(d, "ts.csv"))
            back = timeseries_mod.read_csv(path)
        self.assertEqual(back, recs)

    def test_read_json_roundtrip(self):
        recs = [
            SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.4, 0.4, 0.0,
                       0.4, 0.4, 0.4, 0.4, 10, 10),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = timeseries_mod.write_json(recs, os.path.join(d, "ts.json"))
            back = timeseries_mod.read_json(path)
        self.assertEqual(back, recs)

    def test_merge_records(self):
        old = [
            SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.4, 0.4, 0.0,
                       0.4, 0.4, 0.4, 0.4, 10, 10),
            SceneStats("s2", "2026-06-06T00:00:00Z", "ndvi", 0.6, 0.6, 0.0,
                       0.6, 0.6, 0.6, 0.6, 10, 10),
        ]
        # s2 reprocessed with a better mask; s3 is brand new
        new = [
            SceneStats("s2", "2026-06-06T00:00:00Z", "ndvi", 0.7, 0.7, 0.0,
                       0.7, 0.7, 0.7, 0.7, 10, 10),
            SceneStats("s3", "2026-05-28T00:00:00Z", "ndvi", 0.3, 0.3, 0.0,
                       0.3, 0.3, 0.3, 0.3, 10, 10),
        ]
        merged = timeseries_mod.merge_records(old, new)
        self.assertEqual(len(merged), 3)  # no duplicates
        by_id = {r.scene_id: r for r in merged}
        self.assertAlmostEqual(by_id["s2"].mean, 0.7)  # new wins
        # sorted by (datetime, index, scene_id)
        self.assertEqual([r.scene_id for r in merged], ["s3", "s1", "s2"])

    def test_merge_records_empty(self):
        rec = SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.4, 0.4, 0.0,
                         0.4, 0.4, 0.4, 0.4, 10, 10)
        self.assertEqual(timeseries_mod.merge_records([], [rec]), [rec])
        self.assertEqual(timeseries_mod.merge_records([rec], []), [rec])

    def test_summarize(self):
        recs = [
            SceneStats("s1", "2026-06-01T00:00:00Z", "ndvi", 0.4, 0.4, 0.0,
                       0.4, 0.4, 0.4, 0.4, 10, 10),
            SceneStats("s2", "2026-06-06T00:00:00Z", "ndvi", 0.6, 0.6, 0.0,
                       0.6, 0.6, 0.6, 0.6, 10, 10),
        ]
        summary = timeseries_mod.summarize(recs)
        self.assertEqual(summary["valid_scenes"], 2)
        self.assertAlmostEqual(summary["delta_mean"], 0.2)


class TestIO(unittest.TestCase):
    def test_write_cog_roundtrip(self):
        import rasterio

        data = np.arange(16, dtype=np.float32).reshape(4, 4)
        with tempfile.TemporaryDirectory() as d:
            path = io_mod.write_cog(
                os.path.join(d, "out.tif"), data,
                (1.0, 0.0, 0.0, 0.0, -1.0, 4.0), "EPSG:4326")
            with rasterio.open(path) as src:
                self.assertEqual(src.shape, (4, 4))
                self.assertEqual(src.count, 1)
                back = src.read(1)
            self.assertTrue(np.allclose(back, data))

    def test_write_cog_bad_shape(self):
        with self.assertRaises(ValueError):
            io_mod.write_cog("/tmp/x.tif", np.zeros((2, 2, 2)),
                             (1, 0, 0, 0, -1, 2), "EPSG:4326")

    def test_write_multiband(self):
        import rasterio

        bands = {"ndvi": np.ones((4, 4), dtype=np.float32),
                 "ndwi": np.zeros((4, 4), dtype=np.float32)}
        with tempfile.TemporaryDirectory() as d:
            path = io_mod.write_multiband_cog(
                os.path.join(d, "multi.tif"), bands,
                (1.0, 0.0, 0.0, 0.0, -1.0, 4.0), "EPSG:4326")
            with rasterio.open(path) as src:
                self.assertEqual(src.count, 2)
                self.assertEqual(src.descriptions, ("ndvi", "ndwi"))

    def test_write_geojson_and_text(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = io_mod.write_geojson(os.path.join(d, "a.geojson"),
                                      {"type": "Point", "coordinates": [0, 0]})
            with open(p1) as handle:
                self.assertEqual(json.load(handle)["type"], "Point")
            p2 = io_mod.write_text(os.path.join(d, "sub", "r.md"), "# hi")
            self.assertTrue(os.path.exists(p2))


class TestQGIS(unittest.TestCase):
    def test_style_qml(self):
        qml = qgis_mod.style_qml("ndvi")
        self.assertIn("singlebandpseudocolor", qml)
        self.assertIn("Dense vegetation", qml)

    def test_style_unknown(self):
        with self.assertRaises(ValueError):
            qgis_mod.style_qml("nope")

    def test_list_styles(self):
        self.assertIn("ndvi", qgis_mod.list_styles())
        self.assertIn("ndwi", qgis_mod.list_styles())

    def test_write_style_qml(self):
        with tempfile.TemporaryDirectory() as d:
            path = qgis_mod.write_style_qml(os.path.join(d, "ndvi.qml"), "ndvi")
            with open(path) as handle:
                self.assertIn("qgis", handle.read()[:200])

    def test_usage_notes(self):
        self.assertIn("QGIS", qgis_mod.qgis_usage_notes())


class TestInterop(unittest.TestCase):
    def test_as_grid_and_check(self):
        grid = interop_mod.as_grid(np.ones((2, 2)), (1, 0, 0, 0, -1, 2), "EPSG:4326")
        checked = interop_mod.check_grid(grid)
        self.assertEqual(checked["crs"], "EPSG:4326")
        with self.assertRaises(ValueError):
            interop_mod.check_grid({"data": np.ones((2, 2))})

    def test_crs_from_any(self):
        self.assertEqual(interop_mod.crs_from_any("EPSG:32618"), "EPSG:32618")

        class FakeCRS:
            def to_wkt(self):
                return "FAKE_WKT"

        self.assertEqual(interop_mod.crs_from_any(FakeCRS()), "FAKE_WKT")

        class FakeGeo:
            crs = "EPSG:4326"

        self.assertEqual(interop_mod.crs_from_any(FakeGeo()), "EPSG:4326")

    def test_from_survey_raster_ducktype(self):
        class FakeRaster:
            data = [[0.5, -9999.0], [0.2, 0.3]]
            transform = (1, 0, 0, 0, -1, 2)
            crs = "EPSG:4326"
            nodata = -9999.0

        grid = interop_mod.from_survey_raster(FakeRaster())
        self.assertTrue(np.isnan(grid["data"][0, 1]))
        self.assertAlmostEqual(float(grid["data"][0, 0]), 0.5)

    def test_to_survey_raster_missing(self):
        grid = interop_mod.as_grid(np.ones((2, 2)), (1, 0, 0, 0, -1, 2), "EPSG:4326")
        try:
            import raster  # noqa: F401
        except ImportError:
            with self.assertRaises(ImportError):
                interop_mod.to_survey_raster(grid)
        else:
            obj = interop_mod.to_survey_raster(grid)
            self.assertIsNotNone(obj)

    def test_grid_to_pointcloud_xyz(self):
        grid = interop_mod.as_grid(
            np.array([[1.0, np.nan], [3.0, 4.0]]),
            (10.0, 0.0, 100.0, 0.0, -10.0, 200.0), "EPSG:32618")
        pc = interop_mod.grid_to_pointcloud_xyz(grid)
        self.assertEqual(len(pc["points"]), 3)  # NaN skipped
        x, y, v = pc["points"][0]
        self.assertAlmostEqual(x, 105.0)  # pixel centre
        self.assertAlmostEqual(y, 195.0)
        self.assertAlmostEqual(v, 1.0)


class TestMonitor(unittest.TestCase):
    def _scene(self, tmpdir, scene_id="S2_TEST", dt="2026-06-01T10:00:00Z"):
        red = _temp_tif(np.full((10, 10), 2000, dtype=np.uint16),
                        (0.001, 0.0, -70.0, 0.0, -0.001, 44.0))
        nir = _temp_tif(np.full((10, 10), 6000, dtype=np.uint16),
                        (0.001, 0.0, -70.0, 0.0, -0.001, 44.0))
        scl = _temp_tif(np.full((10, 10), 4, dtype=np.uint8),
                        (0.001, 0.0, -70.0, 0.0, -0.001, 44.0))
        self.addCleanup(os.remove, red)
        self.addCleanup(os.remove, nir)
        self.addCleanup(os.remove, scl)
        return Scene(
            id=scene_id, collection="sentinel-2-l2a",
            datetime=dt, cloud_cover=5.0,
            bbox=[-70.0, 43.99, -69.99, 44.0],
            assets={"red": red, "nir": nir, "scl": scl},
        )

    def test_config_defaults(self):
        cfg = MonitorConfig(
            name="demo",
            aoi=from_bbox(-70, 43, -69, 44),
            collections=["sentinel-2-l2a"],
            indices=["ndvi"],
            output_dir="/tmp/demo",
        )
        self.assertEqual(cfg.max_cloud_cover, 40.0)
        self.assertTrue(cfg.start < cfg.end)

    def test_config_validation(self):
        with self.assertRaises(monitor_mod.MonitorError):
            MonitorConfig(name="x", aoi=from_bbox(-70, 43, -69, 44),
                          collections=[], indices=["ndvi"], output_dir="/tmp/x")
        with self.assertRaises(monitor_mod.MonitorError):
            MonitorConfig(name="x", aoi=from_bbox(-70, 43, -69, 44),
                          collections=["sentinel-2-l2a"], indices=["nope"],
                          output_dir="/tmp/x")

    def test_config_dict_roundtrip(self):
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo")
        cfg2 = MonitorConfig.from_dict(cfg.to_dict())
        self.assertEqual(cfg2.name, "demo")
        self.assertEqual(cfg2.aoi.bounds_lonlat(), (-70, 43, -69, 44))

    def test_run_monitor_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            scene = self._scene(d)
            orig = monitor_mod.search_scenes
            monitor_mod.search_scenes = lambda *a, **k: [scene]
            self.addCleanup(setattr, monitor_mod, "search_scenes", orig)
            cfg = MonitorConfig(
                name="demo",
                aoi=from_bbox(-70.0, 43.99, -69.99, 44.0),
                collections=["sentinel-2-l2a"],
                indices=["ndvi"],
                output_dir=os.path.join(d, "out"),
                start="2026-06-01", end="2026-06-02",
            )
            result = monitor_mod.run_monitor(cfg)
            self.assertEqual(len(result.scenes), 1)
            self.assertEqual(len(result.records), 1)
            rec = result.records[0]
            # refl: red 0.2, nir 0.6 -> ndvi 0.5
            self.assertAlmostEqual(rec.mean, 0.5, places=4)
            self.assertTrue(os.path.exists(result.timeseries_csv))
            self.assertTrue(os.path.exists(result.report_path))
            self.assertEqual(len(result.rasters), 1)
            self.assertTrue(os.path.exists(result.rasters[0]))
            self.assertTrue(os.path.exists(
                result.rasters[0].replace(".tif", ".qml")))

    def test_run_monitor_no_scenes(self):
        orig = monitor_mod.search_scenes
        monitor_mod.search_scenes = lambda *a, **k: []
        self.addCleanup(setattr, monitor_mod, "search_scenes", orig)
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo-nosuch")
        with self.assertRaises(monitor_mod.MonitorError):
            monitor_mod.run_monitor(cfg)

    def test_render_report(self):
        scene = Scene("S1", "sentinel-2-l2a", "2026-06-01T10:00:00Z",
                      5.0, [], {})
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo")
        report = monitor_mod.render_report(cfg, [scene], {"valid_scenes": 0})
        self.assertIn("demo", report)
        self.assertIn("S1", report)

    def test_config_signer_roundtrip(self):
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo",
                            signer="planetary-computer")
        self.assertEqual(cfg.to_dict()["signer"], "planetary-computer")
        cfg2 = MonitorConfig.from_dict(cfg.to_dict())
        self.assertEqual(cfg2.signer, "planetary-computer")
        self.assertIs(
            signing_mod.resolve_signer(cfg2.signer),
            signing_mod.planetary_computer_signer,
        )

    def test_config_signer_callable_not_serialised(self):
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo",
                            signer=lambda href: href)
        self.assertIsNone(cfg.to_dict()["signer"])

    def test_config_signer_invalid_rejected(self):
        cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                            collections=["sentinel-2-l2a"], indices=["ndvi"],
                            output_dir="/tmp/demo")
        data = cfg.to_dict()
        data["signer"] = 123
        with self.assertRaises(monitor_mod.MonitorError):
            MonitorConfig.from_dict(data)

    def _run_with_scenes(self, d, scenes, **cfg_kwargs):
        orig = monitor_mod.search_scenes
        monitor_mod.search_scenes = lambda *a, **k: list(scenes)
        self.addCleanup(setattr, monitor_mod, "search_scenes", orig)
        kwargs = dict(
            name="demo",
            aoi=from_bbox(-70.0, 43.99, -69.99, 44.0),
            collections=["sentinel-2-l2a"],
            indices=["ndvi"],
            output_dir=os.path.join(d, "out"),
            start="2026-06-01", end="2026-06-03",
        )
        kwargs.update(cfg_kwargs)
        return monitor_mod.run_monitor(MonitorConfig(**kwargs))

    def test_run_monitor_merges_on_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            scene = self._scene(d)
            first = self._run_with_scenes(d, [scene])
            self.assertEqual(len(first.records), 1)
            second = self._run_with_scenes(d, [scene])
            # Re-running over the same window must not duplicate rows.
            self.assertEqual(len(second.records), 1)
            with open(second.timeseries_csv, newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scene_id"], "S2_TEST")

    def test_run_monitor_composite(self):
        with tempfile.TemporaryDirectory() as d:
            scenes = [
                self._scene(d, "S2_A", "2026-06-01T10:00:00Z"),
                self._scene(d, "S2_B", "2026-06-02T10:00:00Z"),
            ]
            result = self._run_with_scenes(d, scenes, composite=True)
            self.assertEqual(len(result.composites), 1)
            comp = result.composites[0]
            self.assertTrue(os.path.exists(comp))
            self.assertIn("composite", os.path.basename(comp))
            self.assertTrue(os.path.exists(comp.replace(".tif", ".qml")))
            import rasterio
            with rasterio.open(comp) as src:
                arr = src.read(1)
            # red 0.2 / nir 0.6 -> ndvi 0.5 in both scenes -> median 0.5
            self.assertAlmostEqual(float(arr[5, 5]), 0.5, places=4)
            self.assertIn("composites", result.summary)

    def test_run_monitor_composite_single_scene_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            result = self._run_with_scenes(d, [self._scene(d)], composite=True)
            self.assertEqual(result.composites, [])

    def test_run_monitor_skips_unreadable_scene(self):
        with tempfile.TemporaryDirectory() as d:
            good = self._scene(d, "S2_GOOD", "2026-06-01T10:00:00Z")
            bad = self._scene(d, "S2_BAD", "2026-06-02T10:00:00Z")
            bad.assets = {"red": "/nonexistent/red.tif"}  # unreadable
            orig = monitor_mod.process_scene
            calls = []

            def fake_process(scene, config, out_dir):
                calls.append(scene.id)
                if scene.id == "S2_BAD":
                    raise monitor_mod.AcquisitionError("boom")
                return orig(scene, config, out_dir)

            monitor_mod.process_scene = fake_process
            self.addCleanup(setattr, monitor_mod, "process_scene", orig)
            result = self._run_with_scenes(d, [good, bad])
            self.assertEqual(calls, ["S2_GOOD", "S2_BAD"])
            # The bad scene is skipped; the good one still lands in the series.
            self.assertEqual([r.scene_id for r in result.records], ["S2_GOOD"])


class TestSigning(unittest.TestCase):
    def setUp(self):
        signing_mod.clear_sign_cache()

    def _resp(self, payload, status=200):
        m = mock.Mock()
        m.status_code = status
        m.json.return_value = payload
        if status >= 400:
            m.raise_for_status.side_effect = requests.HTTPError(f"{status}")
        return m

    def test_list_signers(self):
        self.assertIn("planetary-computer", signing_mod.list_signers())

    def test_resolve_none_and_callable(self):
        self.assertIsNone(signing_mod.resolve_signer(None))
        fn = lambda href: href  # noqa: E731
        self.assertIs(fn, signing_mod.resolve_signer(fn))

    def test_resolve_named(self):
        self.assertIs(
            signing_mod.resolve_signer("planetary-computer"),
            signing_mod.planetary_computer_signer,
        )

    def test_resolve_unknown(self):
        with self.assertRaises(signing_mod.SigningError):
            signing_mod.resolve_signer("nope")
        with self.assertRaises(signing_mod.SigningError):
            signing_mod.resolve_signer(123)

    def test_signer_href_shape(self):
        signed = "https://x/signed?se=2030-01-01T00:00:00Z"
        with mock.patch.object(
            signing_mod.requests, "get", return_value=self._resp({"href": signed})
        ) as get:
            out = signing_mod.planetary_computer_signer("https://x/raw")
        self.assertEqual(out, signed)
        get.assert_called_once()

    def test_signer_token_shape(self):
        with mock.patch.object(
            signing_mod.requests, "get", return_value=self._resp({"token": "sig=abc"})
        ):
            out = signing_mod.planetary_computer_signer("https://x/raw")
        self.assertEqual(out, "https://x/raw?sig=abc")

    def test_signer_empty_shape_raises(self):
        with mock.patch.object(
            signing_mod.requests, "get", return_value=self._resp({})
        ):
            with self.assertRaises(signing_mod.SigningError):
                signing_mod.planetary_computer_signer("https://x/raw")

    def test_signer_retries_429_then_succeeds(self):
        calls = []

        def fake_get(*_a, **_k):
            calls.append(1)
            if len(calls) == 1:
                return self._resp({}, status=429)
            return self._resp({"href": "https://x/signed?se=2030-01-01T00:00:00Z"})

        with mock.patch.object(signing_mod.requests, "get", side_effect=fake_get):
            out = signing_mod.planetary_computer_signer("https://x/raw")
        self.assertEqual(out, "https://x/signed?se=2030-01-01T00:00:00Z")
        self.assertEqual(len(calls), 2)

    def test_signer_caches_until_expiry(self):
        signed = "https://x/signed?se=2030-01-01T00:00:00Z"
        with mock.patch.object(
            signing_mod.requests, "get", return_value=self._resp({"href": signed})
        ) as get:
            first = signing_mod.planetary_computer_signer("https://x/raw")
            second = signing_mod.planetary_computer_signer("https://x/raw")
        self.assertEqual(first, second)
        get.assert_called_once()  # second call served from cache

    def test_signer_cache_respects_expiry(self):
        past = "https://x/old?se=2020-01-01T00:00:00Z"
        fresh = "https://x/new?se=2030-01-01T00:00:00Z"
        with mock.patch.object(
            signing_mod.requests, "get", return_value=self._resp({"href": fresh})
        ) as get:
            signing_mod._cache_sign("https://x/raw", past)  # expired entry
            out = signing_mod.planetary_computer_signer("https://x/raw")
        self.assertEqual(out, fresh)
        get.assert_called_once()


class TestCLI(unittest.TestCase):
    def test_info(self):
        self.assertEqual(cli_main(["info"]), 0)

    def test_indices(self):
        self.assertEqual(cli_main(["indices"]), 0)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            cli_main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_monitor_signer_flag(self):
        from imagery import cli as cli_mod

        captured = {}

        def fake_run_monitor(config, progress=None):
            captured["signer"] = config.signer
            return mock.Mock(
                scenes=[], records=[], rasters=[], composites=[],
                report_path="r.md", timeseries_csv="t.csv", summary={},
            )

        orig = cli_mod.run_monitor
        cli_mod.run_monitor = fake_run_monitor
        self.addCleanup(setattr, cli_mod, "run_monitor", orig)
        with tempfile.TemporaryDirectory() as d:
            cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                                collections=["sentinel-2-l2a"], indices=["ndvi"],
                                output_dir=os.path.join(d, "out"))
            path = os.path.join(d, "cfg.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cfg.to_dict(), handle)
            self.assertEqual(
                cli_main(["monitor", "--config", path,
                          "--signer", "planetary-computer"]), 0)
        self.assertEqual(captured["signer"], "planetary-computer")

    def test_monitor_signer_from_config_file(self):
        from imagery import cli as cli_mod

        captured = {}

        def fake_run_monitor(config, progress=None):
            captured["signer"] = config.signer
            return mock.Mock(
                scenes=[], records=[], rasters=[], composites=[],
                report_path="r.md", timeseries_csv="t.csv", summary={},
            )

        orig = cli_mod.run_monitor
        cli_mod.run_monitor = fake_run_monitor
        self.addCleanup(setattr, cli_mod, "run_monitor", orig)
        with tempfile.TemporaryDirectory() as d:
            cfg = MonitorConfig(name="demo", aoi=from_bbox(-70, 43, -69, 44),
                                collections=["sentinel-2-l2a"], indices=["ndvi"],
                                output_dir=os.path.join(d, "out"),
                                signer="planetary-computer")
            path = os.path.join(d, "cfg.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cfg.to_dict(), handle)
            self.assertEqual(cli_main(["monitor", "--config", path]), 0)
        self.assertEqual(captured["signer"], "planetary-computer")


class TestPackageAPI(unittest.TestCase):
    def test_version(self):
        self.assertEqual(imagery.__version__, "0.1.1")

    def test_exports(self):
        for name in ["AOI", "Scene", "MonitorConfig", "compute",
                     "temporal_composite", "run_monitor", "write_cog",
                     "style_qml", "as_grid", "search_scenes",
                     "resolve_signer", "planetary_computer_signer",
                     "list_signers", "merge_records", "read_csv", "read_json",
                     "clear_sign_cache"]:
            self.assertTrue(hasattr(imagery, name), name)


if __name__ == "__main__":
    unittest.main()
