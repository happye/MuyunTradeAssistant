#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fetch_wechat_article.py 离线冒烟测试 (不联网, 用模拟微信页面)

覆盖:
  1. 元数据提取 (标题 / 公众号 / 发布时间 / 摘要)
  2. 正文区块截取 (div 平衡, 不漏页脚)
  3. HTML -> Markdown (标题/加粗/图片/嵌套列表/引用/表格/HTML 实体)
  4. 失效页识别 (已删除 / 违规 / 参数错误)
  5. 文件名安全化

跑法:
    .venv\\Scripts\\python.exe scripts/test_fetch_wechat.py

回归触发点: 改 _MDParser 的标签处理或 finalize 的清理正则后必须重跑。
历史坑: finalize 曾用 r"\\*\\s*\\*" 清空标记, 会把 **正文** 的 ** 一起删掉。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_wechat_article as fw  # noqa: E402

SAMPLE = """
<html><head><title>测试文章 - 微信</title>
<meta property="og:description" content="这是一段摘要说明文字" />
</head><body>
<script>var msg_title = '下半年A股策略：哑铃配置';</script>
<script>var nickname = '渤海证券研究';var ct = "1750000000";</script>
<div class="rich_media_area_primary">
  <h1 class="rich_media_title" id="activity-name">下半年A股策略：哑铃配置</h1>
  <div class="rich_media_content" id="js_content">
    <section><h2>一、核心观点</h2></section>
    <p>宏观方面，<strong>外需韧性较强</strong>但稳内需压力抬升。参考<a href="http://x.com">链接</a>。</p>
    <p><img data-src="https://mmbiz.qpic.cn/mmbiz_png/abc123/640?wx_fmt=png" alt="图表1" /></p>
    <ul><li>算力板块机会</li><li>高股息防御
      <ul><li>银行</li><li>电力</li></ul>
    </li></ul>
    <blockquote>风险提示：政策不及预期</blockquote>
    <table><tr><td>板块</td><td>评级</td></tr><tr><td>半导体</td><td>增持</td></tr></table>
    <p style="x">结尾段落 &amp; 特殊字符 &lt;tag&gt;</p>
  </div>
  <div class="rich_media_tool">页脚不该被抓进来 FOOTER_MARKER</div>
</div>
<div class="qr_code">二维码区域</div>
</body></html>
"""

DEAD = "<html><body><div class='weui-msg'>该内容已被发布者删除</div></body></html>"


def main() -> int:
    ok = True

    # 1 元数据
    meta = fw.extract_metadata(SAMPLE)
    print(f"[meta] {meta}")
    ok &= meta["title"] == "下半年A股策略：哑铃配置"
    ok &= meta["account"] == "渤海证券研究"
    ok &= bool(meta["publish_time"])

    # 2 正文区块
    frag = fw.extract_content_html(SAMPLE)
    has_body = "核心观点" in frag and "结尾段落" in frag
    leak = "FOOTER_MARKER" in frag
    print(f"[frag] len={len(frag)} body={has_body} footer_leak={leak}")
    ok &= has_body and not leak

    # 3 Markdown
    md = fw.html_to_markdown(frag)
    checks = {
        "h2": "## 一、核心观点" in md,
        "strong": "**外需韧性较强**" in md,
        "img": "![" in md and "mmbiz.qpic.cn" in md,
        "list": "- 算力板块机会" in md,
        "nested_list": "  - 银行" in md,
        "quote": "风险提示" in md,
        "table_row": "| 板块 | 评级 |" in md,
        "table_sep": "| --- | --- |" in md,
        "table_data": "| 半导体 | 增持 |" in md,
        "entity": "<tag>" in md and "&" in md,
    }
    for k, v in checks.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
        ok &= v

    # 4 失效页
    ok &= fw.check_dead(DEAD) == "文章已被作者删除"
    ok &= fw.check_dead(SAMPLE) is None
    print(f"[dead] {fw.check_dead(DEAD)} / normal={fw.check_dead(SAMPLE)}")

    # 5 文件名
    fn = fw.safe_filename("下半年A股策略：哑铃配置/斜杠*星号", "20260729")
    print(f"[fname] {fn}")
    ok &= all(c not in fn for c in '\\/:*?"<>|')

    print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
