"""机构持股（财务投资者）：全站统一用东方财富数据中心的同一报告期、同一口径，写 web/live/inst_hold.json。

口径：基金、QFII、社保、券商、保险、信托六类机构的持股占总股本比例之和，剔除单家持股 ≥5% 的机构
（控股或战略股东，如中国平安之于平安银行；持股 5% 以上须单独公告，不算财务投资者）。
报告期只用中报（6-30）与年报（12-31）：这两期基金披露全部持仓，季报只有前十大，偏低。
中报在 9 月 1 日后、年报在次年 4 月 1 日后视为披露完整。

2026-10-30 回测：原已核实档案的数字口径不明，与东财任一报告期、任一机构组合都只有约 10–16% 一致，
因此不再沿用旧数，页面上所有公司改用本文件（页面注明报告期与口径）。

已有同一报告期结果的公司不重抓；换报告期时全部重算。

用法：python scripts/build_inst_hold.py [--output-dir web/live]
"""

import argparse
import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_store as store  # noqa: E402


LIVE_DIR = store.ROOT / "ashare100" / "ashare100" / "web" / "live"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get?"
CN_TZ = timezone(timedelta(hours=8))
FIN_TYPES = ("01", "02", "03", "04", "05", "06")  # 基金、QFII、社保、券商、保险、信托（07 其他含一般法人，不计）
TYPE_NAME = dict(zip(FIN_TYPES, ("基金", "QFII", "社保", "券商", "保险", "信托")))
STRATEGIC = 5.0
DEFINITION = "基金、QFII、社保、券商、保险、信托合计，剔除单家持股 ≥5% 的机构"


def report_date(today=None):
    """最近一期披露完整的中报或年报日期。"""
    today = today or date.today()
    if today >= date(today.year, 9, 1):
        return f"{today.year}-06-30"
    if today >= date(today.year, 4, 1):
        return f"{today.year - 1}-12-31"
    return f"{today.year - 1}-06-30"


def query(report, code, period, extra=""):
    from enrich_company import get_json
    doc = get_json(DATACENTER + urllib.parse.urlencode({
        "reportName": report, "columns": "ALL", "pageSize": 50,
        "filter": f'(SECURITY_CODE="{code.split(".")[0]}")(REPORT_DATE=\'{period}\'){extra}'}), timeout=20)
    return ((doc.get("result") or {}).get("data")) or []


def fetch(code, period):
    """返回 (按类型合计行, 单家 ≥5% 的明细行)。"""
    types = '(ORG_TYPE in ("' + '","'.join(FIN_TYPES) + '"))'
    return (query("RPT_MAIN_ORGHOLD", code, period),
            query("RPT_MAIN_ORGHOLDDETAIL", code, period, f"(TOTALSHARES_RATIO>={STRATEGIC}){types}"))


def ratio(totals, big):
    """无任何机构持仓记录时返回 None（不记 0）。"""
    if not totals:
        return None, []
    total = sum(r.get("TOTALSHARES_RATIO") or 0 for r in totals if r.get("ORG_TYPE") in FIN_TYPES)
    excluded = [{"name": r.get("HOLDER_NAME"), "type": TYPE_NAME.get(r.get("ORG_TYPE")),
                 "ratio": round(r.get("TOTALSHARES_RATIO") or 0, 2)} for r in big]
    return round(max(total - sum(r.get("TOTALSHARES_RATIO") or 0 for r in big), 0), 2), excluded


def build(codes, previous, today=None, fetch=fetch, workers=6):
    period = report_date(today)
    same = (previous.get("meta") or {}).get("report_date") == period
    old = previous.get("companies", {}) if same else {}
    out, problems = {code: old[code] for code in codes if code in old}, []

    def one(code):
        try:
            return code, ratio(*fetch(code, period)), None
        except Exception as exc:  # 单家失败只记录，下次运行重试
            return code, (None, []), f"{code} 机构持股：{type(exc).__name__} {exc}"[:200]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, (value, excluded), problem in pool.map(one, [c for c in codes if c not in out]):
            if problem:
                problems.append(problem)
            elif value is not None:
                out[code] = {"ratio": value, "excluded": excluded}
    meta = {"report_date": period, "definition": DEFINITION, "source": "东方财富数据中心 · 主力持仓",
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"), "count": len(out)}
    return {"meta": meta, "companies": dict(sorted(out.items()))}, problems


def run(output_dir=LIVE_DIR):
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((output_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    codes = [r["code"] for r in snapshot["members"]]
    path = output_dir / "inst_hold.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc, problems = build(codes, previous)
    store.write_json(path, doc)
    return doc, problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    doc, problems = run(parser.parse_args().output_dir)
    for p in problems:
        print("问题：" + p, file=sys.stderr)
    print(f"机构持股：{doc['meta']['report_date']} 中报/年报口径，{doc['meta']['count']} 家，问题 {len(problems)} 项")
