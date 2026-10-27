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
PROFILE_FILE = ROOT / "ashare100" / "ashare100" / "data.json"
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


def previous_members(output_dir):
    manifest = output_dir / "manifest.json"
    if manifest.exists():
        doc = json.loads((output_dir / json.loads(manifest.read_text(encoding="utf-8"))["snapshot"]).read_text(encoding="utf-8"))
        return [item["code"] for item in doc["members"]]
    return [item["code"] for item in json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["companies"]]


def board_of(code):
    num, exchange = code.split(".")
    if exchange == "BJ":
        return "北交所"
    if exchange == "SH" and num.startswith(("688", "689")):
        return "科创板"
    if exchange == "SZ" and num.startswith("3"):
        return "创业板"
    return "主板"


def build_snapshot(codes, quotes, old_members, known_profiles, slot, now):
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
    members = sorted(quotes.values(), key=lambda row: (-row["total_mv"], row["code"]))[:100]
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
            "listed_count": len(codes), "quoted_count": len(quotes), "member_count": len(members),
            "sources": {"codes": LIST_URL, "quotes": QUOTE_URL},
        },
        "members": members,
        "changes": {
            "entered": sorted(set(new_ranks) - set(old_ranks)),
            "exited": sorted(set(old_ranks) - set(new_ranks)),
            "rank_change": {code: old_ranks[code] - rank for code, rank in new_ranks.items()
                            if code in old_ranks and old_ranks[code] != rank},
        },
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
    old = previous_members(args.output_dir)
    known = set(json.loads(PROFILE_FILE.read_text(encoding="utf-8"))["companies"])
    snapshot = build_snapshot(codes, quotes, old, known, args.slot, datetime.now(CN_TZ))
    if snapshot is None:
        print("非交易日或行情仍为上一交易日：保留现有快照")
        return
    filename = publish(snapshot, args.output_dir, list_last_modified)
    print(f"已发布 {filename}：{len(codes)} 支全市场，前 100，"
          f"新进 {len(snapshot['changes']['entered'])}，退出 {len(snapshot['changes']['exited'])}，"
          f"待补档 {len(snapshot['pending_profiles'])}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"发布失败，保留旧版：{exc}", file=sys.stderr)
        sys.exit(1)
