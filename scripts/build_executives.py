"""核心高管（董事长、总经理、董事会秘书）的最新姓名与年度税前报酬，写 web/live/executives.json。

来源：东方财富 F10「公司高管」（CompanyManagement/PageAjax），一次返回全体董监高的姓名、职务、任职起始日与薪酬。
更新节奏按来源的变化周期：
- 姓名、职务随临时公告随时可能变：每家公司超过 20 小时未抓就重抓，即约每天一次（随榜单任务运行）；
- 报酬来自年报，一年一变：与姓名同一次请求取得，不单独抓。报酬所属年度按年报披露时间推定
  （5 月 1 日起为上一年，之前为前年），页面表头据此显示。
报酬为空时区分：任职起始日在报酬年度内或之后 → 新任（年报未披露其全年报酬）；否则 → 未在公司领薪（年报中常见于在股东单位领薪）。

用法：python scripts/build_executives.py [--output-dir web/live] [--force]
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_store as store  # noqa: E402


LIVE_DIR = store.ROOT / "ashare100" / "ashare100" / "web" / "live"
CN_TZ = timezone(timedelta(hours=8))
REFRESH = timedelta(hours=20)
ROLES = (("董事长", re.compile(r"(?<!副)董事长")), ("总经理", re.compile(r"(?<!副)(总经理|总裁|行长)")),
         ("董事会秘书", re.compile(r"董事会秘书|董秘")))


def pay_year(today=None):
    today = today or date.today()
    return today.year - 1 if today >= date(today.year, 5, 1) else today.year - 2


def since(text):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text or "")
    return m[1] if m else None


def pick(rows, year):
    """全体董监高 → [{name, title, pay, pay_note, since}]，三个角色各取一人（同一人可兼任）。"""
    out = []
    for title, pattern in ROLES:
        hit = next((r for r in rows if pattern.search(r.get("POSITION") or "")), None)
        if not hit:
            continue
        acting = bool(re.search(r"代理|代行|\(代\)|（代）", hit.get("POSITION") or ""))
        start = since(hit.get("INCUMBENT_TIME"))
        try:
            pay = float(hit["SALARY"]) if hit.get("SALARY") not in (None, "") else None
        except ValueError:
            pay = None
        note = None
        if pay is None:
            note = "新任" if start and start >= f"{year}-01-01" else "未在公司领薪"
        out.append({"name": (hit.get("PERSON_NAME") or "").strip() + ("(代)" if acting else ""), "title": title,
                    "pay": pay, "pay_note": note, "since": start})
    return out


def fetch(code):
    from enrich_company import f10
    rows = f10("CompanyManagement/PageAjax", code).get("gglb") or []
    if any(r.get("SECUCODE") not in (None, code) for r in rows):
        raise ValueError("公司高管返回代码不符")
    return rows


def build(codes, previous, now=None, fetch=fetch, force=False, workers=6):
    now = now or datetime.now(CN_TZ)
    year = pay_year(now.date())
    old = previous.get("companies", {}) if (previous.get("meta") or {}).get("pay_year") == year else {}
    keep = {c: old[c] for c in codes if c in old and not force
            and now - datetime.fromisoformat(old[c]["fetched_at"]) < REFRESH}
    problems, out = [], dict(keep)

    def one(code):
        try:
            return code, pick(fetch(code), year), None
        except Exception as exc:  # 单家失败沿用上一版，下次重试
            return code, None, f"{code} 高管：{type(exc).__name__} {exc}"[:200]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, execs, problem in pool.map(one, [c for c in codes if c not in keep]):
            if problem:
                problems.append(problem)
                if code in (previous.get("companies") or {}):
                    out[code] = previous["companies"][code]
            elif execs:
                out[code] = {"executives": execs, "fetched_at": now.isoformat(timespec="seconds")}
    return {"meta": {"pay_year": year, "source": "东方财富 F10 · 公司高管", "updated": now.isoformat(timespec="seconds"),
                     "count": len(out)}, "companies": dict(sorted(out.items()))}, problems


def run(output_dir=LIVE_DIR, force=False):
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((output_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    path = output_dir / "executives.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc, problems = build([r["code"] for r in snapshot["members"]], previous, force=force)
    store.write_json(path, doc)
    return doc, problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    parser.add_argument("--force", action="store_true", help="忽略 20 小时缓存，全部重抓")
    args = parser.parse_args()
    doc, problems = run(args.output_dir, args.force)
    for p in problems:
        print("问题：" + p, file=sys.stderr)
    print(f"高管：{doc['meta']['count']} 家，报酬年度 {doc['meta']['pay_year']}，问题 {len(problems)} 项")
