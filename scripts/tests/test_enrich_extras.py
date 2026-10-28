"""附加资料的离线测试：e 互动时间与片段解析、图片尺寸识别、只补空项、失败不进 _meta.errors。"""

import struct
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrich_extras as ex

NOW = datetime(2026, 10, 27, 17, 0, tzinfo=ex.CN_TZ)

# 按 2026-10-27 实抓 userfeeds.do 的结构精简：一条待回复、一条已回复
FEED = """
<div class="m_feed_item" style="display: flex"><input type="hidden" id="currentPage" value="1" /></div>
<div class="m_feed_item m_question" id="item-1798915"><div class="m_feed_detail">
  <div class="m_feed_txt" id="m_feed_txt-1798915"> <a href='user.do?uid=16' >:民生银行(600016)</a>董秘您好，请问&amp;教育经历？ </div>
  <div class="m_feed_func"><div class="m_feed_from"> <span>昨天 09:38</span> <em>来自</em></div></div></div></div>
<div class="m_feed_item" id="item-1790001"><div class="m_feed_detail m_qa_detail">
  <div class="m_feed_txt" id="m_feed_txt-1790001"> <a href='user.do?uid=16' >:民生银行(600016)</a>分红何时到账？ </div>
  <div class="m_feed_from"> <span>2026年09月29日 15:16</span></div></div>
  <div class="m_feed_detail m_qa"><div class="m_feed_txt" id="m_feed_txt-1790001"> 尊敬的投资者，<b>已于</b>当日到账。 </div>
  <div class="m_feed_from" style="padding-left: 35px;"> <span>10月08日 10:00</span></div></div></div>
"""


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"


class ExtrasTests(unittest.TestCase):
    def test_sse_time_formats(self):
        self.assertEqual(ex.sse_time("2026年09月29日 15:16", NOW), "2026-09-29")
        self.assertEqual(ex.sse_time("10月08日 10:00", NOW), "2026-10-08")
        self.assertEqual(ex.sse_time("昨天 09:38", NOW), "2026-10-26")
        self.assertEqual(ex.sse_time("今天 09:38", NOW), "2026-10-27")
        self.assertEqual(ex.sse_time("5分钟前", NOW), "2026-10-27")
        self.assertIsNone(ex.sse_time("很久以前", NOW))

    def test_parse_feed_question_and_answer(self):
        rows = ex.parse_sse_feed(FEED, "16", NOW)
        self.assertEqual([r["id"] for r in rows], ["1798915", "1790001"])
        self.assertEqual(rows[0]["question"], "董秘您好，请问&教育经历？")  # 去掉「:公司(代码)」并反转义
        self.assertEqual((rows[0]["answer"], rows[0]["ask_date"]), (None, "2026-10-26"))
        self.assertEqual((rows[1]["answer"], rows[1]["answer_date"]), ("尊敬的投资者，已于当日到账。", "2026-10-08"))
        self.assertEqual(rows[1]["url"], "https://sns.sseinfo.com/company.do?uid=16")

    def test_shenzhen_qa_is_reported_not_guessed(self):
        with self.assertRaises(ex.Unsupported):
            ex.fetch_qa("300124.SZ", NOW)

    def test_irm_rows(self):
        day = lambda s: int(datetime.fromisoformat(s + "T10:00:00+08:00").timestamp() * 1000)
        doc = {"total": 50, "rows": [
            {"mainContent": "请问储能&amp;出货？", "pubDate": day("2026-10-20"), "attachedContent": None},
            {"mainContent": "分红计划？", "pubDate": day("2026-10-25"), "attachedContent": "<p>感谢关注。</p>",
             "attachedPubDate": day("2026-10-27")},
            {"mainContent": "", "pubDate": day("2026-10-26")}]}
        original, ex.post = ex.post, lambda url, data, timeout=20: doc
        try:
            qa = ex.fetch_qa("300750.SZ", NOW, org_id="GD165627")
        finally:
            ex.post = original
        self.assertEqual(qa["count_12m"], 50)
        self.assertEqual([(q["ask_date"], q["answer"], q["answer_date"]) for q in qa["items"]],
                         [("2026-10-25", "感谢关注。", "2026-10-27"), ("2026-10-20", None, None)])
        self.assertEqual(qa["items"][1]["question"], "请问储能&出货？")
        self.assertEqual(qa["items"][0]["source"], "irm")

    def test_image_size(self):
        self.assertEqual(ex.image_size(png(128, 64)), 128)
        ico = b"\x00\x00\x01\x00" + struct.pack("<H", 2) + bytes([16, 16]) + b"\x00" * 14 + bytes([0, 0]) + b"\x00" * 14
        self.assertEqual(ex.image_size(ico), 256)  # 0 表示 256
        self.assertEqual(ex.image_size(b'<?xml?><svg viewBox="0 0 1 1"/>'), 1024)
        self.assertEqual(ex.image_size(b"not an image"), 0)

    def test_fill_only_empty_items_and_records_errors_separately(self):
        profile = {"identity": {}, "announcements": {"items": [{"title": "已有"}]}, "qa": {"items": []}, "_meta": {"errors": {}}}
        def boom(code):
            raise OSError("timeout")
        changed, problems = ex.fill("600016.SH", profile, fetchers={
            "announcements": lambda code: {"items": [{"title": "新"}], "count_12m": 1}, "qa": boom}, logo=False)
        self.assertTrue(changed)
        self.assertEqual(profile["announcements"]["items"][0]["title"], "已有")
        self.assertIn("timeout", profile["_extras"]["qa"]["error"])
        self.assertEqual(profile["_meta"]["errors"], {})  # 不阻塞转正
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
