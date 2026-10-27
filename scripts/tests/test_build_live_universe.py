"""关键质量闸门和第 100/101 名交换的受控测试。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_live_universe as live


class DynamicUniverseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 12, 5, tzinfo=live.CN_TZ)
        self.codes = ["sz{:06d}".format(i) for i in range(101)]
        self.old = ["{:06d}.SZ".format(i) for i in range(100)]
        self.quotes = {
            code: {
                "code": code[2:] + ".SZ", "name": "公司" + code[2:],
                "price": 10.0, "total_mv": float(1000 - i),
                "float_mv": float(500 - i), "quote_at": "20261027113000",
            }
            for i, code in enumerate(self.codes)
        }

    def test_rank_boundary_changes_membership_and_marks_new_profile(self):
        self.quotes[self.codes[100]]["total_mv"] = 2000.0
        result = live.build_snapshot(
            self.codes, self.quotes, self.old, set(self.old), "noon", self.now)
        self.assertEqual(result["members"][0]["code"], "000100.SZ")
        self.assertEqual(result["members"][0]["rank"], 1)
        self.assertEqual(result["changes"]["entered"], ["000100.SZ"])
        self.assertEqual(result["changes"]["exited"], ["000099.SZ"])
        self.assertEqual(result["pending_profiles"]["000100.SZ"]["status"], "待补完整公司档案")
        self.assertEqual(result["meta"]["member_count"], 100)

    def test_incomplete_market_does_not_publish_over_old_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "manifest.json").write_text('{"snapshot":"old.json"}', encoding="utf-8")
            del self.quotes[self.codes[100]]
            with self.assertRaisesRegex(ValueError, "行情不完整"):
                live.build_snapshot(self.codes, self.quotes, self.old, set(), "noon", self.now)
            self.assertEqual(json.loads((output / "manifest.json").read_text())["snapshot"], "old.json")

    def test_same_count_with_wrong_code_is_rejected(self):
        self.quotes["sh999999"] = self.quotes.pop(self.codes[100])
        with self.assertRaisesRegex(ValueError, "行情不完整"):
            live.build_snapshot(self.codes, self.quotes, self.old, set(), "noon", self.now)

    def test_old_or_mixed_quote_dates_never_become_today_snapshot(self):
        for row in self.quotes.values():
            row["quote_at"] = "20261026150000"
        self.assertIsNone(live.build_snapshot(
            self.codes, self.quotes, self.old, set(), "noon", self.now))
        self.quotes[self.codes[0]]["quote_at"] = "20261027113000"
        with self.assertRaisesRegex(ValueError, "报价日期混杂"):
            live.build_snapshot(self.codes, self.quotes, self.old, set(), "noon", self.now)

    def test_close_requires_fresh_post_close_quotes(self):
        with self.assertRaisesRegex(ValueError, "收盘行情尚未到齐"):
            live.build_snapshot(self.codes, self.quotes, self.old, set(), "close", self.now)


if __name__ == "__main__":
    unittest.main()
