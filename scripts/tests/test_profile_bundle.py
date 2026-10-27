"""档案增量包：未改动的已核实档案不进包；改动与新进公司自动补档进包；无档案的列出。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_profile_bundle as bundle
import profiles_store as store


class BundleTests(unittest.TestCase):
    def test_only_changes_and_auto_profiles_are_bundled(self):
        base = {"600000.SH": {"identity": {"name_abbr": "浦发"}}, "600036.SH": {"identity": {"name_abbr": "招行"}}}
        verified = {"600000.SH": base["600000.SH"], "600036.SH": {"identity": {"name_abbr": "招商银行"}}}
        snap = {"members": [{"code": c} for c in ("600000.SH", "600036.SH", "600016.SH", "601995.SH")]}
        auto = {"600016.SH": {"identity": {"name_abbr": "民生"}}}
        doc = bundle.build("snapshots/a.json", snap, base, verified, auto.get)
        self.assertEqual({c: p["status"] for c, p in doc["profiles"].items()},
                         {"600036.SH": "verified_snapshot", "600016.SH": "auto_profile"})
        self.assertEqual(doc["meta"]["missing"], ["601995.SH"])
        self.assertEqual(doc["meta"]["snapshot"], "snapshots/a.json")

    def test_split_does_not_overwrite_manual_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            first = store.split(verified_dir=target)
            self.assertEqual(len(first), len(store.load_baseline()))
            edited = target / f"{first[0]}.json"
            edited.write_text('{"identity": {"name_abbr": "人工修订"}}', encoding="utf-8")
            self.assertEqual(store.split(verified_dir=target), [])
            self.assertEqual(store.load_verified(target)[first[0]]["identity"]["name_abbr"], "人工修订")


if __name__ == "__main__":
    unittest.main()
