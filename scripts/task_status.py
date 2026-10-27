"""数据任务状态：每个任务成功后写 web/live/status.json，页面据此显示各类数据的更新时间与是否过期。

只记录成功（含部分成功的问题清单）：失败的运行不会提交与部署，
由工作流的失败告警（GitHub issue）通知；页面靠「距上次成功太久」显示过期。
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


LIVE_DIR = Path(__file__).resolve().parent.parent / "ashare100" / "ashare100" / "web" / "live"
CN_TZ = timezone(timedelta(hours=8))
# 页面判断过期用：超过 warn 个「工作日小时」显示黄色，超过 stale 显示红色；
# 比整 1 / 2 个交易日多留 4 小时，容纳 GitHub 定时触发的延迟
EXPECT = {
    "live_universe": {"label": "榜单与行情", "schedule": "交易日 12:00 / 15:00", "warn_hours": 28, "stale_hours": 52},
    "daily_metrics": {"label": "年初至今 / 52 周 / 股息率", "schedule": "交易日 15:40", "warn_hours": 28, "stale_hours": 52},
    "disclosures": {"label": "公告与投资者问答", "schedule": "交易日 12:00 / 15:00", "warn_hours": 28, "stale_hours": 52},
    "profiles": {"label": "新进公司补档", "schedule": "随榜单更新", "warn_hours": None, "stale_hours": None},
}


def record(task, data_date=None, problems=(), detail=None, live_dir=LIVE_DIR, now=None):
    path = live_dir / "status.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"tasks": {}}
    problems = [str(p)[:200] for p in problems][:20]
    doc["tasks"][task] = {
        **EXPECT[task],
        "last_success": (now or datetime.now(CN_TZ)).isoformat(timespec="seconds"),
        "data_date": data_date, "detail": detail, "problems": problems,
    }
    doc["updated"] = doc["tasks"][task]["last_success"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return doc
