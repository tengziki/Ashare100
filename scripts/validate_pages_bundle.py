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
    print(f"Pages bundle OK: {relative}, {meta['quote_date']}, 100 members")


if __name__ == "__main__":
    validate()
