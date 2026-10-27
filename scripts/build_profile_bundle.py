"""为当前百强合成 web/live/profiles/bundle.json：页面基线 data.js 之外的档案增量。

只收录两类：已核实档案中相对基线有改动的公司、没有已核实档案的新进公司的自动补档。
其余公司页面直接用 data.js，避免重复下载约 1.6 MB 的基线。
bundle 绑定生成时的快照路径；页面只在与当前 manifest 一致时采用。

用法：python scripts/build_profile_bundle.py [--output-dir web/live]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_store as store  # noqa: E402


LIVE_DIR = store.ROOT / "ashare100" / "ashare100" / "web" / "live"
CN_TZ = timezone(timedelta(hours=8))


def build(snapshot_path, snapshot, baseline, verified, load_auto=store.load_auto):
    profiles, missing = {}, []
    for row in snapshot["members"]:
        code = row["code"]
        if code in verified:
            if verified[code] != baseline.get(code):
                profiles[code] = {"status": "verified_snapshot", "profile": verified[code]}
            continue
        auto = load_auto(code)
        if auto:
            profiles[code] = {"status": "auto_profile", "profile": auto}
        else:
            missing.append(code)
    return {
        "meta": {"snapshot": snapshot_path, "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                 "baseline": "data.js", "verified_changed": sum(p["status"] == "verified_snapshot" for p in profiles.values()),
                 "auto": sum(p["status"] == "auto_profile" for p in profiles.values()), "missing": missing},
        "profiles": profiles,
    }


def run(output_dir=LIVE_DIR):
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((output_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    doc = build(manifest["snapshot"], snapshot, store.load_baseline(), store.load_verified())
    store.write_json(output_dir / "profiles" / "bundle.json", doc)
    return doc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    meta = run(parser.parse_args().output_dir)["meta"]
    print(f"bundle：已核实有改动 {meta['verified_changed']} 家，自动补档 {meta['auto']} 家，无档案 {len(meta['missing'])} 家")
