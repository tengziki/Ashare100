"""每日收盘后重算价格类指标：年初至今、近 1 年、近 3 年、52 周高低、股息率。

对象：当前 manifest 快照的百强（临界区快照只有市值、无价格，不在此计算）。
口径与已核实档案一致（2026-10-22 对照茅台/招行/宁德/平安一致）：
- 区间涨跌：腾讯前复权日 K 收盘价，基准取基准日当天或之前最近一个交易日；
- 52 周高低：过去一年（不含一年前当天）日内最高/最低价，并入今日价；
- 股息率：最近一个已实施年度分红的财年，每股派息合计（含中期）÷ 最新价。
上市不足、无基准价的指标为 null。覆盖率不足 90% 时不切换 metrics/latest.json。

用法：python scripts/build_daily_metrics.py [--output-dir web/live]
"""

import argparse
import json
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich_company import DATACENTER, get_json  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "ashare100" / "ashare100" / "web" / "live"
# 同一腾讯前复权日 K 的两个入口（数值一致）；前者被限流（HTTP 501）时换后者
KLINE_URLS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{start},{end},640,qfq",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,{start},{end},640,qfq",
)
CN_TZ = timezone(timedelta(hours=8))
MIN_COVERAGE = 0.9


def years_ago(d, n):
    try:
        return d.replace(year=d.year - n)
    except ValueError:  # 2 月 29 日
        return d.replace(year=d.year - n, day=28)


_skip = set()  # 本轮已失败的入口：后续请求直接跳过，避免每次先等它超时


def kline_json(sym, start, end):
    last = None
    for url in KLINE_URLS:
        if url in _skip and len(_skip) < len(KLINE_URLS):
            continue
        try:
            return get_json(url.format(sym=sym, start=start, end=end))
        except OSError as exc:  # HTTPError / URLError 都是 OSError
            _skip.add(url)
            last = exc
    raise last


def fetch_kline(code, start, end):
    """前复权日 K：[(日期, 开, 收, 高, 低)]。单次最多 640 行，按约 2.5 年（≈610 个交易日）分段取。"""
    num, exchange = code.split(".")
    sym = exchange.lower() + num
    rows = {}
    seg_start = start
    while seg_start <= end:
        seg_end = min(end, seg_start + timedelta(days=900))
        doc = kline_json(sym, seg_start, seg_end)
        data = (doc.get("data") or {}).get(sym) or {}
        for r in data.get("qfqday") or data.get("day") or []:
            rows[r[0]] = (r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        seg_start = seg_end + timedelta(days=1)
    return [rows[k] for k in sorted(rows)]


def fetch_bonus(code):
    doc = get_json(DATACENTER + urllib.parse.urlencode({
        "reportName": "RPT_SHAREBONUS_DET", "columns": "REPORT_DATE,ASSIGN_PROGRESS,PRETAX_BONUS_RMB",
        "pageSize": 30, "sortColumns": "REPORT_DATE", "sortTypes": -1,
        "filter": f'(SECURITY_CODE="{code.split(".")[0]}")'}))
    return ((doc.get("result") or {}).get("data")) or []


def close_on_or_before(bars, day):
    """bars 按日期升序；返回 day 当天或之前最近交易日的收盘价；上市晚于 day 则 None。"""
    best = None
    for b in bars:
        if b[0] > day:
            break
        best = b[2]
    return best


def pct(now, base):
    return round((now / base - 1) * 100, 2) if base else None


def compute(bars, bonus, price, asof):
    """由日 K 与分红明细算出指标；price 为快照最新价（日 K 可能尚未含今日）。"""
    d = date.fromisoformat(asof)
    ytd_base = close_on_or_before(bars, f"{d.year - 1}-12-31")
    one_y = years_ago(d, 1).isoformat()
    window = [b for b in bars if one_y < b[0] <= asof]
    high = max([b[3] for b in window] + [price]) if window else None
    low = min([b[4] for b in window] + [price]) if window else None
    # 上市不足一年：与已核实档案一致，给上市以来的高低
    paid = {}
    for r in bonus:
        if r.get("ASSIGN_PROGRESS") == "实施分配" and r.get("PRETAX_BONUS_RMB"):
            year = int(r["REPORT_DATE"][:4])
            paid.setdefault(year, {"dps": 0.0, "annual": False})
            paid[year]["dps"] += float(r["PRETAX_BONUS_RMB"]) / 10  # 接口为每 10 股派息
            paid[year]["annual"] |= r["REPORT_DATE"][5:10] == "12-31"
    # 财年口径：上一财年（年报分红多在 7 月前实施）。9 月起上一财年仍无实施分配 = 未分红（0）；
    # 9 月前尚未实施则沿用最近一个有年度分红的财年。上市晚于上一财年末的新股为 null。
    fy = d.year - 1
    listed = bool(bars) and bars[0][0] <= f"{fy}-12-31"
    if fy not in paid and d.month < 9:
        fy = max((y for y, v in paid.items() if v["annual"]), default=None)
    dps = round(paid[fy]["dps"], 4) if fy in paid else (0.0 if listed and d.month >= 9 else None)
    return {
        "price": price,
        "ytd_return": pct(price, ytd_base),
        "return_1y": pct(price, close_on_or_before(bars, one_y)) if bars and bars[0][0] <= one_y else None,
        "return_3y": (pct(price, close_on_or_before(bars, years_ago(d, 3).isoformat()))
                      if bars and bars[0][0] <= years_ago(d, 3).isoformat() else None),
        "high_52w": round(high, 3) if high else None, "low_52w": round(low, 3) if low else None,
        "dividend_yield": round(dps / price * 100, 2) if dps is not None and price else None,
        "dps_fiscal_year": fy, "dps_pretax": dps,
    }


def one(code, price, asof):
    d = date.fromisoformat(asof)
    start = years_ago(d, 3) - timedelta(days=10)
    return compute(fetch_kline(code, start, d), fetch_bonus(code), price, asof)


def build(snapshot, fetch_one=one, workers=5):
    asof = snapshot["meta"]["quote_date"]
    prices = {r["code"]: r["price"] for r in snapshot["members"]}
    metrics, errors = {}, {}

    def job(code):
        try:
            return code, fetch_one(code, prices[code], asof), None
        except Exception as exc:  # 单只失败不影响其余
            return code, None, f"{type(exc).__name__} {exc}"[:160]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, value, error in pool.map(job, list(prices)):
            if value is not None:
                metrics[code] = value
            else:
                errors[code] = error
    return {
        "meta": {"quote_date": asof, "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
                 "snapshot_generated_at": snapshot["meta"]["generated_at"],
                 "sources": {"kline": "腾讯前复权日 K", "dividends": "东方财富数据中心分红明细"},
                 "member_count": len(prices), "covered": len(metrics)},
        "metrics": metrics, "errors": errors,
    }


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    args = parser.parse_args()
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((args.output_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    doc = build(snapshot)
    name = f"metrics/{doc['meta']['quote_date'].replace('-', '')}.json"
    write_json(args.output_dir / name, doc)
    coverage = doc["meta"]["covered"] / max(doc["meta"]["member_count"], 1)
    print(f"已写 {name}：{doc['meta']['covered']}/{doc['meta']['member_count']} 家，失败 {len(doc['errors'])}")
    if coverage < MIN_COVERAGE:
        print(f"覆盖率 {coverage:.0%} 低于 {MIN_COVERAGE:.0%}：不切换 metrics/latest.json", file=sys.stderr)
        sys.exit(1)
    write_json(args.output_dir / "metrics" / "latest.json",
               {"file": name, "quote_date": doc["meta"]["quote_date"], "snapshot": manifest["snapshot"]})
    import task_status
    task_status.record("daily_metrics", doc["meta"]["quote_date"],
                       [f"{c}：{e}" for c, e in doc["errors"].items()],
                       detail=f"{doc['meta']['covered']}/{doc['meta']['member_count']} 家", live_dir=args.output_dir)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"日更指标失败，保留旧版：{exc}", file=sys.stderr)
        sys.exit(1)
