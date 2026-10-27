"""档案检查：硬规则与合理性规则分级；复核转正不覆盖已核实档案、拒绝有抓取失败的档案。"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profiles_store as store
import validate_profiles as vp


def company(**over):
    c = {"identity": {"code": "600016", "exchange": "SH", "name_abbr": "民生银行", "industry_sw1": "银行",
                      "board": "主板", "list_date": "2000-12-19"},
         "financials": {"annual": [{"fiscal_year": 2024, "revenue": 1.4e11, "roe": 5.0, "debt_ratio": 91.0},
                                   {"fiscal_year": 2025, "revenue": 1.4e11, "roe": 4.9, "debt_ratio": 91.2}],
                        "latest": {"report_period": "2026-06-30"}},
         "shareholders": {"northbound_ratio": 2.3, "holder_num_date": "2026-06-30", "top10": [{"hold_ratio": 18.9}]},
         "dividends": {"annual": [{"fiscal_year": 2025, "dps_pretax": 0.189}]}}
    for path, value in over.items():
        node = c
        *keys, last = path.split(".")
        for k in keys:
            node = node[int(k)] if k.isdigit() else node[k]
        node[int(last) if last.isdigit() else last] = value
    return c


TODAY = date(2026, 10, 27)


class ValidateTests(unittest.TestCase):
    def levels(self, c, code="600016.SH"):
        return {(f, hard) for f, _, hard in vp.check(code, c, TODAY)}

    def test_clean_profile_passes(self):
        self.assertEqual(vp.check("600016.SH", company(), TODAY), [])

    def test_hard_rules(self):
        self.assertIn(("identity.code", True), self.levels(company(), "601995.SH"))
        self.assertIn(("identity.industry_sw1", True), self.levels(company(**{"identity.industry_sw1": "电新"})))
        self.assertIn(("financials.latest.report_period", True), self.levels(company(**{"financials.latest.report_period": "2026-12-31"})))
        self.assertIn(("financials.annual", True), self.levels(company(**{"financials.annual.1.fiscal_year": 2024})))
        self.assertIn(("shareholders.northbound_ratio", True), self.levels(company(**{"shareholders.northbound_ratio": 120})))
        self.assertIn(("financials.2025.revenue", True), self.levels(company(**{"financials.annual.1.revenue": -5})))

    def test_plausibility_rules_are_soft(self):
        got = self.levels(company(**{"financials.annual.1.roe": -632.6, "financials.annual.0.revenue": 4.6e7}))
        self.assertEqual(got, {("financials.2025.roe", False), ("financials.2024.revenue", False)})


class PromoteTests(unittest.TestCase):
    def test_promote_moves_reviewed_profile_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, verified = Path(directory, "auto"), Path(directory, "verified")
            auto.mkdir()
            (auto / "600016.SH.json").write_text(json.dumps({**company(), "_meta": {"errors": {}, "source": "F10"}}), encoding="utf-8")
            target = store.promote("600016.SH", "张三", "核对年报", auto, verified)
            doc = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotIn("_meta", doc)
            self.assertEqual((doc["review"]["reviewer"], doc["review"]["source"]), ("张三", "F10"))
            with self.assertRaisesRegex(ValueError, "不覆盖"):
                store.promote("600016.SH", "李四", None, auto, verified)

    def test_promote_refuses_profile_with_failed_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            auto = Path(directory)
            (auto / "600016.SH.json").write_text(json.dumps({**company(), "_meta": {"errors": {"bonus": "timeout"}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "抓取失败"):
                store.promote("600016.SH", "张三", None, auto, auto / "v")


if __name__ == "__main__":
    unittest.main()
