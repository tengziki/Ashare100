"""机构持股的离线测试：报告期选择、剔除 ≥5% 机构、同期缓存与换期重算、无记录不记 0。"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_inst_hold as ih


TOTALS = [{"ORG_TYPE": "00", "TOTALSHARES_RATIO": 67.3}, {"ORG_TYPE": "01", "TOTALSHARES_RATIO": 3.4},
          {"ORG_TYPE": "05", "TOTALSHARES_RATIO": 58.8}, {"ORG_TYPE": "07", "TOTALSHARES_RATIO": 5.1}]
BIG = [{"HOLDER_NAME": "中国平安保险(集团)股份有限公司-集团本级-自有资金", "ORG_TYPE": "05", "TOTALSHARES_RATIO": 49.56},
       {"HOLDER_NAME": "中国平安人寿保险股份有限公司-自有资金", "ORG_TYPE": "05", "TOTALSHARES_RATIO": 6.11}]


class InstHoldTests(unittest.TestCase):
    def test_report_date_uses_complete_half_year_or_annual(self):
        self.assertEqual(ih.report_date(date(2026, 10, 30)), "2026-06-30")
        self.assertEqual(ih.report_date(date(2026, 8, 31)), "2025-12-31")
        self.assertEqual(ih.report_date(date(2026, 4, 1)), "2025-12-31")
        self.assertEqual(ih.report_date(date(2026, 3, 31)), "2025-06-30")

    def test_strategic_holders_are_excluded(self):
        value, excluded = ih.ratio(TOTALS, BIG)
        self.assertEqual(value, round(3.4 + 58.8 - 49.56 - 6.11, 2))  # 07 其他、00 汇总都不计
        self.assertEqual([x["type"] for x in excluded], ["保险", "保险"])
        self.assertEqual(ih.ratio([], []), (None, []))

    def test_cache_same_period_and_recompute_on_new_period(self):
        calls = []

        def fake(code, period):
            calls.append((code, period))
            return TOTALS[:2], []
        prev = {"meta": {"report_date": "2026-06-30"}, "companies": {"A": {"ratio": 1.0, "excluded": []}}}
        doc, problems = ih.build(["A", "B"], prev, date(2026, 10, 30), fake, workers=1)
        self.assertEqual((doc["companies"]["A"]["ratio"], doc["companies"]["B"]["ratio"], calls), (1.0, 3.4, [("B", "2026-06-30")]))
        calls.clear()
        doc, _ = ih.build(["A"], prev, date(2027, 4, 2), fake, workers=1)
        self.assertEqual((doc["meta"]["report_date"], calls), ("2026-12-31", [("A", "2026-12-31")]))

    def test_failure_is_reported_not_zero(self):
        def broken(code, period):
            raise OSError("timeout")
        doc, problems = ih.build(["A"], {}, date(2026, 10, 30), broken, workers=1)
        self.assertEqual(doc["companies"], {})
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
