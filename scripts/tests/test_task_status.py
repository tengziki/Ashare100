"""任务状态：按任务覆盖写入、保留其他任务、问题清单截断。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_status as ts


class TaskStatusTests(unittest.TestCase):
    def test_record_keeps_other_tasks_and_truncates_problems(self):
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory)
            t1 = datetime(2026, 10, 27, 15, 6, tzinfo=ts.CN_TZ)
            ts.record("live_universe", "2026-10-27", detail="收盘确认", live_dir=live, now=t1)
            ts.record("daily_metrics", "2026-10-27", [f"p{i}" for i in range(30)], live_dir=live)
            doc = json.loads((live / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["tasks"]["live_universe"]["last_success"], "2026-10-27T15:06:00+08:00")
            self.assertEqual(doc["tasks"]["live_universe"]["warn_hours"], 28)
            self.assertEqual(len(doc["tasks"]["daily_metrics"]["problems"]), 20)
            ts.record("live_universe", "2026-10-28", live_dir=live)
            doc = json.loads((live / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["tasks"]["live_universe"]["data_date"], "2026-10-28")
            self.assertIn("daily_metrics", doc["tasks"])


if __name__ == "__main__":
    unittest.main()
