"""为动态百强的待补档公司自动补申万行业。

查找顺序：
1. profiles/sw_industry_map.json 中已核实（verified_profile）或人工（manual）的代码；
2. 东方财富 F10 「所属板块」中的申万 2021 一/二/三级（source=sw_board）。2026-10-29 抽 25 家已核实公司
   对照，二级全部一致（银行按下方规则改名后）；
3. 板块接口失败时，退回 EM2016 行业经映射表换算（source=em2016_mapped，只有一级较可靠）。
都失败时保持 null，下次运行重试；不阻塞榜单发布。

用法：
  python scripts/enrich_profiles.py --seed       # 用已核实档案（profiles/verified/）重建已核实部分
  python scripts/enrich_profiles.py --backfill   # 为当前 manifest 指向的快照补行业
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = ROOT / "ashare100" / "ashare100" / "profiles" / "sw_industry_map.json"
LIVE_DIR = ROOT / "ashare100" / "ashare100" / "web" / "live"
F10_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={ex}{num}"
BOARD_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/OperationsRequired/PageAjax?code={ex}{num}"
CN_TZ = timezone(timedelta(hours=8))

# 申万 2021 版 31 个一级行业
SW1 = {
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器", "食品饮料", "纺织服饰",
    "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售", "社会服务", "银行",
    "非银金融", "综合", "建筑材料", "建筑装饰", "电力设备", "机械设备", "国防军工", "计算机",
    "传媒", "通信", "煤炭", "石油石化", "环保", "美容护理",
}

# 东财 EM2016「一级-二级」→ 申万一级。先匹配二级，再匹配一级。
# 由已核实的 100 家对照实测得出（2026-10-27）；一级对应多个申万行业的（金融、化石能源、交运设备、
# 互联网）只按二级映射，二级不在表中则留空待人工补。
EM_TO_SW1 = {
    "金融-银行": "银行", "金融-非银行金融": "非银金融",
    "化石能源-煤炭": "煤炭", "化石能源-石油天然气": "石油石化",
    "交运设备-汽车": "汽车", "交运设备-汽车零部件": "汽车", "交运设备-铁路设备": "机械设备",
    "信息技术-通信设备": "通信", "信息技术-通信运营": "通信",
    "信息技术-计算机设备": "计算机", "信息技术-软件服务": "计算机",
    # 一级可唯一对应
    "电子设备": "电子", "电气设备": "电力设备", "公用事业": "公用事业", "交通运输": "交通运输",
    "农林牧渔": "农林牧渔", "医药生物": "医药生物", "基础化工": "基础化工", "家电": "家用电器",
    "建筑": "建筑装饰", "有色金属": "有色金属", "机械设备": "机械设备", "食品饮料": "食品饮料",
    "国防与装备": "国防军工", "钢铁": "钢铁", "房地产": "房地产",
}


# EM2016 三级 → 申万二级：只收录与已核实同行一致、且对应唯一的类别（2026-10-27 对照中信证券、
# 中国平安、宏桥控股、洛阳钼业、工商银行、招商银行、兴业银行）。「股份制与城商行」同时含两类，
# 用 12 家全国性股份制银行名单区分，其余留空。
EM3_TO_SW2 = {"证券": "证券Ⅱ", "保险": "保险Ⅱ", "铝": "工业金属", "铜": "工业金属", "国有银行": "国有大型银行Ⅱ"}
JOINT_STOCK_BANKS = {"招商银行", "浦发银行", "中信银行", "光大银行", "华夏银行", "民生银行",
                     "广发银行", "平安银行", "兴业银行", "浙商银行", "渤海银行", "恒丰银行"}


def em_to_sw2(em2016, name=None):
    parts = (em2016 or "").split("-")
    third = parts[2] if len(parts) > 2 else None
    if third == "股份制与城商行":
        return "股份制银行Ⅱ" if name in JOINT_STOCK_BANKS else None
    return EM3_TO_SW2.get(third)


def now_iso():
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_map():
    if MAP_FILE.exists():
        return json.loads(MAP_FILE.read_text(encoding="utf-8"))
    return {"meta": {"sw_version": "申万 2021", "updated": None}, "codes": {}}


def save_map(doc):
    doc["meta"]["updated"] = now_iso()
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = MAP_FILE.with_name(MAP_FILE.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, MAP_FILE)


def seed(doc):
    """已核实档案的行业优先级最高，覆盖映射得到的结果。"""
    import profiles_store
    companies = profiles_store.load_verified()
    for code, company in companies.items():
        ident = company["identity"]
        if ident.get("industry_sw1") in SW1:
            doc["codes"][code] = {"sw1": ident["industry_sw1"], "sw2": ident.get("industry_sw2"),
                                  "source": "verified_profile", "updated": now_iso()}
    return doc


def em_to_sw1(em2016):
    parts = (em2016 or "").split("-")
    for key in ("-".join(parts[:2]), parts[0]):
        if key in EM_TO_SW1:
            return EM_TO_SW1[key]
    return None


def fetch_em2016(code, timeout=15):
    num, exchange = code.split(".")
    req = urllib.request.Request(F10_URL.format(ex=exchange, num=num), headers={"User-Agent": "Mozilla/5.0"})
    from enrich_company import get_json  # 处理 gzip 响应
    doc = get_json(req.full_url, timeout)
    row = (doc.get("jbzl") or [{}])[0]
    if row.get("SECUCODE") != code:
        raise ValueError(f"F10 返回代码不符：{row.get('SECUCODE')}")
    return row.get("EM2016")


def board_to_sw(boards):
    """F10 所属板块 → (申万一级, 申万二级)。BOARD_RANK 1/2/3 为申万一/二/三级；
    东财银行仍用旧二级「银行Ⅱ」，新版已拆分，取三级改名（股份制银行Ⅲ → 股份制银行Ⅱ）。"""
    level = {b.get("BOARD_RANK"): b.get("BOARD_NAME") for b in boards or []}
    sw1, sw2 = level.get(1), level.get(2)
    if sw2 == "银行Ⅱ" and (level.get(3) or "").endswith("Ⅲ"):
        sw2 = level[3][:-1] + "Ⅱ"
    return sw1, sw2


def fetch_sw_board(code, timeout=15):
    num, exchange = code.split(".")
    from enrich_company import get_json
    doc = get_json(BOARD_URL.format(ex=exchange, num=num), timeout)
    boards = doc.get("ssbk") or []
    if any(b.get("SECUCODE") not in (None, code) for b in boards):
        raise ValueError("F10 所属板块代码不符")
    return board_to_sw(boards)


def resolve(codes, doc, fetch=fetch_em2016, names=None, board=fetch_sw_board):
    """返回 {code: {sw1, sw2, source}}；无法确定的公司不出现在结果中。names：代码 → 简称（判断股份制银行）。"""
    found, missing = {}, []
    names = names or {}
    for code in codes:
        hit = doc["codes"].get(code)
        if hit and hit.get("sw1") in SW1 and hit.get("source") != "em2016_mapped":
            found[code] = hit  # 已核实、人工或已取得申万板块
            continue
        try:
            sw1, sw2 = board(code)
        except (OSError, ValueError):
            sw1 = sw2 = None
        if sw1 in SW1:
            found[code] = doc["codes"][code] = {"sw1": sw1, "sw2": sw2, "source": "sw_board", "updated": now_iso()}
            continue
        if hit and hit.get("sw1") in SW1:  # 板块接口失败：沿用旧的 EM2016 映射
            if not hit.get("sw2") and hit.get("em2016"):
                hit["sw2"] = em_to_sw2(hit["em2016"], names.get(code))
            found[code] = hit
            continue
        try:
            em = fetch(code)
        except (OSError, ValueError) as exc:
            missing.append(f"{code}（接口失败：{exc}）")
            continue
        sw1 = em_to_sw1(em)
        if sw1 not in SW1:
            missing.append(f"{code}（EM2016「{em}」无对应申万一级）")
            continue
        found[code] = doc["codes"][code] = {"sw1": sw1, "sw2": em_to_sw2(em, names.get(code)), "source": "em2016_mapped",
                                            "em2016": em, "updated": now_iso()}
    return found, missing


def apply_to_snapshot(snapshot, doc, fetch=fetch_em2016, board=fetch_sw_board):
    """把行业写入快照的 pending_profiles；返回仍缺行业的说明列表。"""
    pending = snapshot.get("pending_profiles", {})
    found, missing = resolve(sorted(pending), doc, fetch, {c: p.get("name_abbr") for c, p in pending.items()}, board)
    for code, hit in found.items():
        pending[code].update({"industry_sw1": hit["sw1"], "industry_sw2": hit.get("sw2"),
                              "industry_source": hit["source"]})
    return missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    doc = load_map()
    if args.seed or not doc["codes"]:
        seed(doc)
    if args.backfill:
        manifest = json.loads((LIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
        path = LIVE_DIR / manifest["snapshot"]
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        missing = apply_to_snapshot(snapshot, doc)
        import enrich_company
        missing += enrich_company.apply_profiles(snapshot)
        if "vs_prev_close" not in snapshot.get("changes", {}):
            # 旧格式快照：补上相对静态收盘百强的进出榜（无全市场排序，退出公司的现名次记 null）
            import build_live_universe as live
            snapshot["changes"]["vs_prev_close"] = live.diff_members(live.static_base(), snapshot["members"])
        if "vs_prev_close" in snapshot.get("changes", {}):
            import archive_exited
            base = snapshot["changes"]["vs_prev_close"]
            base_doc = json.loads((LIVE_DIR / base["base_snapshot"]).read_text(encoding="utf-8")) if base["base_snapshot"] else None
            print(f"历史档：{archive_exited.archive(snapshot, base_doc, base['base_label'])}")
            n, problems = enrich_company.prefetch_watchlist(snapshot, set(__import__('profiles_store').load_verified()), doc)
            missing += problems
            print(f"临界区预补档 {n} 家")
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        print(f"已为 {manifest['snapshot']} 补档 {len(snapshot.get('pending_profiles', {}))} 家；"
              f"问题 {len(missing)} 项{('：' + '；'.join(missing)) if missing else ''}")
    save_map(doc)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"补档失败：{exc}", file=sys.stderr)
        sys.exit(1)
