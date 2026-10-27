"""每日两更公告问答：问答合并保留旧回复、单家失败沿用上一版、只看当日更新算覆盖。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_disclosures as ud

NOW = datetime(2026, 10, 27, 15, 5, tzinfo=ud.CN_TZ)


class DisclosureTests(unittest.TestCase):
    def test_merge_qa_adds_new_updates_answers_keeps_old_answers(self):
        old = [{"ask_date": "2026-10-20", "question": "A", "answer": None},
               {"ask_date": "2026-10-10", "question": "B", "answer": "已回复 B"}]
        new = [{"ask_date": "2026-10-26", "question": "C", "answer": None},
               {"ask_date": "2026-10-20", "question": "A", "answer": "回复 A", "answer_date": "2026-10-27"},
               {"ask_date": "2026-10-10", "question": "B", "answer": None}]
        got = ud.merge_qa(old, new)
        self.assertEqual([q["question"] for q in got], ["C", "A", "B"])
        self.assertEqual(got[1]["answer"], "回复 A")
        self.assertEqual(got[2]["answer"], "已回复 B")
        self.assertEqual(len(ud.merge_qa([], [{"ask_date": f"2026-10-{i:02d}", "question": str(i)} for i in range(1, 20)])), 8)

    def test_failed_company_keeps_previous_entry(self):
        previous = {"600000.SH": {"announcements": {"items": [{"title": "旧"}]}, "announcements_at": "2026-10-26T15:00:00+08:00"}}
        def fetch(code, prev, ids, now):
            if code == "600000.SH":
                return dict(prev), ["600000.SH 公告：timeout"]
            return {"announcements": {"items": [{"title": "新"}]}, "announcements_at": now.isoformat()}, []
        doc, problems = ud.build(["600000.SH", "600036.SH"], previous, {}, NOW, fetch)
        self.assertEqual(doc["companies"]["600000.SH"]["announcements"]["items"][0]["title"], "旧")
        self.assertEqual(doc["meta"]["announcements_fresh"], 1)
        self.assertEqual(problems, ["600000.SH 公告：timeout"])


if __name__ == "__main__":
    unittest.main()
