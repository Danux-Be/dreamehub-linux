"""Vecteurs de test repris de dreamehub (MapDecoderTests.swift, CloudClientTests.swift)."""
import base64
import json
import struct
import unittest
import zlib

from dreameui import mapdecoder
from dreameui.cloud import canonical_signature_input
from dreameui.device import updated_grouped
from dreameui.maps import configuration


def frame(metadata=None, pixels=(4, 4, 252, 8), kind=73, fid=9):
    raw = bytearray(27)

    def put(at, value):
        raw[at:at + 2] = struct.pack("<h", value)

    put(0, 7); put(2, fid); raw[4] = kind; put(5, -100); put(7, 200); put(9, 359)
    put(11, 32767); put(13, 32767); put(15, 32767); put(17, 50); put(19, 2); put(21, 2); put(23, -500); put(25, -300)
    return bytes(raw) + bytes(pixels) + json.dumps(metadata or {}).encode()


def packed(data):
    return base64.b64encode(zlib.compress(data, 6)).decode()


class MapDecoderTests(unittest.TestCase):
    def test_full_frame(self):
        m = mapdecoder.decode_inflated(frame({"fsm": 1, "tr": "M-100,200L50,-50S10,20L10,0"}))
        self.assertEqual(m["robot_position"], [-100, 200, 359])
        self.assertIsNone(m["charger_position"])
        self.assertEqual(m["data"]["1"], [0, 0, 2])
        self.assertEqual(m["data"]["255"], [0, 1, 1])
        self.assertEqual(m["path"], [[3, -100, 200, -50, 150], [1, 10, 20, 20, 20]])
        json.dumps(m)

    def test_no_fake_sensor_measurements(self):
        m = mapdecoder.decode_inflated(frame({"laser_obstacle": [[3, 4, 128]]}))
        t = m["telemetry"]
        self.assertEqual(t["simulated"], [])
        self.assertIsNone(t["observed_at"])
        self.assertIsNone(t["freshness_ms"])
        self.assertIsNone(m["laser_obstacles"][0][3])
        self.assertFalse(t["capabilities"]["raw_lidar_rays"])

    def test_invalid_frames(self):
        with self.assertRaises(mapdecoder.MapError):
            mapdecoder.decode_inflated(bytes([1, 2]))
        with self.assertRaises(mapdecoder.IncrementalFrame):
            mapdecoder.decode_inflated(frame(kind=80))
        with self.assertRaises(mapdecoder.MapError):
            mapdecoder.decode("not_base64")

    def test_ordered_delta_and_path(self):
        s = mapdecoder.MapStream()
        s.accept_inflated(frame({"fsm": 1, "tr": "M1,2L1,1"}))
        m = s.accept_inflated(frame({"tr": "l3,4L1,0", "timestamp_ms": 1792070000000}, pixels=(4, 0, 0, 0), kind=80, fid=10))
        self.assertEqual(m["data"]["2"], [0, 0, 1, 1, 1, 1])
        self.assertEqual(m["path"], [[3, 1, 2, 2, 3, 3, 4, 4, 4]])
        self.assertEqual(m["frame_type"], "P")
        self.assertTrue(m["incremental_verified_chain"])

    def test_missing_predecessor(self):
        s = mapdecoder.MapStream()
        s.accept_inflated(frame({"fsm": 1}))
        with self.assertRaises(mapdecoder.IncrementalFrame):
            s.accept_inflated(frame(pixels=(4, 0, 0, 0), kind=80, fid=11))
        s.accept_inflated(frame(pixels=(4, 0, 0, 0), kind=80, fid=10))

    def test_model_metadata(self):
        self.assertEqual(len(configuration("dreame.vacuum.r2338a")[0]), 16)
        self.assertEqual(len(configuration("dreame.vacuum.r2579c")[0]), 16)
        self.assertIsNone(configuration("unknown.vacuum.no_such_model"))

    def test_nested_saved_map(self):
        saved = packed(frame({}, pixels=(1, 2, 128, 3)))
        result = mapdecoder.decode_inflated(frame({"ris": 2, "rism": saved}, pixels=(0, 2, 1, 0)))
        layers = result["data"]
        self.assertEqual(layers["1"], [0, 0, 1])
        self.assertEqual(layers["255"], [1, 0, 1])
        self.assertEqual(layers["254"], [0, 1, 1])
        self.assertEqual(layers["3"], [1, 1, 1])
        self.assertEqual(result["saved_map_id"], 7)

    def test_v3_delta_cover_and_objects(self):
        def obstacle(i, x):
            return [x, 2, 128, 0.9, f"100{i}", "file", "key", 0, 0, 0, 0, 2, i, 0, 0, 0, 0, 0, 0]
        s = mapdecoder.MapStream()
        s.accept_inflated(frame({"saveMapId": 7, "tr": "M1,2L1,1", "ai_obstacle": [obstacle(1, 1), obstacle(2, 2)]},
                                pixels=(1, 1, 63, 2)), 3)
        m = s.accept_inflated(frame({"cover": {"timestamp_ms": 1792070000000, "tr": "l3,4L1,0", "laser_obstacle": [[5, 6, 128, 0.7]]},
                                     "diff": {"ai_obstacle": [obstacle(1, 9), obstacle(3, 3)], "deleteContent": {"ai_obstacle": [2]}}},
                                    pixels=(2, 0, 0, 0), kind=80, fid=10), 3)
        self.assertEqual(m["data"]["2"], [0, 0, 1, 1, 1, 1])
        self.assertEqual(m["path"], [[3, 1, 2, 2, 3, 3, 4, 4, 4]])
        self.assertEqual(len(m["obstacles"]), 2)
        self.assertEqual(m["obstacles"][0][1], 9)
        self.assertEqual(m["obstacles"][1][7], "1003")
        self.assertEqual(m["timestamp_ms"], 1792070000000)
        self.assertEqual(len(m["laser_obstacles"]), 1)


class CloudTests(unittest.TestCase):
    def test_canonical_signature(self):
        value = canonical_signature_input({"x": {"b": "Türkçe", "a": [1]}, "q": [2], "n": None})
        self.assertEqual(value, 'n=null&q=[2]&x=[b="Türkçe"]')

    def test_grouped_encoding(self):
        raw = (2 << 16) | (5 << 8) | 0  # humidité 2, bits intermédiaires 5, mode brut 0 (= aspiration+lavage)
        self.assertEqual(updated_grouped(raw, mode=0) & 3, 2)
        self.assertEqual(updated_grouped(raw, water=3) >> 16, 3)
        self.assertEqual((updated_grouped(raw, water=3) >> 8) & 0xFF, 5)


if __name__ == "__main__":
    unittest.main()
