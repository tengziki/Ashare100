"""行业补档的离线测试：已核实优先、EM2016 映射、映射不到与接口失败均不写入错值。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrich_profiles as enrich


class IndustryEnrichTests(unittest.TestCase):
    def setUp(self):
        self.doc = {"meta": {}, "codes": {"600036.SH": {"sw1": "银行", "sw2": "股份制银行Ⅱ", "source": "verified_profile"}}}
        self.em = {"600016.SH": "金融-银行-股份制与城商行", "601995.SH": "金融-非银行金融-证券",
                   "601600.SH": "有色金属-基本金属-铝", "000001.SZ": "互联网-互联网金融-x"}
        self.calls = []

    def fetch(self, code):
        self.calls.append(code)
        if code not in self.em:
            raise OSError("timeout")
        return self.em[code]

    def snapshot(self, codes):
        return {"pending_profiles": {c: {"code": c[:6], "industry_sw1": None} for c in codes}}

    def test_verified_map_wins_without_network(self):
        snap = self.snapshot(["600036.SH"])
        self.assertEqual(enrich.apply_to_snapshot(snap, self.doc, self.fetch), [])
        self.assertEqual(snap["pending_profiles"]["600036.SH"]["industry_sw1"], "银行")
        self.assertEqual(self.calls, [])

    def test_em2016_maps_to_sw1_and_is_cached(self):
        snap = self.snapshot(["600016.SH", "601995.SH", "601600.SH"])
        self.assertEqual(enrich.apply_to_snapshot(snap, self.doc, self.fetch), [])
        got = {c: p["industry_sw1"] for c, p in snap["pending_profiles"].items()}
        self.assertEqual(got, {"600016.SH": "银行", "601995.SH": "非银金融", "601600.SH": "有色金属"})
        self.assertEqual(snap["pending_profiles"]["600016.SH"]["industry_source"], "em2016_mapped")
        self.assertEqual(self.doc["codes"]["601995.SH"]["em2016"], "金融-非银行金融-证券")

    def test_unmapped_or_failed_stays_null(self):
        snap = self.snapshot(["000001.SZ", "688999.SH"])
        missing = enrich.apply_to_snapshot(snap, self.doc, self.fetch)
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(p["industry_sw1"] is None for p in snap["pending_profiles"].values()))
        self.assertNotIn("000001.SZ", self.doc["codes"])

    def test_mapping_targets_are_official_sw1(self):
        self.assertEqual(len(enrich.SW1), 31)
        self.assertTrue({v for v in enrich.EM_TO_SW1.values() if v} <= enrich.SW1)


if __name__ == "__main__":
    unittest.main()
