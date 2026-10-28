"""新进公司的附加资料：logo、近 12 个月公告、投资者问答。首次出现时获取一次。

- 公告：巨潮资讯公告查询，近 12 个月，存最新 20 条与 12 个月总数（与已核实档案同结构）。
- 问答：沪市取上证 e 互动（已回复 + 待回复，合并取最新 8 条）；深市/北交所取互动易（需巨潮 orgId，
  本机网络不可达，只在 GitHub Actions 上可抓）。
- logo：先试上证 e 互动的公司头像（沪市，公司自己上传，78px）与 Google 图标服务（128px），
  已有 ≥64px 的就不再访问官网；否则再取官网图标。取边长 ≥32px 的最大一张；都不合格就不放文件，
  页面显示首字图标，并在 logos.json 记 missing 等人工补。

附加资料失败不影响档案转正：问题记在 _meta.extras_errors，而不是 _meta.errors。
已有内容不覆盖（--refresh 才重抓）；日常更新另行接入每日两更。

用法：python scripts/enrich_extras.py 600016.SH [601995.SH ...] [--refresh]
"""

import argparse
import contextlib
import json
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_store as store  # noqa: E402
from enrich_company import get_json  # noqa: E402


WEB_DIR = store.ROOT / "ashare100" / "ashare100" / "web"
LOGO_DIR = WEB_DIR / "logos"
CN_TZ = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH = "https://www.cninfo.com.cn/new/information/topSearch/query"
SSE_E = "https://sns.sseinfo.com/"
IRM = "https://irm.cninfo.com.cn/"
ANN_N, QA_N, MIN_LOGO_PX = 20, 8, 32


def post(url, data, timeout=20):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={
        **UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


@contextlib.contextmanager
def prefer_ipv6(host):
    """临时让该主机的地址解析优先返回 IPv6。

    2026-10-27 实测：本机到 sns.sseinfo.com 的 IPv4 地址在 TLS 握手时被断开（SSL EOF），
    IPv6 正常（curl 默认走 IPv6 所以能通）。GitHub Actions 通常没有 IPv6，因此先试默认路径。
    """
    original = socket.getaddrinfo

    def patched(name, *args, **kwargs):
        if name == host:
            try:
                return original(name, *args[:1], socket.AF_INET6, *args[2:], **kwargs)
            except socket.gaierror:
                pass
        return original(name, *args, **kwargs)

    socket.getaddrinfo = patched
    try:
        yield
    finally:
        socket.getaddrinfo = original


def get_bytes(url, timeout=20, referer=None, attempts=2):
    req = urllib.request.Request(url, headers={**UA, **({"Referer": referer} if referer else {})})

    def once():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", ""), r.geturl()

    for attempt in range(attempts):
        try:
            return once()
        except OSError:
            try:  # 默认路径失败：改走 IPv6 再试一次
                with prefer_ipv6(urllib.parse.urlparse(url).hostname):
                    return once()
            except OSError:
                if attempt + 1 == attempts:
                    raise
                time.sleep(2)


# ---------- 公告 ----------

def cninfo_org_id(code):
    num = code.split(".")[0]
    rows = post(CNINFO_SEARCH, {"keyWord": num, "maxNum": 5})
    hit = next((r for r in rows if r.get("code") == num and r.get("category") == "A股"), None)
    if not hit:
        raise ValueError(f"巨潮检索不到 {num}")
    return hit["orgId"]


def fetch_announcements(code, today=None, org_id=None):
    today = today or date.today()
    num, exchange = code.split(".")
    doc = post(CNINFO_QUERY, {
        "stock": f"{num},{org_id or cninfo_org_id(code)}", "tabName": "fulltext", "pageSize": ANN_N, "pageNum": 1,
        "column": {"SH": "sse", "SZ": "szse"}.get(exchange, "bj"),
        "seDate": f"{today - timedelta(days=365)}~{today}"})
    items = []
    for a in doc.get("announcements") or []:
        day = datetime.fromtimestamp(a["announcementTime"] / 1000, CN_TZ).date().isoformat()
        title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "").strip()
        items.append({"title": title, "date": day, "type": None,
                      "url": "https://static.cninfo.com.cn/" + a["adjunctUrl"]})
    return {"items": items, "count_12m": doc.get("totalAnnouncement")}


# ---------- 问答（上证 e 互动） ----------

def sse_uid(code):
    raw, _, _ = get_bytes(f"{SSE_E}ajax/getCompany.do?data={code.split('.')[0]}")
    uid = raw.decode().strip()
    if not uid.isdigit():
        raise ValueError(f"e 互动查不到公司 uid：{uid[:40]!r}")
    return uid


def sse_time(text, now):
    """e 互动的时间：'2026年09月29日 15:16'、'10月25日 12:00'、'今天 09:38'、'昨天 09:38'、'5分钟前'。"""
    text = text.strip()
    if m := re.match(r"(\d{4})年(\d{2})月(\d{2})日", text):
        return f"{m[1]}-{m[2]}-{m[3]}"
    if m := re.match(r"(\d{1,2})月(\d{1,2})日", text):
        return f"{now.year}-{int(m[1]):02d}-{int(m[2]):02d}"
    if text.startswith("昨天"):
        return (now - timedelta(days=1)).date().isoformat()
    if text.startswith("今天") or re.match(r"\d+\s*(分钟|秒|小时)前", text):
        return now.date().isoformat()
    return None


def clean(fragment):
    text = re.sub(r"<a [^>]*>:[^<]*</a>", "", fragment)  # 去掉开头的「:公司(代码)」
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()


def parse_sse_feed(html, uid, now):
    """解析 userfeeds.do 返回的片段：每条 m_feed_item 含提问，已回复的还含 m_qa 回答块。"""
    out = []
    for block in re.split(r'<div class="m_feed_item[^"]*" id="item-', html)[1:]:
        qid = block.split('"', 1)[0]
        question, _, answer = block.partition('class="m_feed_detail m_qa"')
        q_txt = re.search(r'class="m_feed_txt"[^>]*>(.*?)</div>', question, re.S)
        q_time = re.search(r'class="m_feed_from"[^>]*>\s*<span>([^<]+)</span>', question)
        a_txt = re.search(r'class="m_feed_txt"[^>]*>(.*?)</div>', answer, re.S) if answer else None
        a_time = re.search(r'class="m_feed_from"[^>]*>\s*<span>([^<]+)</span>', answer) if answer else None
        if not q_txt:
            continue
        out.append({"id": qid, "question": clean(q_txt[1]), "answer": clean(a_txt[1]) if a_txt else None,
                    "ask_date": sse_time(q_time[1], now) if q_time else None,
                    "answer_date": sse_time(a_time[1], now) if a_time else None,
                    "source": "sse_e", "url": f"{SSE_E}company.do?uid={uid}"})
    return out


class Unsupported(ValueError):
    """来源已知不可用（不是临时失败）：只计数，不当作问题告警。"""


def irm_day(ms):
    return datetime.fromtimestamp(ms / 1000, CN_TZ).date().isoformat() if ms else None


def fetch_irm(code, org_id):
    """深市/北交所互动易：company/question 按提问时间倒序，含已回复与待回复。
    本机网络不可达，只在 GitHub Actions 上跑；orgId 与巨潮公告共用。"""
    num = code.split(".")[0]
    doc = post(f"{IRM}newircs/company/question?_t={int(time.time())}",
               {"stockcode": num, "orgId": org_id, "pageSize": QA_N, "pageNum": 1, "keyWord": "",
                "startDay": "", "endDay": ""})
    rows = doc.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"互动易返回格式异常：{str(doc)[:80]}")
    items = []
    for r in rows:
        question = clean(r.get("mainContent") or "")
        if not question:
            continue
        answer = clean(r.get("attachedContent") or "") or None
        items.append({"question": question, "answer": answer, "ask_date": irm_day(r.get("pubDate")),
                      "answer_date": irm_day(r.get("attachedPubDate") or r.get("updateDate")) if answer else None,
                      "source": "irm", "url": f"{IRM}views/interactiveAnswer?stockcode={num}&orgId={org_id}"})
    items.sort(key=lambda q: q["ask_date"] or "", reverse=True)
    return {"items": items[:QA_N], "count_12m": doc.get("total")}


def fetch_qa(code, now=None, uid=None, org_id=None):
    now = now or datetime.now(CN_TZ)
    exchange = code.split(".")[1]
    if exchange != "SH":
        if not org_id:
            raise Unsupported("互动易需要巨潮 orgId")
        return fetch_irm(code, org_id)
    uid = uid or sse_uid(code)
    rows = {}
    for kind in (11, 10):  # 11 = 已回复，10 = 待回复
        raw, _, _ = get_bytes(f"{SSE_E}ajax/userfeeds.do?typeCode=company&type={kind}&pageSize={QA_N}&uid={uid}&page=1",
                              referer=f"{SSE_E}company.do?uid={uid}")
        for item in parse_sse_feed(raw.decode("utf-8", "replace"), uid, now):
            rows.setdefault(item["id"], item)
    items = sorted(rows.values(), key=lambda r: (r["ask_date"] or "", r["id"]), reverse=True)[:QA_N]
    return {"items": [{k: v for k, v in r.items() if k != "id"} for r in items], "count_12m": None}


# ---------- logo ----------

def image_size(data):
    """PNG / ICO / GIF / JPEG 的最大边长；SVG 视为矢量（足够大）；无法识别返回 0。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return max(w, h)
    if data[:4] == b"\x00\x00\x01\x00":
        sizes = [(data[6 + 16 * i] or 256) for i in range(struct.unpack("<H", data[4:6])[0])]
        return max(sizes, default=0)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return max(struct.unpack("<HH", data[6:10]))
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                break
            marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return max(w, h)
            i += 2 + length
        return 0
    if b"<svg" in data[:500].lower():
        return 1024
    return 0


EXT = {b"\x89PNG": "png", b"\x00\x00\x01\x00": "ico", b"GIF8": "gif", b"\xff\xd8\xff": "jpg"}


def quick_candidates(code, website):
    found = []
    if code.endswith(".SH"):
        found.append((f"{SSE_E}resources/images/avatar/company/{code.split('.')[0]}.png", "sse-e-avatar"))
    if website:
        host = urllib.parse.urlparse(website if website.startswith("http") else "https://" + website).netloc
        found.append((f"https://www.google.com/s2/favicons?domain={host}&sz=128", "google-s2"))
    return found


def site_candidates(website):
    site = website if website.startswith("http") else "https://" + website
    found = []
    for base in (site, site.replace("https://", "http://", 1)):
        try:
            html, _, final = get_bytes(base, timeout=10, attempts=1)  # 官网打不开就算了，不长时间重试
        except OSError:
            continue
        text = html[:200000].decode("utf-8", "replace")
        for tag in re.findall(r"<link[^>]+>", text, re.I):
            if re.search(r'rel=["\'][^"\']*icon', tag, re.I) and (href := re.search(r'href=["\']([^"\']+)', tag, re.I)):
                found.append((urllib.parse.urljoin(final, unescape(href[1])), "official"))
        found.append((urllib.parse.urljoin(final, "/favicon.ico"), "official"))
        break
    return found


def fetch_logo(code, website, logo_dir=LOGO_DIR):
    """返回 logos.json 条目；有合格图片时写入 logo_dir/<代码>.<扩展名>。"""
    best = None
    tried = []

    def consider(candidates):
        nonlocal best
        for url, via in candidates:
            try:
                data, _, _ = get_bytes(url, timeout=10, attempts=1)
            except OSError as exc:
                tried.append(f"{via} 失败：{type(exc).__name__}")
                continue
            px = image_size(data)
            tried.append(f"{via} {px}px")
            if px >= MIN_LOGO_PX and (best is None or px > best[0]):
                best = (px, data, url, via)

    consider(quick_candidates(code, website))
    if website and (best is None or best[0] < 64):  # 官网常常很慢或打不开，只在前两类不够好时才去
        consider(site_candidates(website))
    if not best:
        return {"file": None, "missing": True, "tried": tried, "website": website}
    px, data, url, via = best
    ext = next((e for magic, e in EXT.items() if data.startswith(magic)), "svg")
    path = logo_dir / f"{code}.{ext}"
    path.write_bytes(data)
    return {"file": path.name, "source": url, "bytes": len(data), "via": via, "px": px}


def write_logo_index(code, entry, logo_dir=LOGO_DIR, web_dir=WEB_DIR):
    """更新 logos.json 并重新生成页面读取的 logos.js（代码 → 文件名）。"""
    index_path = logo_dir / "logos.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    index[code] = entry
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mapping = {k: v["file"] for k, v in sorted(index.items()) if v.get("file")}
    (web_dir / "logos.js").write_text(
        "/* 由 logos/logos.json 生成：代码 → logo 文件名。新增 logo 用 scripts/enrich_extras.py */\n"
        f"window.__LOGOS__ = {json.dumps(mapping, ensure_ascii=False, separators=(',', ':'))};\n", encoding="utf-8")


# ---------- 合并进档案 ----------

def fill(code, profile, refresh=False, fetchers=None, logo=True, refresh_logo=None):
    """把公告、问答（及 logo）补进 profile；只填空的部分。返回 (是否改动, 问题列表)。"""
    fetchers = fetchers or {"announcements": fetch_announcements, "qa": fetch_qa}
    meta = profile.setdefault("_extras", {})
    problems, changed = [], False
    for key, fetch in fetchers.items():
        if profile.get(key, {}).get("items") and not refresh:
            continue
        try:
            profile[key] = fetch(code)
            meta[key] = {"fetched_at": datetime.now(CN_TZ).isoformat(timespec="seconds"), "error": None}
            changed = True
        except Exception as exc:  # 单项失败只记录
            meta[key] = {"fetched_at": None, "error": f"{type(exc).__name__} {exc}"[:200]}
            problems.append(f"{code} {key}：{meta[key]['error']}")
            changed = True
    if logo:
        index_path = LOGO_DIR / "logos.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        if (refresh if refresh_logo is None else refresh_logo) or code not in index or index[code].get("missing"):
            entry = fetch_logo(code, profile.get("identity", {}).get("website"))
            write_logo_index(code, entry)
            if entry.get("missing"):
                problems.append(f"{code} logo：无合格图片（{'；'.join(entry['tried']) or '无官网'}），页面用首字图标")
    return changed, problems


def run(codes, refresh=False, logo_only=False):
    problems = []
    for code in codes:
        verified_path = store.VERIFIED_DIR / f"{code}.json"
        auto_path = store.AUTO_DIR / f"{code}.json"
        path = verified_path if verified_path.exists() else auto_path
        if not path.exists():
            problems.append(f"{code}：没有档案，先运行 enrich_profiles.py --backfill")
            continue
        profile = json.loads(path.read_text(encoding="utf-8"))
        changed, found = fill(code, profile, False if logo_only else refresh,
                              fetchers={} if logo_only else None, refresh_logo=refresh)
        problems += found
        if changed:
            store.write_json(path, profile)
        print(f"{code}：公告 {len(profile.get('announcements', {}).get('items') or [])} 条，"
              f"问答 {len(profile.get('qa', {}).get('items') or [])} 条（{path.parent.name}）")
    return problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("codes", nargs="+")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--logo-only", action="store_true", help="只补/重抓 logo")
    args = parser.parse_args()
    for p in run(args.codes, args.refresh, args.logo_only):
        print("问题：" + p)
