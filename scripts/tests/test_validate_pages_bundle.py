"""发布校验应允许日更指标落后于同日换榜，但拒绝当前版本的错误成员。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_pages_bundle as bundle


class MetricsSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.web = Path(self.temp.name)
        self.snapshot = "snapshots/close.json"
        for name in bundle.REQUIRED:
            (self.web / name).touch()
        self.write("live/manifest.json", {
            "snapshot": self.snapshot, "quote_date": "2026-11-02", "member_count": 100,
        })
        self.write("live/" + self.snapshot, {
            "meta": {"quote_date": "2026-11-02", "ranking_metric": "total_mv_CNY",
                     "member_count": 100, "listed_count": 5576, "quoted_count": 5576},
            "members": [{"code": f"{i:06d}.SZ", "rank": i, "total_mv": 1000 - i}
                        for i in range(1, 101)],
        })
        self.patcher = patch.object(bundle, "WEB", self.web)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self, path, value):
        target = self.web / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def metrics(self, snapshot, date="2026-11-02", code="000101.SZ", pointer_date=None):
        self.write("live/metrics/latest.json", {
            "file": "metrics/20261102.json", "quote_date": pointer_date or date,
            "snapshot": snapshot,
        })
        self.write("live/metrics/20261102.json", {
            "meta": {"quote_date": date}, "metrics": {code: {}},
        })

    def test_same_day_noon_metrics_do_not_block_close_membership_change(self):
        self.metrics("snapshots/noon.json")
        bundle.validate()

    def test_current_snapshot_rejects_non_member(self):
        self.metrics(self.snapshot)
        with self.assertRaisesRegex(ValueError, "日更指标含非百强代码"):
            bundle.validate()

    def test_current_snapshot_accepts_member(self):
        self.metrics(self.snapshot, code="000100.SZ")
        bundle.validate()

    def test_previous_day_metrics_do_not_block_new_members(self):
        self.metrics("snapshots/previous.json", date="2026-10-30")
        bundle.validate()

    def test_future_metrics_are_rejected_even_for_another_snapshot(self):
        self.metrics("snapshots/future.json", date="2026-11-03")
        with self.assertRaisesRegex(ValueError, "日更指标日期晚于快照"):
            bundle.validate()

    def test_pointer_date_must_match_metrics(self):
        self.metrics("snapshots/noon.json", pointer_date="2026-10-30")
        with self.assertRaisesRegex(ValueError, "日更指标与指针不一致"):
            bundle.validate()


if __name__ == "__main__":
    unittest.main()
