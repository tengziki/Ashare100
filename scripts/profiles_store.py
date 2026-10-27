"""公司档案库：一家公司一个文件。

profiles/verified/<代码>.json   已核实档案（由 data.json 拆出，之后人工修订改这里）
profiles/companies/<代码>.json  自动补档缓存（enrich_company.py 生成）
data.json / web/data.js 是 2026-10-22 交付时的基线与离线备份，不再手工修改；
页面联网时读 web/live/profiles/bundle.json 中相对基线有变化的档案。

用法：
  python scripts/profiles_store.py --split [--force]          # 从 data.json 拆出已核实档案
  python scripts/profiles_store.py --promote 600016.SH --by 审核人 [--note 说明]
                                                              # 自动补档复核通过 → 已核实档案
"""

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = ROOT / "ashare100" / "ashare100" / "data.json"
VERIFIED_DIR = ROOT / "ashare100" / "ashare100" / "profiles" / "verified"
AUTO_DIR = ROOT / "ashare100" / "ashare100" / "profiles" / "companies"


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_baseline():
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["companies"]


def load_verified(verified_dir=VERIFIED_DIR):
    """已核实档案 {代码: 公司}；目录尚未建立时退回基线 data.json。"""
    files = sorted(verified_dir.glob("*.json")) if verified_dir.exists() else []
    if not files:
        return load_baseline()
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def load_auto(code, auto_dir=AUTO_DIR):
    path = auto_dir / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def split(force=False, verified_dir=VERIFIED_DIR):
    """拆分基线；已存在的文件默认不覆盖，保护人工修订。返回写入的代码。"""
    written = []
    for code, company in load_baseline().items():
        path = verified_dir / f"{code}.json"
        if path.exists() and not force:
            continue
        write_json(path, company)
        written.append(code)
    return written


def promote(code, reviewer, note=None, auto_dir=AUTO_DIR, verified_dir=VERIFIED_DIR):
    """把复核过的自动补档转为已核实档案；去掉抓取元数据，记录复核人与时间。"""
    from datetime import datetime, timedelta, timezone
    auto = load_auto(code, auto_dir)
    if not auto:
        raise ValueError(f"{code} 没有自动补档")
    if auto.get("_meta", {}).get("errors"):
        raise ValueError(f"{code} 有抓取失败的字段组：{'、'.join(auto['_meta']['errors'])}，先重新补档")
    target = verified_dir / f"{code}.json"
    if target.exists():
        raise ValueError(f"{code} 已有已核实档案，不覆盖")
    profile = {k: v for k, v in auto.items() if k != "_meta"}
    profile["review"] = {"reviewer": reviewer, "note": note, "source": auto.get("_meta", {}).get("source"),
                         "fetched_at": auto.get("_meta", {}).get("fetched_at"),
                         "reviewed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")}
    write_json(target, profile)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--promote")
    parser.add_argument("--by")
    parser.add_argument("--note")
    args = parser.parse_args()
    if args.promote:
        if not args.by:
            parser.error("--promote 需要 --by 审核人")
        print(f"已转为已核实档案：{promote(args.promote, args.by, args.note).relative_to(ROOT)}")
    if args.split:
        print(f"已拆分 {len(split(args.force))} 家到 {VERIFIED_DIR.relative_to(ROOT)}")
