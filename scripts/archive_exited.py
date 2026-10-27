"""出榜公司历史档：退出百强的公司不删除，档案冻结在退出时点，页面仍可打开。

写入 web/live/history/<代码>.json（随站点部署）与 history/index.json。
档案来源：已核实档案 profiles/verified/ → 自动补档缓存 profiles/companies/ → 仅身份信息。
行情取退出前最后一版快照中的该股报价；没有快照时用静态档案的行情。
出榜超过 180 天且未再进榜的，只保留身份与最后行情。
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "ashare100" / "ashare100" / "profiles" / "companies"
LIVE_DIR = ROOT / "ashare100" / "ashare100" / "web" / "live"
KEEP_DAYS = 180
MARKET_FIELDS = ("price", "change_pct", "amount", "turnover_rate", "pe_ttm", "pb", "total_mv", "float_mv", "quote_at")


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)


def last_market(code, base_doc, static_companies):
    """退出前最后一版快照里的报价；找不到则用静态档案行情。"""
    for row in (base_doc or {}).get("members", []):
        if row["code"] == code:
            return {k: row.get(k) for k in MARKET_FIELDS} | {"as_of_date": base_doc["meta"]["quote_date"]}
    old = static_companies.get(code, {}).get("market")
    return dict(old) if old else None


def profile_of(code, static_companies, cache_dir):
    if code in static_companies:
        return {k: v for k, v in static_companies[code].items() if k != "market"}, "verified_snapshot"
    path = cache_dir / f"{code}.json"
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in doc.items() if k != "_meta"}, "auto_profile"
    return None, "identity_only"


def archive(snapshot, base_doc, base_label, live_dir=LIVE_DIR, cache_dir=CACHE_DIR, static_companies=None, now_ranks=None):
    """now_ranks：{代码: (全市场名次, 总市值)}，用于每次刷新已出榜公司的「当前第 x 名」。"""
    """为 snapshot 相对基准退出的公司写历史档；重新进榜的公司从索引中移除。返回写入的代码。"""
    if static_companies is None:
        import profiles_store
        static_companies = profiles_store.load_verified()
    hist_dir = live_dir / "history"
    index_path = hist_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    quote_date = snapshot["meta"]["quote_date"]
    exited = snapshot["changes"]["vs_prev_close"]["exited"]
    written = []
    for row in exited:
        code = row["code"]
        if code in index and index[code]["exited_on"] < quote_date:
            continue  # 已归档：保留首次退出的时点
        if code in index:  # 同一天重跑（基准更新为上一交易日收盘等）：只更新退出前名次与基准，档案与行情不变
            path = hist_dir / f"{code}.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc.update({"last_rank": row.get("prev_rank"), "exited_vs": base_label})
            write_json(path, doc)
            index[code]["last_rank"] = row.get("prev_rank")
            continue
        profile, status = profile_of(code, static_companies, cache_dir)
        doc = {
            "code": code, "name": row.get("name"), "exited_on": quote_date, "exited_vs": base_label,
            "last_rank": row.get("prev_rank"), "now_rank": row.get("now_rank"), "now_total_mv": row.get("total_mv"),
            "profile_status": status, "market": last_market(code, base_doc, static_companies), "profile": profile,
        }
        write_json(hist_dir / f"{code}.json", doc)
        index[code] = {"name": doc["name"], "exited_on": quote_date, "last_rank": doc["last_rank"],
                       "profile_status": status}
        written.append(code)
    # 已出榜公司：刷新当前全市场名次（档案与退出时行情仍冻结）
    for code in index:
        path = hist_dir / f"{code}.json"
        if now_ranks and code in now_ranks and path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["now_rank"], doc["now_total_mv"] = now_ranks[code]
            doc["now_at"] = quote_date
            write_json(path, doc)
    # 重新进榜：从历史索引移除（文件保留，供旧链接打开）
    for code in [r["code"] for r in snapshot["members"]]:
        index.pop(code, None)
    # 超期精简
    cutoff = (date.fromisoformat(quote_date) - timedelta(days=KEEP_DAYS)).isoformat()
    for code, item in index.items():
        path = hist_dir / f"{code}.json"
        if item["exited_on"] < cutoff and path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("profile"):
                doc["profile"] = {"identity": doc["profile"].get("identity")}
                doc["trimmed"] = True
                write_json(path, doc)
    write_json(index_path, index)
    return written
