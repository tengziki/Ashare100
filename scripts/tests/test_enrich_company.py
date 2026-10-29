"""自动补档的离线测试：字段换算、分红年度归属、失败组不写假值、缓存。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrich_company as ec


def annual_row(year, revenue, profit):
    return {"REPORT_YEAR": str(year), "REPORT_TYPE": "年报", "REPORT_DATE": f"{year}-12-31 00:00:00",
            "TOTALOPERATEREVE": revenue, "PARENTNETPROFIT": profit, "ROEJQ": 10.5, "EPSJB": 1.2,
            "ZCFZL": 50.0, "LIABILITY": 500, "XSJLL": 20.0}


RAW = {
    "survey": {"jbzl": [{"SECUCODE": "600016.SH", "ORG_NAME": "中国民生银行股份有限公司", "CHAIRMAN": "高迎欣",
                         "EMP_NUM": "60394", "PROVINCE": "北京", "BUSINESS_SCOPE": "吸收公众存款"}],
               "fxxg": [{"LISTING_DATE": "2000-12-19 00:00:00"}]},
    "annual": {"data": [annual_row(y, 1000 + y - 2021, 100) for y in range(2020, 2026)]},
    "latest": {"data": [{"REPORT_YEAR": "2026", "REPORT_TYPE": "中报", "REPORT_DATE": "2026-06-30 00:00:00",
                         "TOTALOPERATEREVE": 600, "PARENTNETPROFIT": 60}]},
    "holders": {"gdrs": [{"END_DATE": "2026-06-30 00:00:00", "HOLDER_TOTAL_NUM": 300000, "TOTAL_NUM_RATIO": -2.1}],
                "sdgd": [{"HOLDER_RANK": 1, "HOLDER_NAME": "香港中央结算(代理人)有限公司", "HOLD_NUM": 9, "HOLD_NUM_RATIO": 18.9, "HOLD_NUM_CHANGE": "214472"},
                         {"HOLDER_RANK": 2, "HOLDER_NAME": "香港中央结算有限公司", "HOLD_NUM": 5, "HOLD_NUM_RATIO": 2.3, "HOLD_NUM_CHANGE": "不变"},
                         {"HOLDER_RANK": 3, "HOLDER_NAME": "戴思元", "HOLD_NUM": 4, "HOLD_NUM_RATIO": 1.0, "HOLD_NUM_CHANGE": "新进"}],
                "sjkzr": [{"HOLDER_NAME": None}]},
    "bonus": {"result": {"data": [
        {"REPORT_DATE": "2026-06-30", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 1, "TOTAL_SHARES": 10},
        {"REPORT_DATE": "2025-12-31", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 2, "TOTAL_SHARES": 10, "EX_DIVIDEND_DATE": "2026-07-10"},
        {"REPORT_DATE": "2025-06-30", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 1, "TOTAL_SHARES": 10, "EX_DIVIDEND_DATE": "2026-01-16"},
        {"REPORT_DATE": "2024-12-31", "ASSIGN_PROGRESS": "董事会预案", "PRETAX_BONUS_RMB": 9, "TOTAL_SHARES": 10},
        {"REPORT_DATE": "2023-12-31", "ASSIGN_PROGRESS": "实施分配", "PRETAX_BONUS_RMB": 3, "TOTAL_SHARES": 10},
    ]}},
}
BASE = {"name_abbr": "民生银行", "board": "主板", "industry_sw1": "银行"}


class CompanyEnrichTests(unittest.TestCase):
    def test_profile_fields_and_dividend_years(self):
        p = ec.build_profile("600016.SH", BASE, lambda code: (RAW, {}))
        self.assertEqual(p["identity"]["industry_sw1"], "银行")
        self.assertEqual(p["identity"]["list_date"], "2000-12-19")
        self.assertEqual(p["identity"]["employees"], 60394)
        a25 = p["financials"]["annual"][4]
        self.assertEqual((a25["fiscal_year"], a25["revenue"], a25["roe"], a25["total_assets"]), (2025, 1004, 10.5, 1000))
        self.assertIsNone(a25["ocf_to_np"])
        self.assertEqual(p["financials"]["latest"]["report_type"], "2026年中报")
        self.assertEqual(p["shareholders"]["northbound_ratio"], 2.3)  # 不是「代理人」账户
        self.assertEqual([t["hold_num_change"] for t in p["shareholders"]["top10"]], [214472, 0, None])
        self.assertIsNone(p["shareholders"]["inst_hold_ratio"])
        dps = {a["fiscal_year"]: a["dps_pretax"] for a in p["dividends"]["annual"]}
        # 2025 = 中期 + 年度；2024 只有预案 → 0；2021–2022 在来源覆盖之前 → null
        self.assertEqual(dps, {2021: None, 2022: None, 2023: 0.3, 2024: 0.0, 2025: 0.3})
        self.assertEqual(p["dividends"]["consecutive_years"], 2)  # 2026 中期 + 2025
        self.assertIsNone(p["dividends"]["cum_dividend_5y"])

    def test_summary_segments_and_cashflow(self):
        self.assertEqual(ec.summary_of("茅台酒及系列酒的生产与销售;饮料、食品的生产"), "茅台酒及系列酒的生产与销售")
        self.assertEqual(ec.summary_of("(一)吸收公众存款;(二)发放短期贷款"), "吸收公众存款")
        self.assertEqual(ec.summary_of("一般项目:工业自动控制系统装置制造;销售"), "工业自动控制系统装置制造")
        self.assertEqual(ec.summary_of("许可项目：发电业务；一般项目：储能"), "发电业务")
        self.assertIsNone(ec.summary_of(""))
        self.assertEqual(ec.summary_of("许可项目:医护人员防护用品生产(依法须经批准的项目)一般项目:光电子器件制造;玻璃制造"), "光电子器件制造")
        self.assertEqual(ec.summary_of("生产机械;销售", "公司主要从事工程机械的研发、制造、销售和服务。"), "工程机械的研发、制造、销售和服务")
        raw = dict(RAW, business={"zygcfx": [
            {"REPORT_DATE": "2026-06-30 00:00:00", "MAINOP_TYPE": "2", "ITEM_NAME": "中报产品", "MAIN_BUSINESS_INCOME": 1, "MBI_RATIO": 1},
            {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "1", "ITEM_NAME": "按行业", "MAIN_BUSINESS_INCOME": 9, "MBI_RATIO": 1},
            {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "2", "ITEM_NAME": "存贷款", "MAIN_BUSINESS_INCOME": 800, "MBI_RATIO": 0.8},
            {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "3", "ITEM_NAME": "江苏", "MAIN_BUSINESS_INCOME": 1000, "MBI_RATIO": 1.0}]},
            cashflow=[{"REPORT_DATE": "2025-12-31 00:00:00", "NETCASH_OPERATE": 123.4},
                      {"REPORT_DATE": "2026-06-30 00:00:00", "NETCASH_OPERATE": 56.0}])
        p = ec.build_profile("600016.SH", BASE, lambda code: (raw, {}))
        self.assertEqual([(x["name"], x["type"], x["ratio"], x["period"]) for x in p["peers"]["segments"]],
                         [("存贷款", "product", 80.0, "2025年报"), ("江苏", "region", 100.0, "2025年报")])
        self.assertEqual(p["financials"]["annual"][4]["ocf"], 123.4)
        self.assertIsNone(p["financials"]["annual"][3]["ocf"])  # 来源没有该期：留空，不填 0
        self.assertEqual(p["financials"]["latest"]["ocf"], 56.0)

    def test_failed_group_stays_empty_not_zero(self):
        raw = {k: v for k, v in RAW.items() if k != "bonus"}
        p = ec.build_profile("600016.SH", BASE, lambda code: (raw, {"bonus": "timeout"}))
        self.assertTrue(all(a["dps_pretax"] is None for a in p["dividends"]["annual"]))
        self.assertIsNone(p["dividends"]["consecutive_years"])
        self.assertEqual(p["_meta"]["errors"], {"bonus": "timeout"})

    def test_null_result_from_source_is_no_dividend_data(self):
        raw = dict(RAW, bonus={"result": None, "success": False}, latest={"data": None})
        p = ec.build_profile("600016.SH", BASE, lambda code: (raw, {}))
        self.assertTrue(all(a["dps_pretax"] is None for a in p["dividends"]["annual"]))
        self.assertEqual(p["financials"]["latest"], {})

    def test_batch_records_unexpected_errors_instead_of_raising(self):
        def broken(code):
            raise AttributeError("boom")
        with tempfile.TemporaryDirectory() as directory:
            snap = {"pending_profiles": {"600016.SH": dict(BASE)}}
            problems = ec.apply_profiles(snap, broken, Path(directory), lambda c, p: (False, []))
        self.assertEqual(len(problems), 1)
        self.assertNotIn("profile", snap["pending_profiles"]["600016.SH"])

    def test_wrong_code_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "代码不符"):
            ec.build_profile("601995.SH", BASE, lambda code: (RAW, {}))

    def test_cache_reused_within_days_and_refreshed_after(self):
        calls = []
        def fetch(code):
            calls.append(code)
            return RAW, {}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            now = datetime.now(ec.CN_TZ)
            snap = {"pending_profiles": {"600016.SH": dict(BASE)}}
            extras = lambda code, profile: (profile.update(_extras={}, qa={"items": [{"question": "q"}]}), [])
            self.assertEqual(ec.apply_profiles(snap, fetch, cache, extras), [])
            self.assertEqual(json.loads((cache / "600016.SH.json").read_text(encoding="utf-8"))["qa"]["items"][0]["question"], "q")
            self.assertIn("profile", snap["pending_profiles"]["600016.SH"])
            ec.cached_profile("600016.SH", BASE, fetch, cache, now + timedelta(days=1))
            self.assertEqual(len(calls), 1)
            ec.cached_profile("600016.SH", BASE, fetch, cache, now + timedelta(days=8))
            self.assertEqual(len(calls), 2)
            # 重建档案时沿用已获取的问答
            rebuilt = json.loads((cache / "600016.SH.json").read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["qa"]["items"][0]["question"], "q")
            self.assertTrue(json.loads((cache / "600016.SH.json").read_text(encoding="utf-8"))["identity"])


class WatchDocTests(unittest.TestCase):
    def test_watch_docs_use_verified_then_auto_and_drop_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, cache = Path(tmp) / "live", Path(tmp) / "cache"
            cache.mkdir()
            (cache / "002050.SZ.json").write_text(json.dumps({"identity": {"name_abbr": "三花智控"}, "_meta": {}}), encoding="utf-8")
            (live / "watch").mkdir(parents=True)
            (live / "watch" / "000000.SZ.json").write_text("{}", encoding="utf-8")
            snap = {"meta": {"quote_date": "2026-10-29"}, "members": [{"code": "300033.SZ", "name": "同花顺", "total_mv": 151.0}],
                    "watchlist": [{"code": "688808.SH", "name": "联讯仪器", "rank": 101, "total_mv": 146.0},
                                  {"code": "002050.SZ", "name": "三花智控", "rank": 103, "total_mv": 145.0}]}
            written = ec.write_watch_docs(snap, {"sz002050": {"price": 30.1, "code": "002050.SZ"}}, live,
                                          {"688808.SH": {"identity": {"name_abbr": "联讯仪器"}}}, cache_dir=cache)
            self.assertEqual(written, ["688808.SH", "002050.SZ"])
            self.assertEqual(sorted(f.name for f in (live / "watch").iterdir()), ["002050.SZ.json", "688808.SH.json"])
            doc = json.loads((live / "watch" / "002050.SZ.json").read_text(encoding="utf-8"))
            self.assertEqual((doc["profile_status"], doc["gap_to_100"], doc["market"]["price"]), ("auto_profile", 6.0, 30.1))
            self.assertNotIn("_meta", doc["profile"])
            first = json.loads((live / "watch" / "688808.SH.json").read_text(encoding="utf-8"))
            self.assertEqual(first["profile_status"], "verified")


if __name__ == "__main__":
    unittest.main()
