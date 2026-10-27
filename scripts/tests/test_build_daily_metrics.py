"""日更指标的离线测试：基准日取最近交易日、52 周窗口、新股留空、股息率财年口径、单只失败不中断。"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_daily_metrics as bm


def bars(start, days, close=10.0, step=0.0):
    out, d = [], date.fromisoformat(start)
    for i in range(days):
        day = d + timedelta(days=i)
        if day.weekday() < 5:
            c = close + step * i
            out.append((day.isoformat(), c, c, c + 1, c - 1))
    return out


class DailyMetricsTests(unittest.TestCase):
    def test_returns_use_last_trading_day_on_or_before_base(self):
        k = bars("2023-10-01", 1200)
        k = [b for b in k if b[0] != "2025-12-31"]  # 年末当天无交易：取 12-30
        base = dict((b[0], b[2]) for b in k)
        m = bm.compute(k, [], 20.0, "2026-10-22")
        self.assertEqual(m["ytd_return"], round((20 / base["2025-12-30"] - 1) * 100, 2))
        self.assertEqual(m["return_1y"], round((20 / base["2025-10-22"] - 1) * 100, 2))
        self.assertIsNotNone(m["return_3y"])

    def test_52w_window_excludes_year_ago_day_and_includes_price(self):
        k = bars("2025-01-01", 660)
        spike = [("2025-10-22", 10, 10, 99, 1)]  # 一年前当天：不应计入
        m = bm.compute(sorted(k + spike), [], 50.0, "2026-10-22")
        self.assertEqual(m["high_52w"], 50.0)
        self.assertEqual(m["low_52w"], 9.0)

    def test_recent_listing_gets_null_long_metrics(self):
        k = bars("2026-03-02", 200)
        m = bm.compute(k, [], 12.0, "2026-10-22")
        self.assertIsNone(m["ytd_return"])
        self.assertIsNone(m["return_1y"])
        self.assertEqual((m["high_52w"], m["low_52w"]), (12.0, 9.0))  # 上市以来，并入今日价

    def test_dividend_yield_uses_latest_fiscal_year_with_annual_payout(self):
        bonus = [
            {"REPORT_DATE": "2026-06-30", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 5},   # 中期，财年未完
            {"REPORT_DATE": "2025-12-31", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 10},
            {"REPORT_DATE": "2025-06-30", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 6},
            {"REPORT_DATE": "2024-12-31", "ASSIGN_PROGRESS": "董事会预案", "PRETAX_BONUS_RMB": 99},
        ]
        m = bm.compute(bars("2023-01-02", 1400), bonus, 16.0, "2026-10-22")
        self.assertEqual((m["dps_fiscal_year"], m["dps_pretax"], m["dividend_yield"]), (2025, 1.6, 10.0))
        # 9 月后上一财年无分红：0；只有更早财年的分红不再顶替
        old_only = [{"REPORT_DATE": "2024-12-31", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 3}]
        self.assertEqual(bm.compute(bars("2023-01-02", 1400), old_only, 16.0, "2026-10-22")["dividend_yield"], 0.0)
        self.assertEqual(bm.compute(bars("2023-01-02", 1400), [], 16.0, "2026-10-22")["dividend_yield"], 0.0)
        # 9 月前上一财年尚未实施：沿用最近年度
        self.assertEqual(bm.compute(bars("2023-01-02", 1250), old_only, 15.0, "2026-05-20")["dps_fiscal_year"], 2024)
        # 上一财年末之后才上市：null
        self.assertIsNone(bm.compute(bars("2026-03-02", 200), [], 12.0, "2026-10-22")["dividend_yield"])

    def test_build_records_failures_per_code(self):
        snap = {"meta": {"quote_date": "2026-10-22", "generated_at": "x"},
                "members": [{"code": "600000.SH", "price": 10}, {"code": "600001.SH", "price": 10}]}
        def fetch(code, price, asof):
            if code == "600001.SH":
                raise OSError("timeout")
            return {"price": price}
        doc = bm.build(snap, fetch)
        self.assertEqual(list(doc["metrics"]), ["600000.SH"])
        self.assertIn("timeout", doc["errors"]["600001.SH"])
        self.assertEqual(doc["meta"]["covered"], 1)


if __name__ == "__main__":
    unittest.main()
