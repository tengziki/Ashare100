"""自动复核：把新进公司的自动补档与巨潮年报摘要交叉核对，全部通过则转为已核实档案（review.method = auto）。

核对项（任一不通过就保持「自动补档 · 待复核」，原因写进数据状态，由 enrich-company skill 人工处理）：
1. validate_profiles 的硬规则无错误、各字段组抓取无失败；
2. 最近一年年报摘要正文里的营业（总）收入、归属于母公司股东的净利润，与东财数值相差 ≤1%；
   只读正文叙述句，不读「主要会计数据」表格（转文本后列会错位，见 enrich-company skill）；
3. 摘要「重要提示」里能读到审计意见（写入 identity.audit_opinion）；
   最近一个有分红的财年，每股派息（含中期）与巨潮「权益分派实施公告」正文里的每股现金红利合计一致；
4. 股东户数日期不早于最近一期财报；公告已取得；问答抓取无报错（0 条可以接受）。

页面对 method = auto 显示「自动复核」，与人工核实（method = manual）区分；人工复核后可改为 manual。
需要 pdftotext（poppler-utils 或 xpdf）。

用法：python scripts/auto_review.py [代码 ...]   # 默认：当前快照中有自动补档的百强公司
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_store as store  # noqa: E402


REVIEWER = "自动复核（scripts/auto_review.py）"
TOLERANCE = 0.01
LIVE_DIR = store.ROOT / "ashare100" / "ashare100" / "web" / "live"
ID_CACHE = store.ROOT / "ashare100" / "ashare100" / "profiles" / "disclosure_ids.json"
UNIT = {"亿元": 1e8, "万元": 1e4, "元": 1}
NUM = r"(?:人民币)?([\d,]+(?:\.\d+)?)(亿元|万元|元)"
REVENUE_RE = re.compile(r"实现营业(?:总)?收入" + NUM)
PROFIT_RE = re.compile(r"(?:实现)?归属于(?:母公司|上市公司|本行|本公司)?(?:普通股)?(?:股东|所有者)的净利润" + NUM)
OPINIONS = [("带强调事项段的无保留意见", "带强调事项段的无保留意见"), ("标准(?:的)?无保留意见", "标准无保留意见"),
            ("保留意见", "保留意见"), ("否定意见", "否定意见"), ("无法表示意见", "无法表示意见")]


def amount(match):
    return round(float(match[1].replace(",", "")) * UNIT[match[2]]) if match else None


def parse_summary(text):
    """年报摘要文本 → {revenue, net_profit, audit_opinion}；读不到的项为 None。"""
    flat = re.sub(r"\s+", "", text)
    head = flat[:6000]  # 审计意见在开头的「重要提示」里
    opinion = next((name for pattern, name in OPINIONS if re.search(pattern + r"(?:的)?审计报告|审计意见为[:：]?" + pattern, head)), None)
    return {"revenue": amount(REVENUE_RE.search(flat)), "net_profit": amount(PROFIT_RE.search(flat)), "audit_opinion": opinion}


DPS_RE = re.compile(r"每股派发现金红利(?:人民币)?([\d.]+)元")
DPS10_RE = re.compile(r"每10股派(?:发)?(?:现金(?:红利|股利))?(?:人民币)?([\d.]+)元")
NOTICE_RE = re.compile(r"(\d{4})年(?:年度|度|半年度|中期|前三季度|第三季度|第一季度|一季度)?(?:权益分派|利润分配)实施公告")


def parse_dps(text):
    """权益分派实施公告 → 每股税前现金红利（元）；读不到返回 None。"""
    flat = re.sub(r"\s+", "", text)
    if m := DPS_RE.search(flat):
        return float(m[1])
    if m := DPS10_RE.search(flat):
        return round(float(m[1]) / 10, 6)
    return None


def find_dividend_notices(code, org_id, year):
    """属于 year 财年的权益分派实施公告 [(标题, 链接)]（含中期）；调整、提示性公告除外。"""
    import enrich_extras as ex
    num, exchange = code.split(".")
    doc = ex.post(ex.CNINFO_QUERY, {"stock": f"{num},{org_id}", "tabName": "fulltext", "pageSize": 30, "pageNum": 1,
                                    "column": {"SH": "sse", "SZ": "szse"}.get(exchange, "bj"), "searchkey": "实施公告",
                                    "seDate": f"{year}-04-01~{year + 1}-12-31"})
    found = []
    for a in doc.get("announcements") or []:
        title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "")
        m = NOTICE_RE.search(title)
        if m and int(m[1]) == year and not re.search("调整|提示|更正|英文|H股", title):
            found.append((title, "https://static.cninfo.com.cn/" + a["adjunctUrl"]))
    return found


def check_dividend(code, profile, org_id, find_div, read):
    """返回 (检查说明, 失败说明)；最近财年未分红时不核对。"""
    paid = [a for a in profile["dividends"]["annual"] if a.get("dps_pretax")]
    if not paid:
        return "近五年东财无实施分红记录，不核对", None
    year, mine = paid[-1]["fiscal_year"], paid[-1]["dps_pretax"]
    notices = find_div(code, org_id, year)
    values = [parse_dps(read(url)) for _, url in notices]
    if not notices or None in values:
        return None, f"{year} 年分红：找不到或读不出权益分派实施公告"
    total = round(sum(values), 6)
    if abs(total - mine) <= max(0.0005, abs(mine) * TOLERANCE):
        return f"{year} 年每股派息 {mine} 元与 {len(notices)} 份权益分派实施公告合计 {total} 元一致", None
    return None, f"{year} 年每股派息：东财 {mine} 元，实施公告合计 {total} 元"


def find_summary(code, org_id, year):
    """巨潮上 year 年度报告摘要（中文版）的 (标题, 链接)；没有返回 None。"""
    import enrich_extras as ex
    num, exchange = code.split(".")
    doc = ex.post(ex.CNINFO_QUERY, {"stock": f"{num},{org_id}", "tabName": "fulltext", "pageSize": 30, "pageNum": 1,
                                    "column": {"SH": "sse", "SZ": "szse"}.get(exchange, "bj"),
                                    "category": "category_ndbg_szsh", "seDate": f"{year + 1}-01-01~{year + 1}-12-31"})
    for a in doc.get("announcements") or []:
        title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "")
        if str(year) in title and "摘要" in title and not re.search("英文|取消|更正前", title):
            return title, "https://static.cninfo.com.cn/" + a["adjunctUrl"]
    return None


def pdf_text(url):
    import enrich_extras as ex
    data, _, _ = ex.get_bytes(url, timeout=60)
    with tempfile.TemporaryDirectory() as tmp:
        pdf, txt = Path(tmp) / "a.pdf", Path(tmp) / "a.txt"
        pdf.write_bytes(data)
        subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(txt)], check=True, timeout=120)
        return txt.read_text(encoding="utf-8", errors="replace")


def close(a, b):
    return a is not None and b is not None and b != 0 and abs(a - b) / abs(b) <= TOLERANCE


def review(code, profile, org_id, find=find_summary, read=pdf_text, find_div=find_dividend_notices):
    """返回 (检查结果 {项: 说明}, 失败说明列表, 摘要链接)。"""
    import validate_profiles
    checks, fails = {}, []
    hard = [f"{f}：{why}" for f, why, is_hard in validate_profiles.check(code, profile) if is_hard]
    if hard or profile.get("_meta", {}).get("errors"):
        fails.append("档案校验：" + "；".join(hard or [f"抓取失败组 {'、'.join(profile['_meta']['errors'])}"]))
    annual = profile["financials"]["annual"]
    last = next((a for a in reversed(annual) if a.get("revenue") is not None), None)
    if not last:
        return checks, fails + ["没有年报财务数据"], None
    year = last["fiscal_year"]
    try:
        hit = find(code, org_id, year)
        if not hit:
            return checks, fails + [f"巨潮找不到 {year} 年报摘要"], None
        title, url = hit
        got = parse_summary(read(url))
    except Exception as exc:  # 网络、PDF 转换失败都只算本次未通过
        return checks, fails + [f"读取 {year} 年报摘要失败：{type(exc).__name__} {exc}"[:120]], None
    for key, label in (("revenue", "营业收入"), ("net_profit", "归母净利润")):
        mine, theirs = last.get(key), got[key]
        if close(mine, theirs):
            checks[key] = f"{year} 年{label}与《{title}》一致（差 {abs(mine - theirs) / abs(theirs):.2%}）"
        else:
            fails.append(f"{year} 年{label}：东财 {mine}，年报摘要 {theirs if theirs is not None else '未读到'}")
    if got["audit_opinion"]:
        checks["audit_opinion"] = f"{year} 年报审计意见：{got['audit_opinion']}"
    else:
        fails.append("年报摘要中未读到审计意见")
    try:
        ok, bad = check_dividend(code, profile, org_id, find_div, read)
    except Exception as exc:
        ok, bad = None, f"分红核对失败：{type(exc).__name__} {exc}"[:120]
    if ok:
        checks["dividend"] = ok
    else:
        fails.append(bad)
    s, latest = profile["shareholders"], profile["financials"].get("latest") or {}
    if s.get("holder_num_date") and latest.get("report_period") and s["holder_num_date"] < latest["report_period"]:
        fails.append(f"股东户数截至 {s['holder_num_date']}，早于最新财报 {latest['report_period']}")
    if not (profile.get("announcements") or {}).get("items"):
        fails.append("公告未取得")
    qa_error = ((profile.get("_extras") or {}).get("qa") or {}).get("error")
    if qa_error and not qa_error.startswith("Unsupported"):
        fails.append(f"问答抓取失败：{qa_error}")
    checks["audit_value"] = got["audit_opinion"]
    return checks, fails, url


def run(codes, ids=None, find=find_summary, read=pdf_text, auto_dir=store.AUTO_DIR, verified_dir=store.VERIFIED_DIR,
        find_div=find_dividend_notices):
    """逐家复核；通过的转正。返回 (已转正代码, 问题列表)。"""
    if ids is None:
        ids = json.loads(ID_CACHE.read_text(encoding="utf-8")).get("cninfo", {}) if ID_CACHE.exists() else {}
    promoted, problems = [], []
    for code in codes:
        profile = store.load_auto(code, auto_dir)
        if not profile or (verified_dir / f"{code}.json").exists():
            continue
        org_id = ids.get(code)
        if not org_id:
            try:
                import enrich_extras
                org_id = enrich_extras.cninfo_org_id(code)
            except Exception as exc:
                problems.append(f"{code} 自动复核未通过：巨潮 orgId 查询失败 {type(exc).__name__}")
                continue
        checks, fails, url = review(code, profile, org_id, find, read, find_div)
        if fails:
            problems.append(f"{code} 自动复核未通过：{'；'.join(fails)}"[:200])
            continue
        opinion = checks.pop("audit_value")
        profile["identity"]["audit_opinion"] = opinion
        store.write_json(auto_dir / f"{code}.json", profile)
        store.promote(code, REVIEWER, "与巨潮年报摘要、权益分派实施公告交叉核对营收、归母净利润、审计意见与分红", auto_dir, verified_dir,
                      extra={"method": "auto", "checks": checks, "sources": [url]})
        promoted.append(code)
    return promoted, problems


def current_auto_codes(live_dir=LIVE_DIR):
    manifest = json.loads((live_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((live_dir / manifest["snapshot"]).read_text(encoding="utf-8"))
    return [r["code"] for r in snapshot["members"] if r["code"] in snapshot.get("pending_profiles", {})]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("codes", nargs="*")
    args = parser.parse_args()
    promoted, problems = run(args.codes or current_auto_codes())
    for p in problems:
        print(p)
    print(f"自动复核：转正 {len(promoted)} 家{('（' + '、'.join(promoted) + '）') if promoted else ''}，未通过 {len(problems)} 家")


if __name__ == "__main__":
    main()
