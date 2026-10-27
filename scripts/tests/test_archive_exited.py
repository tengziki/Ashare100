"""出榜历史档：退出时冻结档案与行情、重复运行不覆盖、重新进榜移出索引、超期精简。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_exited as arch


STATIC = {"300033.SZ": {"identity": {"name_abbr": "同花顺"}, "financials": {"annual": []},
                        "market": {"price": 300.0, "total_mv": 1.6e11, "as_of_date": "2026-10-22"}}}


def snapshot(date, exited, members=()):
    return {"meta": {"quote_date": date}, "members": [{"code": c} for c in members],
            "changes": {"vs_prev_close": {"exited": [{"code": c, "name": "x", "prev_rank": 100, "now_rank": 104} for c in exited]}}}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / "live"
        self.cache = Path(self.tmp.name) / "cache"
        self.cache.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        return json.loads((self.live / "history" / name).read_text(encoding="utf-8"))

    def test_archive_freezes_last_snapshot_quote_and_profile(self):
        base = {"meta": {"quote_date": "2026-10-26"}, "members": [{"code": "300033.SZ", "price": 280.0, "total_mv": 1.5e11, "quote_at": "20261026150000"}]}
        (self.cache / "688999.SH.json").write_text(json.dumps({"identity": {"name_abbr": "新股"}, "_meta": {}}), encoding="utf-8")
        written = arch.archive(snapshot("2026-10-27", ["300033.SZ", "688999.SH", "600000.SH"]), base, "2026-10-26 收盘",
                               self.live, self.cache, STATIC)
        self.assertEqual(written, ["300033.SZ", "688999.SH", "600000.SH"])
        doc = self.read("300033.SZ.json")
        self.assertEqual((doc["profile_status"], doc["market"]["price"], doc["market"]["as_of_date"]), ("verified_snapshot", 280.0, "2026-10-26"))
        self.assertNotIn("market", doc["profile"])
        self.assertEqual(self.read("688999.SH.json")["profile_status"], "auto_profile")
        self.assertNotIn("_meta", self.read("688999.SH.json")["profile"])
        self.assertEqual(self.read("600000.SH.json")["profile_status"], "identity_only")

    def test_rerun_keeps_first_exit_and_reentry_leaves_index(self):
        arch.archive(snapshot("2026-10-27", ["300033.SZ"]), None, "b", self.live, self.cache, STATIC)
        arch.archive(snapshot("2026-10-28", ["300033.SZ"]), None, "b", self.live, self.cache, STATIC)
        self.assertEqual(self.read("300033.SZ.json")["exited_on"], "2026-10-27")
        self.assertEqual(self.read("300033.SZ.json")["market"]["price"], 300.0)  # 无快照时用静态行情
        arch.archive(snapshot("2026-10-29", [], ["300033.SZ"]), None, "b", self.live, self.cache, STATIC)
        self.assertNotIn("300033.SZ", self.read("index.json"))
        self.assertTrue((self.live / "history" / "300033.SZ.json").exists())

    def test_now_rank_refreshed_each_run(self):
        arch.archive(snapshot("2026-10-27", ["300033.SZ"]), None, "b", self.live, self.cache, STATIC)
        arch.archive(snapshot("2026-10-28", []), None, "b", self.live, self.cache, STATIC, now_ranks={"300033.SZ": (107, 1.4e11)})
        doc = self.read("300033.SZ.json")
        self.assertEqual((doc["now_rank"], doc["now_at"], doc["exited_on"]), (107, "2026-10-28", "2026-10-27"))

    def test_old_archives_are_trimmed(self):
        arch.archive(snapshot("2026-01-02", ["300033.SZ"]), None, "b", self.live, self.cache, STATIC)
        arch.archive(snapshot("2026-10-27", []), None, "b", self.live, self.cache, STATIC)
        doc = self.read("300033.SZ.json")
        self.assertTrue(doc["trimmed"])
        self.assertEqual(list(doc["profile"]), ["identity"])


if __name__ == "__main__":
    unittest.main()
