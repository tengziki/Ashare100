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

    def test_prev_close_base_is_independent_of_same_day_runs(self):
        self.quotes[self.codes[100]]["total_mv"] = 2000.0
        base = lambda label, codes: {"snapshot": label, "label": label,
                                     "ranked": [{"code": c, "name": "旧" + c, "rank": i} for i, c in enumerate(codes, 1)]}
        same_day = ["{:06d}.SZ".format(i) for i in [100] + list(range(99))]
        prev_close = self.old + ["000100.SZ"]  # 昨日收盘：000100 排第 101
        result = live.build_snapshot(self.codes, self.quotes, same_day, set(self.old), "noon", self.now,
                                     {"prev_run": base("今日 12:00", same_day), "prev_close": base("昨日收盘", prev_close)})
        run, close = result["changes"]["vs_prev_run"], result["changes"]["vs_prev_close"]
        self.assertEqual((run["entered"], run["exited"]), ([], []))
        self.assertEqual(close["base_label"], "昨日收盘")
        self.assertEqual(close["entered"], [{"code": "000100.SZ", "name": "公司000100", "rank": 1, "prev_rank": 101}])
        self.assertEqual(close["exited"][0]["code"], "000099.SZ")
        self.assertEqual(close["exited"][0]["prev_rank"], 100)
        self.assertEqual(close["exited"][0]["now_rank"], 101)
        self.assertEqual(close["exited"][0]["name"], "公司000099")
        self.assertEqual(result["watchlist"][0]["code"], "000099.SZ")

    def test_load_bases_picks_latest_previous_close(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "snapshots").mkdir()
            def write(name, date, status, first):
                doc = {"meta": {"quote_date": date, "ranking_status": status, "quote_max_at": date + "T12:00:00+08:00"},
                       "members": [{"code": first, "name": "x", "rank": 1}]}
                (output / "snapshots" / name).write_text(json.dumps(doc), encoding="utf-8")
            write("a.json", "2026-10-23", "close_confirmed", "000001.SZ")
            write("b.json", "2026-10-26", "close_confirmed", "000002.SZ")
            write("c.json", "2026-10-27", "close_confirmed", "000003.SZ")
            write("d.json", "2026-10-27", "intraday_provisional", "000004.SZ")
            (output / "manifest.json").write_text('{"snapshot":"snapshots/d.json"}', encoding="utf-8")
            bases = live.load_bases(output, self.now.date(), prev_close_fallback=lambda d: self.fail("有自存收盘快照时不应再取东财"))
            self.assertEqual(bases["prev_close"]["snapshot"], "snapshots/b.json")
            self.assertEqual(bases["prev_close"]["label"], "2026-10-26 收盘")
            self.assertEqual(bases["prev_run"]["snapshot"], "snapshots/d.json")

    def test_cold_start_uses_em_previous_close_then_static(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            em = {"snapshot": None, "label": "2026-10-26 收盘（东方财富日估值）", "ranked": [{"code": "000001.SZ", "name": "x", "rank": 1}]}
            self.assertEqual(live.load_bases(output, self.now.date(), lambda d: em)["prev_close"]["label"], em["label"])
            def down(d):
                raise OSError("timeout")
            self.assertIn("静态基准", live.load_bases(output, self.now.date(), down)["prev_close"]["label"])

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
