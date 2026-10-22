"""Validation des réglages supplémentaires : jamais de valeur hors plage envoyée au robot."""
import json
import unittest

from dreameui import extras
from dreameui.device import ServiceError


def rows(**values):
    return [{"siid": s, "piid": p, "code": 0, "value": v} for (s, p), v in values.items()]


ROWS = [{"siid": s, "piid": p, "code": 0, "value": v} for (s, p), v in {
    (9, 1): 154, (9, 2): 51, (11, 1): 2, (11, 2): 4, (7, 1): 57, (28, 1): 24, (28, 8): 3, (4, 11): 1,
    (4, 40): 3, (12, 3): 469, (5, 4): '[{"id":0,"en":false,"st":"22:00","et":"08:00","wk":127,"ss":0}]'}.items()]


class ExtrasTests(unittest.TestCase):
    def test_describe(self):
        d = extras.describe(ROWS)
        filt = next(c for c in d["consumables"] if c["key"] == "filter")
        self.assertEqual((filt["percent"], filt["hours"]), (2, 4))
        self.assertEqual(d["settings"]["wetness"]["value"], 24)
        self.assertNotIn("child_lock", d["settings"])  # propriété absente : réglage masqué
        self.assertEqual(d["dnd"], {"enabled": False, "start": "22:00", "end": "08:00"})
        self.assertEqual(d["stats"], {"count": 469})

    def test_ranges(self):
        self.assertEqual(extras.setting_payload(ROWS, "volume", 70), ((7, 1), 70))
        for name, value in (("volume", 101), ("wetness", 27), ("water_temperature", 9), ("drying_time", 8),
                            ("volume", True), ("child_lock", 1), ("unknown", 1)):
            with self.assertRaises(ServiceError, msg=(name, value)):
                extras.setting_payload(ROWS, name, value)

    def test_dnd_task_preserves_other_fields(self):
        [(pair, value)] = extras.dnd_payload(ROWS, True, "23:00", "07:30")
        self.assertEqual(pair, (5, 4))
        self.assertEqual(json.loads(value), [{"id": 0, "en": True, "st": "23:00", "et": "07:30", "wk": 127, "ss": 0}])
        for args in ((True, "25:00", "07:00"), (True, "07:00", "07:00"), ("yes", "22:00", "08:00")):
            with self.assertRaises(ServiceError):
                extras.dnd_payload(ROWS, *args)

    def test_reset(self):
        self.assertEqual(extras.reset_action(ROWS, "filter"), (11, 1))
        with self.assertRaises(ServiceError):
            extras.reset_action(ROWS, "wheel")


if __name__ == "__main__":
    unittest.main()
