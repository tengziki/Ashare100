"""用免费公开代码表和腾讯行情生成动态 A 股市值前 100 快照。

依赖 Python 标准库。完整性检查通过后才写入 web/live/manifest.json；
失败或非交易日保留上一个成功版本。
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "ashare100" / "ashare100" / "web" / "live"
BASELINE_FILE = ROOT / "ashare100" / "ashare100" / "codes_top100.json"
LIST_URL = "https://assets.linkdiary.cn/shares/zh_a_list.json"
QUOTE_URL = "https://qt.gtimg.cn/q="
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
CN_TZ = timezone(timedelta(hours=8))
CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$")


def request_bytes(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), response.headers


def fetch_codes():
    raw, headers = request_bytes(LIST_URL)
    doc = json.loads(raw)
    if doc.get("success") is not True or not isinstance(doc.get("list"), list):
        raise ValueError("股票代码表结构异常")
    codes = doc["list"]
    if len(codes) < 5000 or len(codes) != len(set(codes)) or any(not CODE_RE.fullmatch(c) for c in codes):
        raise ValueError(f"股票代码表不完整或重复：{len(codes)} 条")
    last_modified = headers.get("Last-Modified")
    if last_modified:
        modified = parsedate_to_datetime(last_modified).astimezone(CN_TZ)
        if datetime.now(CN_TZ) - modified > timedelta(days=7):
            raise ValueError(f"股票代码表已超过 7 天未更新：{last_modified}")
    return codes, last_modified


def parse_quotes(raw, requested):
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        body = raw.decode("gb18030")
    requested_set = set(requested)
    found = {}
    for line in body.splitlines():
        if '="' not in line:
            continue
        prefix, payload = line.split('="', 1)
        code = prefix[2:] if prefix.startswith("v_") else prefix
        if code not in requested_set:
            continue
        fields = payload.split('"', 1)[0].split("~")
        if len(fields) < 47 or fields[2] != code[2:]:
            continue
        try:
            price = float(fields[3])
            total_mv = float(fields[45]) * 1e8  # 腾讯源以“亿元”返回
            float_mv = float(fields[44]) * 1e8
        except ValueError:
            continue
        timestamp = fields[30]
        if (not re.fullmatch(r"\d{14}", timestamp) or
                not all(math.isfinite(x) and x > 0 for x in (price, total_mv, float_mv))):
            continue

        def optional_num(index, multiplier=1):
            try:
                value = float(fields[index]) * multiplier
                return value if math.isfinite(value) else None
            except (ValueError, IndexError):
                return None

        found[code] = {
            "code": code[2:] + "." + code[:2].upper(), "name": fields[1],
            "price": price, "change_pct": optional_num(32),
            "amount": optional_num(37, 1e4),  # 腾讯源以“万元”返回
            "turnover_rate": optional_num(38), "pe_ttm": optional_num(39),
            "pb": optional_num(46), "total_mv": total_mv, "float_mv": float_mv,
            "quote_at": timestamp,
        }
    return found


def fetch_batch(codes, attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            raw, _ = request_bytes(QUOTE_URL + ",".join(codes))
            found = parse_quotes(raw, codes)
            if len(found) != len(codes):
                missing = sorted({c[2:] + "." + c[:2].upper() for c in codes} - set(found))
                raise ValueError(f"缺少 {len(missing)} 支报价，例：{missing[:3]}")
            return found
        except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"批次 {codes[0]}… 失败：{last_error}")


def fetch_all_quotes(codes, workers=3):
    batches = [codes[i:i + 50] for i in range(0, len(codes), 50)]
    quotes = {}
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                quotes.update(future.result())
            except RuntimeError:
                failed.append(futures[future])
    # 对连续抓取时偶发失败的批次，低并发单独恢复一次。
    for batch in failed:
        quotes.update(fetch_batch(batch, attempts=3))
    return quotes


def snapshot_base(output_dir, relative, doc):
    """把一份已发布快照转成对比基准：成员 + 临界名单（用于查新进公司原名次）。"""
    meta = doc["meta"]
    slot = "收盘" if meta.get("ranking_status") == "close_confirmed" else meta.get("quote_max_at", "")[11:16]
    rows = doc["members"] + doc.get("watchlist", [])
    return {"snapshot": relative, "label": f"{meta['quote_date']} {slot}",
            "ranked": [{"code": r["code"], "name": r.get("name"), "rank": r["rank"]} for r in rows]}


def static_base():
    doc = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return {"snapshot": None, "label": f"{doc.get('as_of', '静态')} 收盘（静态基准）",
            "ranked": [{"code": r["code"], "name": r.get("name_abbr"), "rank": i}
                       for i, r in enumerate(doc["companies"], 1)]}


EM_VALUATION = "https://datacenter-web.eastmoney.com/api/data/v1/get?"


def em_prev_close_base(today, top_n=150):
    """还没有自存的上一交易日收盘快照时（首次运行）：用东方财富日估值表取上一交易日收盘总市值前 150。

    2026-10-27 实测：该表 10-26 的中金公司已按换股吸收合并后的股本计市值（2,498 亿，第 50 名），
    比用「昨收 × 今日股本」推算更可靠。之后每天以自存的收盘快照为准，此函数只在冷启动时用一次。
    """
    import urllib.parse
    from enrich_company import get_json

    def query(**params):
        doc = get_json(EM_VALUATION + urllib.parse.urlencode({
            "reportName": "RPT_VALUEANALYSIS_DET", "columns": "SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,TOTAL_MARKET_CAP",
            **params}))
        return (doc.get("result") or {}).get("data") or []

    last = query(filter=f"(TRADE_DATE<'{today.isoformat()}')", sortColumns="TRADE_DATE", sortTypes=-1, pageSize=1)
    if not last:
        raise ValueError("东财日估值查不到上一交易日")
    day = last[0]["TRADE_DATE"][:10]
    rows = query(filter=f"(TRADE_DATE='{day}')", sortColumns="TOTAL_MARKET_CAP", sortTypes=-1, pageSize=top_n)
    if len(rows) < 100 or any(not r.get("TOTAL_MARKET_CAP") for r in rows[:100]):
        raise ValueError(f"东财日估值 {day} 数据不完整：{len(rows)} 条")
    return {"snapshot": None, "label": f"{day} 收盘（东方财富日估值）",
            "ranked": [{"code": r["SECUCODE"], "name": r["SECURITY_NAME_ABBR"], "rank": i}
                       for i, r in enumerate(rows, 1)]}


def load_bases(output_dir, today, prev_close_fallback=None):
    """上一次运行（vs_prev_run）与上一交易日收盘（vs_prev_close，首页默认）两个基准。"""
    prev_run = prev_close = None
    manifest = output_dir / "manifest.json"
    if manifest.exists():
        relative = json.loads(manifest.read_text(encoding="utf-8"))["snapshot"]
        prev_run = snapshot_base(output_dir, relative, json.loads((output_dir / relative).read_text(encoding="utf-8")))
    best = None
    for path in sorted((output_dir / "snapshots").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        meta = doc.get("meta", {})
        if meta.get("ranking_status") == "close_confirmed" and meta.get("quote_date", "") < today.isoformat():
            if best is None or meta["quote_date"] >= best[1]["meta"]["quote_date"]:
                best = (f"snapshots/{path.name}", doc)
    if best:
        prev_close = snapshot_base(output_dir, *best)
    if prev_close is None:
        try:
            prev_close = (prev_close_fallback or em_prev_close_base)(today)
        except (OSError, ValueError, KeyError) as exc:
            print(f"上一交易日收盘基准取不到，退回静态百强：{exc}", file=sys.stderr)
    return {"prev_run": prev_run or static_base(), "prev_close": prev_close or static_base()}


def diff_members(base, ranked_all, top_n=100):
    """相对基准的新进、退出与名次变化；退出公司带名称与当前全市场名次。"""
    old_top = {r["code"]: r for r in base["ranked"] if r["rank"] <= top_n}
    old_any = {r["code"]: r for r in base["ranked"]}
    now = {r["code"]: (i, r) for i, r in enumerate(ranked_all, 1)}
    new_top = {r["code"] for r in ranked_all[:top_n]}
    entered = [{"code": c, "name": now[c][1]["name"], "rank": now[c][0],
                "prev_rank": old_any.get(c, {}).get("rank")}
               for c in sorted(new_top - set(old_top), key=lambda c: now[c][0])]
    exited = [{"code": c, "name": now[c][1]["name"] if c in now else old_top[c]["name"],
               "prev_rank": old_top[c]["rank"], "now_rank": now[c][0] if c in now else None,
               "total_mv": now[c][1]["total_mv"] if c in now else None}
              for c in sorted(set(old_top) - new_top, key=lambda c: old_top[c]["rank"])]
    return {"base_snapshot": base["snapshot"], "base_label": base["label"],
            "entered": entered, "exited": exited,
            "rank_change": {c: old_top[c]["rank"] - now[c][0] for c in new_top
                            if c in old_top and old_top[c]["rank"] != now[c][0]}}


def board_of(code):
    num, exchange = code.split(".")
    if exchange == "BJ":
        return "北交所"
    if exchange == "SH" and num.startswith(("688", "689")):
        return "科创板"
    if exchange == "SZ" and num.startswith("3"):
        return "创业板"
    return "主板"


def build_snapshot(codes, quotes, old_members, known_profiles, slot, now, bases=None):
    if len(codes) != len(set(codes)) or set(quotes) != set(codes):
        raise ValueError(f"行情不完整：{len(quotes)}/{len(codes)}")
    timestamps = [datetime.strptime(row["quote_at"], "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)
                  for row in quotes.values()]
    dates = {stamp.date() for stamp in timestamps}
    today = now.date()
    if dates == {today - timedelta(days=1)} or (len(dates) == 1 and next(iter(dates)) < today):
        return None  # 非交易日：不把旧行情伪装成今天的数据
    if dates != {today}:
        raise ValueError(f"报价日期混杂或不属于今天：{sorted(str(d) for d in dates)}")
    latest = max(timestamps)
    if latest > now + timedelta(minutes=5):
        raise ValueError("行情时间晚于当前时间")
    if slot == "noon" and latest.time() < datetime.strptime("11:25", "%H:%M").time():
        raise ValueError("午间行情尚未到齐")
    if slot == "close":
        close_time = datetime.strptime("15:00", "%H:%M").time()
        fresh = sum(stamp.time() >= datetime.strptime("14:55", "%H:%M").time() for stamp in timestamps)
        if latest.time() < close_time or fresh < len(timestamps) * 0.95:
            raise ValueError(f"收盘行情尚未到齐：最近 {latest.time()}，14:55 后 {fresh}/{len(timestamps)}")
    ranked_all = sorted(quotes.values(), key=lambda row: (-row["total_mv"], row["code"]))
    members = ranked_all[:100]
    watchlist = [{"code": r["code"], "name": r["name"], "rank": i, "total_mv": r["total_mv"]}
                 for i, r in enumerate(ranked_all[100:150], 101)]
    if bases is None:
        legacy = {"snapshot": None, "label": "上一版", "ranked": [{"code": c, "name": None, "rank": i} for i, c in enumerate(old_members, 1)]}
        bases = {"prev_run": legacy, "prev_close": legacy}
    old_ranks = {code: index + 1 for index, code in enumerate(old_members)}
    new_ranks = {row["code"]: index + 1 for index, row in enumerate(members)}
    pending = {}
    for rank, row in enumerate(members, 1):
        row["rank"] = rank
        row["profile_status"] = "snapshot" if row["code"] in known_profiles else "pending"
        if row["profile_status"] == "pending":
            num, exchange = row["code"].split(".")
            pending[row["code"]] = {
                "code": num, "exchange": exchange, "name_abbr": row["name"],
                "board": board_of(row["code"]), "industry_sw1": None,
                "status": "待补完整公司档案",
            }
    generated = now.isoformat(timespec="seconds")
    return {
        "meta": {
            "schema_version": "2.0-universe", "generated_at": generated,
            "quote_date": today.isoformat(), "quote_min_at": min(timestamps).isoformat(timespec="seconds"),
            "quote_max_at": latest.isoformat(timespec="seconds"),
            "ranking_metric": "total_mv_CNY", "ranking_status": "close_confirmed" if slot == "close" else "intraday_provisional",
            "slot": slot, "listed_count": len(codes), "quoted_count": len(quotes), "member_count": len(members),
            "sources": {"codes": LIST_URL, "quotes": QUOTE_URL},
        },
        "members": members,
        "changes": {
            "entered": sorted(set(new_ranks) - set(old_ranks)),
            "exited": sorted(set(old_ranks) - set(new_ranks)),
            "rank_change": {code: old_ranks[code] - rank for code, rank in new_ranks.items()
                            if code in old_ranks and old_ranks[code] != rank},
            "vs_prev_close": diff_members(bases["prev_close"], ranked_all),
            "vs_prev_run": diff_members(bases["prev_run"], ranked_all),
        },
        "watchlist": watchlist,
        "pending_profiles": pending,
    }


def atomic_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def publish(snapshot, output_dir, list_last_modified):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"snapshots/{stamp}.json"
    snapshot["meta"]["list_last_modified"] = list_last_modified
    atomic_json(output_dir / filename, snapshot)
    # 进出榜记录：每版追加一条，前端可直接读取历次变化
    history_path = output_dir / "history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    vs = snapshot["changes"]["vs_prev_run"]
    history.append({"snapshot": filename, "quote_date": snapshot["meta"]["quote_date"],
                    "slot": snapshot["meta"]["slot"], "base_label": vs["base_label"],
                    "entered": [r["code"] for r in vs["entered"]], "exited": [r["code"] for r in vs["exited"]]})
    atomic_json(history_path, history)
    manifest = {
        "snapshot": filename, "generated_at": snapshot["meta"]["generated_at"],
        "quote_date": snapshot["meta"]["quote_date"],
        "ranking_status": snapshot["meta"]["ranking_status"],
        "member_count": snapshot["meta"]["member_count"],
    }
    atomic_json(output_dir / "manifest.json", manifest)  # 最后切换入口
    return filename


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=("intraday", "noon", "close"), default="intraday")
    parser.add_argument("--output-dir", type=Path, default=LIVE_DIR)
    args = parser.parse_args()
    codes, list_last_modified = fetch_codes()
    quotes = fetch_all_quotes(codes)
    now = datetime.now(CN_TZ)
    bases = load_bases(args.output_dir, now.date())
    old = [r["code"] for r in bases["prev_run"]["ranked"] if r["rank"] <= 100]
    import profiles_store
    known = set(profiles_store.load_verified())
    snapshot = build_snapshot(codes, quotes, old, known, args.slot, now, bases)
    if snapshot is None:
        print("非交易日或行情仍为上一交易日：保留现有快照")
        # 工作日 10:00 后行情仍非今日 = 节假日休市（开盘前的运行不能据此判断）
        if now.weekday() < 5 and now.hour >= 10:
            import task_status
            task_status.mark_closed(now.date(), live_dir=args.output_dir)
            print(f"已记为休市：{now.date()}")
        return
    # 行业补档失败不阻塞发布：缺的公司保持 null，下次运行重试
    profile_problems = []
    try:
        import enrich_profiles
        industry_map = enrich_profiles.load_map()
        if not industry_map["codes"]:
            enrich_profiles.seed(industry_map)
        missing = enrich_profiles.apply_to_snapshot(snapshot, industry_map)
        enrich_profiles.save_map(industry_map)
        if missing:
            profile_problems += missing
            print("行业待补：" + "；".join(missing), file=sys.stderr)
        import enrich_company
        problems = enrich_company.apply_profiles(snapshot)
        if problems:
            profile_problems += problems
            print("档案待补：" + "；".join(problems), file=sys.stderr)
    except Exception as exc:  # 补档是附属任务，任何异常都不能阻止榜单发布
        profile_problems.append(f"补档跳过：{type(exc).__name__} {exc}")
        print(f"补档跳过：{type(exc).__name__} {exc}", file=sys.stderr)
    filename = publish(snapshot, args.output_dir, list_last_modified)
    # 发布后的附属任务：失败只记日志，不影响已发布的榜单
    try:
        import archive_exited
        base = bases["prev_close"]
        base_doc = json.loads((args.output_dir / base["snapshot"]).read_text(encoding="utf-8")) if base["snapshot"] else None
        ranked = sorted(quotes.values(), key=lambda r: (-r["total_mv"], r["code"]))
        now_ranks = {r["code"]: (i, r["total_mv"]) for i, r in enumerate(ranked, 1)}
        archived = archive_exited.archive(snapshot, base_doc, base["label"], args.output_dir, now_ranks=now_ranks)
        n, problems = enrich_company.prefetch_watchlist(snapshot, known, industry_map)
        enrich_profiles.save_map(industry_map)
        profile_problems += problems
        print(f"历史档 {len(archived)} 家；临界区预补档 {n} 家" + (f"，问题：{'；'.join(problems)}" if problems else ""))
    except Exception as exc:  # 榜单已发布：附属任务失败只记日志，退出码保持 0
        profile_problems.append(f"历史档/预补档跳过：{type(exc).__name__} {exc}")
        print(f"历史档/预补档跳过：{type(exc).__name__} {exc}", file=sys.stderr)
    try:
        import build_profile_bundle
        meta = build_profile_bundle.run(args.output_dir)["meta"]
        if meta["missing"]:
            profile_problems.append(f"无档案：{'、'.join(meta['missing'])}")
    except Exception as exc:
        profile_problems.append(f"档案包跳过：{type(exc).__name__} {exc}")
        print(f"档案包跳过：{type(exc).__name__} {exc}", file=sys.stderr)
    try:
        import task_status
        vs = snapshot["changes"]["vs_prev_close"]
        task_status.record("live_universe", snapshot["meta"]["quote_date"],
                           detail=f"{'收盘确认' if snapshot['meta']['ranking_status'] == 'close_confirmed' else '盘中暂定'} · 较{vs['base_label']}新进 {len(vs['entered'])}、退出 {len(vs['exited'])}",
                           live_dir=args.output_dir)
        task_status.record("profiles", snapshot["meta"]["quote_date"], profile_problems,
                           detail=f"待补档 {len(snapshot['pending_profiles'])} 家", live_dir=args.output_dir)
    except Exception as exc:
        print(f"状态记录跳过：{type(exc).__name__} {exc}", file=sys.stderr)
    print(f"已发布 {filename}：{len(codes)} 支全市场，前 100，"
          f"较{snapshot['changes']['vs_prev_close']['base_label']}新进 {len(snapshot['changes']['vs_prev_close']['entered'])}、"
          f"退出 {len(snapshot['changes']['vs_prev_close']['exited'])}，"
          f"待补档 {len(snapshot['pending_profiles'])}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"发布失败，保留旧版：{exc}", file=sys.stderr)
        sys.exit(1)
