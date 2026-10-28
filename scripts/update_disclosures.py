"""每日两更：当前百强的公告与投资者问答，写 web/live/disclosures/latest.json，页面覆盖档案中的同名字段。

公告与问答是会变的数据，不写进已核实档案（profiles/verified/）。
- 公告：巨潮近 12 个月，最新 20 条与 12 个月总数，每次整体重取。
- 问答：沪市上证 e 互动（已回复 + 待回复），与上一版按问题合并：新问题加入、原问题出现回复时更新，取最新 8 条。
  深市/北交所取互动易（本机网络不可达，只在 GitHub Actions 上抓），合并规则相同；失败时沿用上一版。
单家失败沿用上一版该公司的数据；覆盖率低于 80% 时不切换 latest.json。
巨潮 orgId 与 e 互动 uid 缓存在 profiles/disclosure_ids.json。

用法：python scripts/update_disclosures.py [--output-dir web/live] [--only 600016.SH ...]
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_extras as ex  # noqa: E402
import profiles_store as store  # noqa: E402


LIVE_DIR = store.ROOT / "ashare100" / "ashare100" / "web" / "live"
ID_CACHE = store.ROOT / "ashare100" / "ashare100" / "profiles" / "disclosure_ids.json"
CN_TZ = timezone(timedelta(hours=8))
MIN_COVERAGE = 0.8


def merge_qa(old_items, new_items, limit=ex.QA_N):
    """按（提问日, 问题）合并：新抓到的覆盖旧的（回复可能新出现），旧的里没再出现的保留。"""
    key = lambda q: (q.get("ask_date"), q.get("question"))
    merged = {key(q): q for q in old_items or []}
    for q in new_items:
        old = merged.get(key(q))
        if old and old.get("answer") and not q.get("answer"):
            continue  # 新一版没带回复（只抓了待回复列表）时不抹掉旧回复
        merged[key(q)] = q
    return sorted(merged.values(), key=lambda q: (q.get("ask_date") or "", q.get("answer_date") or ""), reverse=True)[:limit]


def one(code, previous, ids, now):
    """返回 (条目, 问题列表)。条目缺项时沿用 previous。"""
    entry, problems = dict(previous or {}), []
    num = code.split(".")[0]
    try:
        if code not in ids.get("cninfo", {}):
            ids.setdefault("cninfo", {})[code] = ex.cninfo_org_id(code)
        entry["announcements"] = ex.fetch_announcements(code, now.date(), ids["cninfo"][code])
        entry["announcements_at"] = now.isoformat(timespec="seconds")
    except Exception as exc:
        problems.append(f"{code} 公告：{type(exc).__name__} {exc}"[:200])
    try:
        if code.endswith(".SH") and num not in ids.get("sse_uid", {}):
            ids.setdefault("sse_uid", {})[num] = ex.sse_uid(code)
        fresh = ex.fetch_qa(code, now, ids.get("sse_uid", {}).get(num), ids.get("cninfo", {}).get(code))
        entry["qa"] = {"items": merge_qa((entry.get("qa") or {}).get("items"), fresh["items"]), "count_12m": None}
        entry["qa_at"] = now.isoformat(timespec="seconds")
        entry.pop("qa_unsupported", None)
    except ex.Unsupported:
        entry["qa_unsupported"] = True  # 已知缺口，不告警；页面沿用档案原有问答
    except Exception as exc:
        problems.append(f"{code} 问答：{type(exc).__name__} {exc}"[:200])
    return entry, problems


def build(codes, previous, ids, now=None, fetch_one=one, workers=6):
    now = now or datetime.now(CN_TZ)
    entries, problems = {}, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, (entry, found) in zip(codes, pool.map(lambda c: fetch_one(c, previous.get(c), ids, now), codes)):
            problems += found
            if entry.get("announcements") or entry.get("qa"):
                entries[code] = entry
    fresh = sum(1 for c in codes if entries.get(c, {}).get("announcements_at", "").startswith(now.date().isoformat()))
    unsupported = sum(1 for e in entries.values() if e.get("qa_unsupported"))
    return {"meta": {"generated_at": now.isoformat(timespec="seconds"), "member_count": len(codes),
                     "announcements_fresh": fresh, "problems": len(problems), "qa_unsupported": unsupported},
            "companies": entries}, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((args.output_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    codes = [r["code"] for r in snapshot["members"]]
    if args.only:
        codes = [c for c in codes if c in set(args.only)]
    path = args.output_dir / "disclosures" / "latest.json"
    previous = json.loads(path.read_text(encoding="utf-8"))["companies"] if path.exists() else {}
    ids = json.loads(ID_CACHE.read_text(encoding="utf-8")) if ID_CACHE.exists() else {}
    doc, problems = build(codes, previous, ids)
    store.write_json(ID_CACHE, ids)
    if args.only:  # 只更新部分公司：其余沿用上一版
        doc["companies"] = {**previous, **doc["companies"]}
    for p in problems:
        print("问题：" + p, file=sys.stderr)
    meta = doc["meta"]
    print(f"公告更新 {meta['announcements_fresh']}/{meta['member_count']} 家，问题 {meta['problems']} 项")
    if not args.only and meta["announcements_fresh"] < meta["member_count"] * MIN_COVERAGE:
        print(f"公告覆盖不足 {MIN_COVERAGE:.0%}：不切换 latest.json", file=sys.stderr)
        sys.exit(1)
    doc["meta"]["snapshot"] = manifest["snapshot"]
    store.write_json(path, doc)
    if args.only:
        return  # 部分补跑不代表整体状态，不写 status.json
    import task_status
    task_status.record("disclosures", snapshot["meta"]["quote_date"], problems,
                       detail=f"公告 {meta['announcements_fresh']}/{meta['member_count']} 家"
                              + (f"；深市问答暂不支持 {meta['qa_unsupported']} 家" if meta["qa_unsupported"] else ""),
                       live_dir=args.output_dir)


if __name__ == "__main__":
    main()
