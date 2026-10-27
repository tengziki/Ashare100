"""为待补档公司自动生成简介、财务、股东、分红档案（结构同 data.json 中一家公司）。

来源：东方财富 F10 公开页面接口（公司概况、主要指标、股东研究）与数据中心分红明细。
每个字段组独立抓取：失败的组保持空值并写入 _meta.errors，下次运行重试；
无法由来源直接得到的字段一律为 null，不推算、不填 0。
生成结果缓存在 profiles/companies/<代码>.json，7 天内不重复抓取。
"""

import gzip
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "ashare100" / "ashare100" / "profiles" / "companies"
F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get?"
CN_TZ = timezone(timedelta(hours=8))
YEARS = list(range(2021, 2026))
CACHE_DAYS = 7
GZIP_MAGIC = bytes((0x1F, 0x8B))
NORTHBOUND_HOLDER = "香港中央结算有限公司"  # 沪深股通名义持有人（与「代理人」H 股账户不同）


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
    return json.loads(gzip.decompress(body) if body.startswith(GZIP_MAGIC) else body)


def f10(path, code):
    num, exchange = code.split(".")
    return get_json(f"{F10}{path}?{'type=1&' if 'ZYZB' in path else ''}code={exchange}{num}")


def fetch_sources(code):
    """返回 {组名: 原始数据}；失败的组记入 errors。"""
    num = code.split(".")[0]
    jobs = {
        "survey": lambda: f10("CompanySurvey/PageAjax", code),
        "annual": lambda: f10("NewFinanceAnalysis/ZYZBAjaxNew", code),
        "latest": lambda: get_json(f"{F10}NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={code.split('.')[1]}{num}"),
        "holders": lambda: f10("ShareholderResearch/PageAjax", code),
        "bonus": lambda: get_json(DATACENTER + urllib.parse.urlencode({
            "reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "pageSize": 30,
            "sortColumns": "REPORT_DATE", "sortTypes": -1, "filter": f'(SECURITY_CODE="{num}")'})),
    }
    raw, errors = {}, {}
    for name, job in jobs.items():
        try:
            raw[name] = job()
        except (OSError, ValueError) as exc:
            errors[name] = str(exc)[:120]
    return raw, errors


def num_or_none(value, digits=2):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits)


def day(value):
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def fin_row(row):
    """主要指标一行 → data.json 的财务字段；接口没有的字段保持 null。"""
    debt = num_or_none(row.get("ZCFZL"))
    liab = num_or_none(row.get("LIABILITY"), 0)
    return {
        "report_date": day(row.get("REPORT_DATE")),
        "revenue": num_or_none(row.get("TOTALOPERATEREVE"), 0),
        "revenue_yoy": num_or_none(row.get("TOTALOPERATEREVETZ")),
        "net_profit": num_or_none(row.get("PARENTNETPROFIT"), 0),
        "net_profit_yoy": num_or_none(row.get("PARENTNETPROFITTZ")),
        "net_profit_deducted": num_or_none(row.get("KCFJCXSYJLR"), 0),
        "gross_margin": num_or_none(row.get("XSMLL")),
        "net_margin": num_or_none(row.get("XSJLL")),
        "eps": num_or_none(row.get("EPSJB"), 3),
        "bvps": num_or_none(row.get("BPS")),
        "roe": num_or_none(row.get("ROEJQ")),
        "roa": num_or_none(row.get("ZZCJLL")),
        "debt_ratio": debt,
        "total_liab": liab,
        "total_assets": round(liab / debt * 100) if liab and debt else None,
        # 该来源的现金流/净利润口径与已核实档案不同（差 3–15%），不采用
        "ocf_to_np": None,
    }


def cagr(a, b, years=3):
    return round(((b / a) ** (1 / years) - 1) * 100, 2) if a and b and a > 0 and b > 0 else None


def build_financials(raw):
    rows = {int(r["REPORT_YEAR"]): r for r in (raw.get("annual") or {}).get("data") or [] if r.get("REPORT_TYPE") == "年报"}
    annual = [{"fiscal_year": y, **(fin_row(rows[y]) if y in rows else {})} for y in YEARS]
    latest_rows = (raw.get("latest") or {}).get("data") or []
    latest = {}
    if latest_rows:
        r = latest_rows[0]
        latest = {"report_period": day(r.get("REPORT_DATE")),
                  "report_type": f"{r.get('REPORT_YEAR', '')}年{r.get('REPORT_TYPE', '')}",
                  **{k: v for k, v in fin_row(r).items() if k in (
                      "revenue", "revenue_yoy", "net_profit", "net_profit_yoy", "net_profit_deducted",
                      "gross_margin", "net_margin", "roe", "eps", "debt_ratio")}}
    first, last = annual[1], annual[4]  # 2022 → 2025
    return {"unit": "CNY", "annual": annual, "latest": latest, "derived": {
        "revenue_cagr_3y": cagr(first.get("revenue"), last.get("revenue")),
        "net_profit_cagr_3y": cagr(first.get("net_profit"), last.get("net_profit")),
        "receivables_to_revenue": None, "goodwill_to_equity": None}}


def build_identity(raw, code, base):
    survey = raw.get("survey") or {}
    j = (survey.get("jbzl") or [{}])[0]
    x = (survey.get("fxxg") or [{}])[0]
    if j and j.get("SECUCODE") != code:
        raise ValueError(f"公司概况代码不符：{j.get('SECUCODE')}")
    num, exchange = code.split(".")
    scope = (j.get("BUSINESS_SCOPE") or "").strip()
    return {
        "code": num, "exchange": exchange, "board": base.get("board"),
        "name_full": j.get("ORG_NAME") or base.get("name_abbr"), "name_abbr": base.get("name_abbr"),
        "aliases": [], "name_en": j.get("ORG_NAME_EN"),
        "former_names": [j["FORMERNAME"]] if j.get("FORMERNAME") else [],
        "industry_sw1": base.get("industry_sw1"), "industry_sw2": base.get("industry_sw2"),
        "list_date": day(x.get("LISTING_DATE")), "found_date": day(x.get("FOUND_DATE")), "province": j.get("PROVINCE"), "city": None,
        "website": j.get("ORG_WEB"), "chairman": j.get("CHAIRMAN"),
        "employees": int(j["EMP_NUM"]) if str(j.get("EMP_NUM") or "").isdigit() else None,
        "business_summary": scope[:80] + ("…" if len(scope) > 80 else "") if scope else None,
        "profile": (j.get("ORG_PROFILE") or "").strip() or None,
        "st_status": None, "audit_opinion": None,
    }


def holder_change(value):
    """东财「较上期」持股变动：数字、'不变'（记 0，与已核实档案一致）或 '新进'（无上期可比，记 null）。"""
    if value == "不变":
        return 0
    return num_or_none(value, 0)


def build_shareholders(raw, survey):
    h = raw.get("holders") or {}
    j = (survey.get("jbzl") or [{}])[0]
    gdrs = (h.get("gdrs") or [{}])[0]
    top10 = [{"rank": r.get("HOLDER_RANK"), "name": r.get("HOLDER_NAME"),
              "hold_num": num_or_none(r.get("HOLD_NUM"), 0), "hold_ratio": num_or_none(r.get("HOLD_NUM_RATIO")),
              "hold_num_change": holder_change(r.get("HOLD_NUM_CHANGE"))} for r in h.get("sdgd") or []]
    north = next((r["hold_ratio"] for r in top10 if r["name"] == NORTHBOUND_HOLDER), None)
    controller = (h.get("sjkzr") or [{}])[0]
    execs = [{"name": j.get(k), "title": t, "pay": None}
             for k, t in (("CHAIRMAN", "董事长"), ("PRESIDENT", "总经理"), ("SECRETARY", "董事会秘书")) if j.get(k)]
    return {
        "controller": controller.get("HOLDER_NAME"), "controlling_shareholder": None, "controlling_ratio": None,
        "top10": top10, "top10_date": day((h.get("sdgd") or [{}])[0].get("END_DATE")),
        "holder_num": num_or_none(gdrs.get("HOLDER_TOTAL_NUM"), 0),
        "holder_num_change_pct": num_or_none(gdrs.get("TOTAL_NUM_RATIO")),
        "holder_num_date": day(gdrs.get("END_DATE")),
        # 机构持股口径（基金/保险/券商/QFII/社保）无法从此来源对齐，留空；北向仅在前十大中出现时可得
        "inst_hold_ratio": None, "northbound_ratio": north,
        "executives": execs, "pledge_ratio_total": None, "unlock_12m": [],
    }


def build_dividends(raw, financials):
    rows = [r for r in ((raw.get("bonus") or {}).get("result") or {}).get("data") or []
            if r.get("ASSIGN_PROGRESS") == "实施分配" and r.get("PRETAX_BONUS_RMB")]
    by_year = {}
    for r in rows:
        year = int(r["REPORT_DATE"][:4])
        item = by_year.setdefault(year, {"dps": 0.0, "amount": 0.0, "ex_date": None})
        dps = float(r["PRETAX_BONUS_RMB"]) / 10  # 接口为每 10 股派息
        item["dps"] += dps
        item["amount"] += dps * float(r.get("TOTAL_SHARES") or 0)
        item["ex_date"] = max(filter(None, [item["ex_date"], day(r.get("EX_DIVIDEND_DATE"))]), default=None)
    profit = {a["fiscal_year"]: a.get("net_profit") for a in financials["annual"]}
    # 来源覆盖范围内某年没有实施分配 = 当年未分红（记 0）；覆盖范围之前未知（记 null）
    first = min(by_year, default=None)
    annual = []
    for y in YEARS:
        d = by_year.get(y)
        if d is None and first is not None and y > first:
            d = {"dps": 0.0, "amount": 0.0, "ex_date": None}
        annual.append({"fiscal_year": y, "dps_pretax": round(d["dps"], 4) if d else None,
                       "total_amount": round(d["amount"], 2) if d else None,
                       "payout_ratio": round(d["amount"] / profit[y] * 100, 2) if d and profit.get(y) else None,
                       "dividend_yield": None, "ex_date": d["ex_date"] if d else None})
    consecutive = 0  # 统计 2021 年起至今（含当年中期分红），与已核实档案口径一致
    for y in range(max(by_year, default=0), YEARS[0] - 1, -1):
        if not by_year.get(y, {}).get("dps"):
            break
        consecutive += 1
    cum_div = sum(a["total_amount"] for a in annual) if all(a["total_amount"] is not None for a in annual) else None
    cum_np = sum(profit[y] or 0 for y in YEARS) if all(profit.get(y) for y in YEARS) else None
    return {"annual": annual, "consecutive_years": consecutive if rows else None,
            "cum_dividend_5y": cum_div, "cum_netprofit_5y": cum_np,
            "payout_5y": round(cum_div / cum_np * 100, 2) if cum_div and cum_np else None, "buyback_3y": None}


def build_profile(code, base, fetch=fetch_sources, now=None):
    """base：快照 pending_profiles 中的身份信息（名称、板块、行业）。"""
    raw, errors = fetch(code)
    financials = build_financials(raw)
    profile = {
        "identity": build_identity(raw, code, base),
        "financials": financials,
        "shareholders": build_shareholders(raw, raw.get("survey") or {}),
        "dividends": build_dividends(raw, financials),
        "peers": {"peer_codes": [], "segments": []},
        "announcements": {"items": [], "count_12m": None}, "qa": {"items": []},
        "_meta": {"source": "东方财富 F10 / 数据中心（自动补档，待复核）",
                  "fetched_at": (now or datetime.now(CN_TZ)).isoformat(timespec="seconds"),
                  "errors": errors},
    }
    return profile


def cached_profile(code, base, fetch=fetch_sources, cache_dir=CACHE_DIR, now=None):
    """7 天内有完整缓存则复用；否则重新抓取，仅在无错误时覆盖缓存。"""
    now = now or datetime.now(CN_TZ)
    path = cache_dir / f"{code}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        fresh = now - datetime.fromisoformat(old["_meta"]["fetched_at"]) < timedelta(days=CACHE_DAYS)
        if fresh and not old["_meta"]["errors"]:
            old["identity"].update({k: base.get(k) for k in ("industry_sw1", "industry_sw2", "board") if base.get(k)})
            return old
    profile = build_profile(code, base, fetch, now)
    if path.exists():  # 公告、问答由 enrich_extras.py 首次获取，重建档案时沿用
        for key in ("announcements", "qa", "_extras"):
            if key in old and (key == "_extras" or old[key].get("items")):
                profile[key] = old[key]
    if not profile["_meta"]["errors"] or not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(temp, path)
    return profile


def apply_profiles(snapshot, fetch=fetch_sources, cache_dir=CACHE_DIR, extras=None):
    """把档案写入快照 pending_profiles[code].profile；返回各公司的失败组说明。"""
    problems = []
    for code, base in snapshot.get("pending_profiles", {}).items():
        try:
            profile = cached_profile(code, base, fetch, cache_dir)
        except Exception as exc:  # 单家公司的任何解析错误都只记录，不中断整批
            problems.append(f"{code}：{type(exc).__name__} {exc}")
            continue
        # 公告、问答、logo：首次出现时获取一次（失败记 _extras，不影响转正），写回缓存
        if "_extras" not in profile:
            try:
                if extras is None:
                    import enrich_extras
                    extras = enrich_extras.fill
                _, extra_problems = extras(code, profile)
                problems += extra_problems
                path = cache_dir / f"{code}.json"
                if path.exists():
                    path.write_text(json.dumps(profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            except Exception as exc:
                problems.append(f"{code} 附加资料跳过：{type(exc).__name__} {exc}")
        base["profile"] = profile
        if profile["_meta"]["errors"]:
            problems.append(f"{code}：{'、'.join(profile['_meta']['errors'])} 抓取失败")
    return problems


def prefetch_watchlist(snapshot, known_profiles, industry_map, limit=10, fetch=fetch_sources, cache_dir=CACHE_DIR):
    """临界区（第 101 名起）预先补档：只写缓存，进榜当天 cached_profile 直接复用。"""
    import enrich_profiles
    from build_live_universe import board_of
    rows = [r for r in snapshot.get("watchlist", [])[:limit] if r["code"] not in known_profiles]
    found, _ = enrich_profiles.resolve([r["code"] for r in rows], industry_map, names={r["code"]: r["name"] for r in rows})
    problems = []
    for r in rows:
        hit = found.get(r["code"], {})
        base = {"name_abbr": r["name"], "board": board_of(r["code"]),
                "industry_sw1": hit.get("sw1"), "industry_sw2": hit.get("sw2")}
        try:
            profile = cached_profile(r["code"], base, fetch, cache_dir)
            if profile["_meta"]["errors"]:
                problems.append(f"{r['code']}：{'、'.join(profile['_meta']['errors'])}")
        except Exception as exc:  # 同上
            problems.append(f"{r['code']}：{type(exc).__name__} {exc}")
    return len(rows), problems
