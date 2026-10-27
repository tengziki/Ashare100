"""公司档案质量检查：profiles/verified/（已核实）与 profiles/companies/（自动补档）。

两类规则：
- 硬规则（结构、代码、日期、行业、正负号）：已核实档案违反记为 error（退出码 1，工作流不提交）；
- 合理性规则（ROE 超出 ±100%、营收不足 1 亿元）：只记 warning。早期亏损的次新股确有这类真实值
  （如摩尔线程 2024 年 ROE -632%），需要人看，不能自动拦截。
自动补档本身「待复核」，所有问题都记为 warning，不阻塞榜单发布，供补档 skill 复核时处理。

用法：python scripts/validate_profiles.py [--verified-only] [代码 ...]
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_profiles  # noqa: E402  （申万一级 31 个行业）
import profiles_store as store  # noqa: E402


CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BOARDS = {"主板", "创业板", "科创板", "北交所"}


def check(code, c, today=None):
    """返回问题列表 [(字段, 说明, 是否硬规则)]。"""
    today = (today or date.today()).isoformat()
    out = []
    ident = c.get("identity") or {}

    def bad(field, why, hard=True):
        out.append((field, why, hard))

    if not CODE_RE.match(code) or f"{ident.get('code')}.{ident.get('exchange')}" != code:
        bad("identity.code", f"代码与文件名不符：{ident.get('code')}.{ident.get('exchange')}")
    if not ident.get("name_abbr"):
        bad("identity.name_abbr", "缺少简称")
    if ident.get("industry_sw1") not in enrich_profiles.SW1:
        bad("identity.industry_sw1", f"不是申万一级行业：{ident.get('industry_sw1')!r}")
    if ident.get("board") not in BOARDS:
        bad("identity.board", f"板块无效：{ident.get('board')!r}")
    for field in ("list_date",):
        v = ident.get(field)
        if v is not None and (not DATE_RE.match(str(v)) or v > today):
            bad(f"identity.{field}", f"日期无效或晚于今天：{v}")

    fin = c.get("financials") or {}
    years = [a.get("fiscal_year") for a in fin.get("annual") or []]
    if years != sorted(set(years)):
        bad("financials.annual", f"年度重复或未排序：{years}")
    for a in fin.get("annual") or []:
        y = a.get("fiscal_year")
        rev, roe, debt = a.get("revenue"), a.get("roe"), a.get("debt_ratio")
        if rev is not None and rev <= 0:
            bad(f"financials.{y}.revenue", f"营收应为正：{rev}")
        if rev is not None and 0 < rev < 1e8:
            bad(f"financials.{y}.revenue", f"营收不足 1 亿元，请确认单位：{rev}", hard=False)
        if roe is not None and not -100 <= roe <= 100:
            bad(f"financials.{y}.roe", f"ROE 超出 ±100%，请确认：{roe}", hard=False)
        if debt is not None and not 0 <= debt <= 100:
            bad(f"financials.{y}.debt_ratio", f"资产负债率超出 0–100%：{debt}")
    latest = fin.get("latest") or {}
    rp = latest.get("report_period")
    if rp is not None and (not DATE_RE.match(str(rp)) or rp > today):
        bad("financials.latest.report_period", f"报告期无效或晚于今天：{rp}")

    sh = c.get("shareholders") or {}
    for field in ("inst_hold_ratio", "northbound_ratio", "controlling_ratio"):
        v = sh.get(field)
        if v is not None and not 0 <= v <= 100:
            bad(f"shareholders.{field}", f"比例超出 0–100%：{v}")
    hd = sh.get("holder_num_date")
    if hd is not None and (not DATE_RE.match(str(hd)) or hd > today):
        bad("shareholders.holder_num_date", f"日期无效或晚于今天：{hd}")
    ratios = [t.get("hold_ratio") for t in sh.get("top10") or [] if t.get("hold_ratio") is not None]
    if sum(ratios) > 100.5:
        bad("shareholders.top10", f"前十大股东合计超过 100%：{sum(ratios):.2f}")

    for d in (c.get("dividends") or {}).get("annual") or []:
        dps = d.get("dps_pretax")
        if dps is not None and dps < 0:
            bad(f"dividends.{d.get('fiscal_year')}.dps_pretax", f"每股派息为负：{dps}")
    return out


def run(codes=None, verified_only=False, today=None):
    """返回 (errors, warnings)，元素为 (代码, 字段, 说明, 来源)。"""
    errors, warnings = [], []
    verified = store.load_verified()
    for code, c in sorted(verified.items()):
        if codes and code not in codes:
            continue
        for f, why, hard in check(code, c, today):
            (errors if hard else warnings).append((code, f, why, "verified"))
    if not verified_only and store.AUTO_DIR.exists():
        for path in sorted(store.AUTO_DIR.glob("*.json")):
            code = path.stem
            if (codes and code not in codes) or code in verified:
                continue
            c = store.load_auto(code)
            # 自动补档的行业可能尚未映射到：在快照阶段已单独提示，这里不重复
            warnings += [(code, f, why, "auto") for f, why, _ in check(code, c, today)
                         if not (f == "identity.industry_sw1" and c["identity"].get("industry_sw1") is None)]
            if c.get("_meta", {}).get("errors"):
                warnings.append((code, "_meta.errors", f"抓取失败组：{'、'.join(c['_meta']['errors'])}", "auto"))
    return errors, warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("codes", nargs="*")
    parser.add_argument("--verified-only", action="store_true")
    args = parser.parse_args()
    errors, warnings = run(set(args.codes) or None, args.verified_only)
    for level, rows in (("ERROR", errors), ("WARN", warnings)):
        for code, field, why, src in rows:
            print(f"{level} {code} [{src}] {field}：{why}")
    print(f"档案检查：{len(errors)} error，{len(warnings)} warning")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
