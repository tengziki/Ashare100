"""自动复核的离线测试：摘要解析、交叉核对通过才转正、任一项不符保持待复核。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_review as ar


SUMMARY = """
  4、 天健会计师事务所(特殊普通合伙)为本公司出具了标准无保留意见的审计报告。
  主要会计数据   营业收入   14,190,018    14,290,193
  2025 年，公司实现营业收入 555.42 亿元，较去年增加 52.69 亿元。实现归属于母公司股东
  的净利润 218.07 亿元，同比增长 8.08%。
"""


def profile(revenue=55541916000.0, net_profit=21807089000.0):
    annual = [{"fiscal_year": y} for y in range(2021, 2025)] + [
        {"fiscal_year": 2025, "report_date": "2025-12-31", "revenue": revenue, "net_profit": net_profit}]
    return {"identity": {"code": "601009", "exchange": "SH", "name_abbr": "南京银行", "audit_opinion": None,
                         "industry_sw1": "银行", "board": "主板"},
            "financials": {"annual": annual, "latest": {"report_period": "2026-06-30"}},
            "shareholders": {"holder_num_date": "2026-06-30"},
            "dividends": {"annual": [{"fiscal_year": 2024, "dps_pretax": 0.5}, {"fiscal_year": 2025, "dps_pretax": 0.5292}]},
            "announcements": {"items": [{"title": "公告"}]}, "qa": {"items": []},
            "_extras": {"qa": {"error": None}}, "_meta": {"errors": {}, "source": "东财", "fetched_at": "2026-10-29T00:00:00+08:00"}}


NOTICES = {"n1": "每股现金红利人民币0.3062元（含税） 每股派发现金红利人民币0.3062元（含税）",
           "n2": "本次利润分配以方案实施前总股本为基数，每10股派发现金红利2.2295元（含税）"}


class AutoReviewTests(unittest.TestCase):
    def test_dividend_notice_parsing_and_titles(self):
        self.assertEqual(ar.parse_dps(NOTICES["n1"]), 0.3062)
        self.assertEqual(ar.parse_dps(NOTICES["n2"]), 0.22295)
        self.assertIsNone(ar.parse_dps("无"))
        for title, year in (("南京银行2025年半年度权益分派实施公告", 2025), ("2025年年度权益分派实施公告", 2025),
                            ("贵州茅台2025年中期权益分派实施公告", 2025), ("2024年度利润分配实施公告", 2024)):
            self.assertEqual(int(ar.NOTICE_RE.search(title)[1]), year)

    def test_dividend_mismatch_or_missing_notice_fails(self):
        p = profile()
        p["dividends"]["annual"][-1]["dps_pretax"] = 0.6
        promoted, problems, _ = self.run_one(p)
        self.assertEqual(promoted, [])
        self.assertIn("每股派息", problems[0])
        promoted, problems, _ = self.run_one(profile(), notices={})
        self.assertIn("找不到或读不出", problems[0])
        p = profile()
        p["dividends"]["annual"] = [{"fiscal_year": 2025, "dps_pretax": None}]
        self.assertEqual(self.run_one(p, notices={})[0], ["601009.SH"])  # 未分红：不核对

    def test_parse_reads_narrative_not_table(self):
        got = ar.parse_summary(SUMMARY)
        self.assertEqual(got["revenue"], 555.42e8)
        self.assertEqual(got["net_profit"], 218.07e8)  # 跨行的句子也能读到
        self.assertEqual(got["audit_opinion"], "标准无保留意见")
        self.assertEqual(ar.parse_summary("审计意见为：标准的无保留意见。实现营业总收入451.05亿元")["audit_opinion"], "标准无保留意见")
        self.assertIsNone(ar.parse_summary("主要会计数据 营业收入 1,000")["revenue"])

    def run_one(self, prof, text=SUMMARY, notices=None):
        notices = NOTICES if notices is None else notices
        read = lambda url: notices.get(url, text)
        with tempfile.TemporaryDirectory() as tmp:
            auto, verified = Path(tmp) / "auto", Path(tmp) / "verified"
            auto.mkdir()
            (auto / "601009.SH.json").write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
            promoted, problems = ar.run(["601009.SH"], {"601009.SH": "x"}, lambda c, o, y: ("2025年年度报告摘要", "u"),
                                        read, auto, verified, lambda c, o, y: [(k, k) for k in notices if y == 2025])
            out = verified / "601009.SH.json"
            return promoted, problems, json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    def test_matching_profile_is_promoted_as_auto(self):
        promoted, problems, doc = self.run_one(profile())
        self.assertEqual((promoted, problems), (["601009.SH"], []))
        self.assertEqual(doc["review"]["method"], "auto")
        self.assertEqual(doc["review"]["sources"], ["u"])
        self.assertEqual(doc["identity"]["audit_opinion"], "标准无保留意见")
        self.assertNotIn("audit_value", doc["review"]["checks"])

    def test_mismatch_or_unreadable_stays_pending(self):
        promoted, problems, doc = self.run_one(profile(revenue=60e9))
        self.assertEqual(promoted, [])
        self.assertIn("营业收入", problems[0])
        self.assertIsNone(doc)
        promoted, problems, _ = self.run_one(profile(), text="没有叙述句的摘要")
        self.assertEqual(promoted, [])
        self.assertIn("未读到", problems[0])

    def test_stale_holders_or_qa_error_fail(self):
        p = profile()
        p["financials"]["latest"]["report_period"] = "2026-09-30"
        p["_extras"]["qa"]["error"] = "TimeoutError"
        promoted, problems, _ = self.run_one(p)
        self.assertEqual(promoted, [])
        self.assertIn("股东户数", problems[0])
        self.assertIn("问答抓取失败", problems[0])


if __name__ == "__main__":
    unittest.main()
