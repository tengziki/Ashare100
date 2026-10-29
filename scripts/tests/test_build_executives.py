"""高管更新的离线测试：角色识别（含行长、代理、副职排除）、空报酬区分新任、缓存与失败沿用。"""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_executives as be


CN = timezone(timedelta(hours=8))
ROWS = [
    {"PERSON_NAME": "陈华", "POSITION": "董事长,法定代表人,董事", "SALARY": None, "INCUMBENT_TIME": "2025-11-28至今"},
    {"PERSON_NAME": "王莉", "POSITION": "代理总经理,董事", "SALARY": None, "INCUMBENT_TIME": "2024-03-01至今"},
    {"PERSON_NAME": "张旭", "POSITION": "副总经理", "SALARY": "574400", "INCUMBENT_TIME": "2020-01-01至今"},
    {"PERSON_NAME": "余思明", "POSITION": "董事会秘书,财务总监", "SALARY": "1000000", "INCUMBENT_TIME": "2023-01-01至今"},
]


class ExecutiveTests(unittest.TestCase):
    def test_roles_acting_and_pay_notes(self):
        got = be.pick(ROWS, 2025)
        self.assertEqual([(e["title"], e["name"]) for e in got], [("董事长", "陈华"), ("总经理", "王莉(代)"), ("董事会秘书", "余思明")])
        self.assertEqual([e["pay_note"] for e in got], ["新任", "未在公司领薪", None])
        self.assertEqual(got[2]["pay"], 1000000.0)

    def test_bank_president_and_vice_excluded(self):
        rows = [{"PERSON_NAME": "甲", "POSITION": "副行长"}, {"PERSON_NAME": "朱钢", "POSITION": "行长,执行董事", "SALARY": "720000"},
                {"PERSON_NAME": "乙", "POSITION": "副董事长"}]
        got = be.pick(rows, 2025)
        self.assertEqual([(e["title"], e["name"]) for e in got], [("总经理", "朱钢")])

    def test_pay_year_follows_annual_report_season(self):
        self.assertEqual(be.pay_year(date(2026, 10, 30)), 2025)
        self.assertEqual(be.pay_year(date(2026, 4, 30)), 2024)

    def test_cache_refresh_and_failure_keeps_previous(self):
        now = datetime(2026, 10, 30, 12, tzinfo=CN)
        prev = {"meta": {"pay_year": 2025}, "companies": {
            "A": {"executives": [{"name": "旧"}], "fetched_at": (now - timedelta(hours=2)).isoformat()},
            "B": {"executives": [{"name": "旧B"}], "fetched_at": (now - timedelta(days=2)).isoformat()}}}
        calls = []

        def fake(code):
            calls.append(code)
            raise OSError("timeout")
        doc, problems = be.build(["A", "B"], prev, now, fake, workers=1)
        self.assertEqual(calls, ["B"])  # A 在 20 小时内不重抓
        self.assertEqual(doc["companies"]["B"]["executives"][0]["name"], "旧B")  # 失败沿用上一版
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
