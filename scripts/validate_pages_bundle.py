"""Fail the deployment if the static site cannot load its current dynamic snapshot."""

import json
import re
from pathlib import Path


WEB = Path(__file__).resolve().parent.parent / "ashare100" / "ashare100" / "web"
REQUIRED = ("index.html", "app.js", "data.js", "live-data.js", "style.css")
SNAPSHOT_RE = re.compile(r"snapshots/[A-Za-z0-9_-]+\.json\Z")
CODE_RE = re.compile(r"\d{6}\.(SH|SZ|BJ)\Z")


def validate():
    for name in REQUIRED:
        if not (WEB / name).is_file():
            raise ValueError(f"页面缺少 {name}")
    manifest = json.loads((WEB / "live" / "manifest.json").read_text(encoding="utf-8"))
    relative = manifest.get("snapshot")
    if not isinstance(relative, str) or not SNAPSHOT_RE.fullmatch(relative):
        raise ValueError("manifest 的快照路径无效")
    snapshot = json.loads((WEB / "live" / relative).read_text(encoding="utf-8"))
    meta = snapshot.get("meta", {})
    members = snapshot.get("members", [])
    if (meta.get("quote_date") != manifest.get("quote_date") or
            meta.get("ranking_metric") != "total_mv_CNY" or
            meta.get("member_count") != manifest.get("member_count") or
            len(members) != 100 or meta.get("listed_count", 0) < 5000 or
            meta.get("quoted_count") != meta.get("listed_count")):
        raise ValueError("快照与 manifest 不一致或全市场报价不完整")
    codes = [row.get("code") for row in members]
    if any(not isinstance(code, str) or not CODE_RE.fullmatch(code) for code in codes) or len(set(codes)) != 100:
        raise ValueError("百强代码无效或重复")
    if any(row.get("rank") != index or not isinstance(row.get("total_mv"), (int, float)) or
           row["total_mv"] <= 0 for index, row in enumerate(members, 1)):
        raise ValueError("百强名次或市值无效")
    # 日更指标可缺省；存在时必须指向同目录下有效文件。与快照同日时只能含当前百强代码；
    # 早于快照说明榜单已换手而指标尚未重算（15:40 任务），旧代码允许存在
    pointer = WEB / "live" / "metrics" / "latest.json"
    if pointer.exists():
        latest = json.loads(pointer.read_text(encoding="utf-8"))
        if not re.fullmatch(r"metrics/\d{8}\.json", str(latest.get("file"))):
            raise ValueError("metrics/latest.json 路径无效")
        metrics = json.loads((WEB / "live" / latest["file"]).read_text(encoding="utf-8"))
        if metrics["meta"]["quote_date"] != latest["quote_date"]:
            raise ValueError("日更指标与指针不一致")
        if metrics["meta"]["quote_date"] > meta["quote_date"]:
            raise ValueError("日更指标日期晚于快照")
        if metrics["meta"]["quote_date"] == meta["quote_date"] and not set(metrics["metrics"]) <= set(codes):
            raise ValueError("日更指标含非百强代码")
    # 档案增量包可缺省；存在且对应当前快照时，只能含当前百强
    bundle_path = WEB / "live" / "profiles" / "bundle.json"
    if bundle_path.exists():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        if bundle["meta"]["snapshot"] == relative and not set(bundle["profiles"]) <= set(codes):
            raise ValueError("档案包含非百强代码")
        if any(p.get("status") not in ("verified_snapshot", "auto_profile") or not p.get("profile", {}).get("identity")
               for p in bundle["profiles"].values()):
            raise ValueError("档案包条目缺少状态或身份信息")
    # 公告与问答可缺省；存在时条目结构完整。对应当前快照时只能含当前百强；
    # 对应旧快照说明榜单刚换手、公告尚未按新榜重抓（同一任务的 extras 步骤），出榜代码允许存在，页面会跳过
    disc_path = WEB / "live" / "disclosures" / "latest.json"
    if disc_path.exists():
        disc = json.loads(disc_path.read_text(encoding="utf-8"))
        if disc["meta"].get("snapshot") == relative and not set(disc["companies"]) <= set(codes):
            raise ValueError("公告问答含非百强代码")
        for code, d in disc["companies"].items():
            for item in (d.get("announcements") or {}).get("items") or []:
                if not item.get("title") or not item.get("url", "").startswith("https://static.cninfo.com.cn/"):
                    raise ValueError(f"{code} 公告条目无效")
    print(f"Pages bundle OK: {relative}, {meta['quote_date']}, 100 members")


if __name__ == "__main__":
    validate()
