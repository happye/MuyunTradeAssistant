#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微信公众号文章抓取 -> Markdown (纯标准库, 零第三方依赖)

定位:
    单篇/批量抓取"已发布且有链接"的微信公众号文章, 落成 .md 文件,
    供 RAG 知识库检索(src/rag/ingestion.py 已 rglob("*.md") 递归扫描)。

能力边界 (重要):
    能: 给定文章 URL (mp.weixin.qq.com/s/xxx 或 /s?__biz=...), 抓取标题/公众号/
        发布时间/正文/图片, 落成 Markdown。
    不能: 1) 按公众号名拉取"历史文章列表" (需登录态 + appmsg token, 反爬严格)
          2) 微信内"搜一搜"搜索文章 (需微信客户端登录态)
          3) 需要关注/付费/好友验证才能看的内容
    说明: 列表与搜索是"登录态接口", 不是普通网页, 本脚本不做, 也不建议做。

用法:
    # 单篇
    python scripts/fetch_wechat_article.py "https://mp.weixin.qq.com/s/xxxx"

    # 多篇 (空格分隔)
    python scripts/fetch_wechat_article.py URL1 URL2 URL3

    # 批量: 从文件读 (每行一个 URL, # 开头为注释)
    python scripts/fetch_wechat_article.py -f scripts/wechat_urls.txt

    # 指定输出目录 (默认: 投资策略（持续更新）/公众号/)
    python scripts/fetch_wechat_article.py URL -o docs/公众号

    # 顺带把图片下载到本地 assets/ (默认只保留远端图片链接)
    python scripts/fetch_wechat_article.py URL --images

    # 保存原始 HTML 便于排查
    python scripts/fetch_wechat_article.py URL --keep-html

    # 打印到 stdout 不落盘
    python scripts/fetch_wechat_article.py URL --stdout

退出码: 0 全部成功 / 1 部分或全部失败
"""

import argparse
import gzip
import html
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------- 常量

DEFAULT_OUT_DIR = "投资策略（持续更新）/公众号"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]

# 判死: 这些页面不是正文, 抓到了要立刻报清楚, 不静默吞掉
DEAD_SIGNALS = [
    ("该内容已被发布者删除", "文章已被作者删除"),
    ("此内容因违规无法查看", "文章因违规被下架"),
    ("该账号已被屏蔽", "公众号已被屏蔽"),
    ("内容已被发布者删除", "文章已被作者删除"),
    ("参数错误", "URL 无效或已失效 (参数错误)"),
    ("该内容发送失败无法查看", "URL 无效或已失效"),
]
# 需要过验证码: 换 UA 重试, 重试仍失败就报出来
BLOCK_SIGNALS = ["环境异常", "去验证", "完成验证", "weixin110"]

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


# ---------------------------------------------------------------- 抓取

def fetch_html(url: str, timeout: int = 25, retries: int = 2) -> str:
    """GET 微信文章页, 返回解码后的 HTML 文本。失败抛 RuntimeError。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    last_err = None
    for attempt in range(retries + 1):
        ua = random.choice(UA_POOL)
        req = urllib.request.Request(
            url,
            headers={
                # 用 identity 避免 gzip, 省掉一层解码坑
                "Accept-Encoding": "identity",
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://mp.weixin.qq.com/",
                "Connection": "keep-alive",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                enc = _detect_encoding(resp, raw)
                text = raw.decode(enc, errors="ignore")
                if not _is_blocked(text):
                    return text
                last_err = "触发微信风控 (环境异常/验证码), 建议放慢频率或换网络"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            last_err = f"网络错误: {e.reason}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

        if attempt < retries:
            time.sleep(1.5 + random.random() * 1.5)

    raise RuntimeError(last_err or "未知错误")


def _detect_encoding(resp, raw: bytes) -> str:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    m = re.search(r"charset=([\w\-]+)", ctype)
    if m:
        return m.group(1)
    head = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    return m.group(1) if m else "utf-8"


def _is_blocked(text: str) -> bool:
    return any(s in text for s in BLOCK_SIGNALS)


def check_dead(text: str) -> str | None:
    """若页面是失效页, 返回人话原因; 否则返回 None。"""
    for sig, reason in DEAD_SIGNALS:
        if sig in text:
            return reason
    return None


# ---------------------------------------------------------------- 提取

def _meta(text: str, *patterns: str) -> str:
    for p in patterns:
        m = re.search(p, text, re.S)
        if m:
            val = html.unescape(m.group(1)).strip()
            val = re.sub(r"\s+", " ", val)
            if val:
                return val
    return ""


def extract_metadata(text: str) -> dict:
    title = _meta(
        text,
        r'var msg_title\s*=\s*["\'](.+?)["\']',
        r'<meta property="og:title" content="(.*?)"',
        r'id="activity-name"[^>]*>(.*?)</h1>',
        r"<title>(.*?)</title>",
    )
    # 页面里的标题常带尾部空格/HTML 实体, 再剥一层标签
    title = re.sub(r"<[^>]+>", "", title).strip()

    account = _meta(
        text,
        r'var nickname\s*=\s*["\'](.+?)["\']',
        r'id="js_name"[^>]*>(.*?)</a>',
        r'<meta property="og:site_name" content="(.*?)"',
        r'var user_name\s*=\s*["\'](.+?)["\']',
    )
    account = re.sub(r"<[^>]+>", "", account).strip()

    # 发布时间: 优先页面时间戳, 其次可见的日期文本
    pub = ""
    m = re.search(r'var ct\s*=\s*"?(\d{10})"?', text) or re.search(
        r'var create_time\s*=\s*["\'](\d{10})["\']', text
    )
    if m:
        pub = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d %H:%M")
    if not pub:
        m = re.search(r'id="publish_time"[^>]*>(.*?)</em>', text, re.S)
        if m:
            pub = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not pub:
        m = re.search(
            r'var oriCreateTime\s*=\s*["\'](\d{4}-\d{2}-\d{2})["\']', text
        )
        if m:
            pub = m.group(1)

    desc = _meta(text, r'<meta property="og:description" content="(.*?)"')

    return {"title": title, "account": account, "publish_time": pub, "desc": desc}


def extract_content_html(text: str) -> str:
    """截取 id=js_content 的正文区块 (做 div 平衡, 避免截到页脚)。"""
    m = re.search(r'id=["\']js_content["\'][^>]*>', text)
    if not m:
        # 老版页面兜底
        m = re.search(r'class=["\']rich_media_content["\'][^>]*>', text)
        if not m:
            return ""
    start = m.end()
    depth, i, n = 1, start, len(text)
    tag_re = re.compile(r"<(/?)div\b[^>]*>", re.I)
    while i < n:
        mt = tag_re.search(text, i)
        if not mt:
            i = n
            break
        depth += -1 if mt.group(1) else 1
        i = mt.end()
        if depth == 0:
            break
    return text[start:i]


# ---------------------------------------------------------------- HTML -> Markdown

class _MDParser(HTMLParser):
    """精简版 HTML->Markdown。只处理公众号正文常见的标签集合。"""

    BLOCK = {"p", "div", "section", "br", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "tr", "blockquote", "pre", "hr"}
    SKIP = {"script", "style", "noscript", "svg", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0
        self._list_stack: list[str] = []
        self._in_pre = False
        self._link_href = ""
        self._pending_img: list[str] = []
        # 表格状态: {"row": 当前行号(1起), "cells": 当前行已输出单元格数}
        self._table = None

    # --- helpers
    def _nl(self):
        if self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def _blank(self):
        if self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")
        if self.out and not self.out[-1].endswith("\n\n"):
            self.out.append("\n")

    # --- handlers
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        a = dict(attrs)

        if tag == "img":
            src = a.get("data-src") or a.get("src") or ""
            alt = a.get("alt") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src:
                self._pending_img.append((src, alt))
            return

        if tag == "br":
            self.out.append("\n")
            return

        if tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag in ("del", "s"):
            self.out.append("~~")
        elif tag == "a":
            self._link_href = a.get("href", "")
        elif tag == "code":
            if not self._in_pre:
                self.out.append("`")
        elif tag == "pre":
            self._in_pre = True
            self._blank()
            self.out.append("```\n")
        elif tag == "blockquote":
            self._blank()
        elif tag == "hr":
            self._blank()
            self.out.append("---\n")
            self._blank()
        elif tag in ("ul", "ol"):
            self._blank()
            self._list_stack.append(tag)
        elif tag == "li":
            self._nl()
            indent = "  " * max(0, len(self._list_stack) - 1)
            bullet = "-" if (not self._list_stack or self._list_stack[-1] == "ul") else "1."
            self.out.append(f"{indent}{bullet} ")
        elif tag == "table":
            self._blank()
            self._table = {"row": 0, "cells": 0}
        elif tag == "tr":
            self._nl()
            self.out.append("| ")
            if self._table:
                self._table["row"] += 1
                self._table["cells"] = 0
        elif tag in ("td", "th"):
            # 单元格内容由 handle_data 直出, 收尾补 " | " 分隔
            pass
        elif re.fullmatch(r"h[1-6]", tag):
            self._blank()
            self.out.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag in ("del", "s"):
            self.out.append("~~")
        elif tag == "a":
            self._link_href = ""
        elif tag == "code":
            if not self._in_pre:
                self.out.append("`")
        elif tag == "pre":
            self.out.append("\n```")
            self._in_pre = False
            self._blank()
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._blank()
        elif tag == "tr":
            if self._table and self._table["row"] == 1:
                # 首行当表头, 补 markdown 分隔行, 否则表格渲染不出表头
                self.out.append("\n|" + " --- |" * self._table["cells"])
            self._nl()
        elif tag in ("td", "th"):
            self.out.append(" | ")
            if self._table:
                self._table["cells"] += 1
        elif tag == "table":
            self._table = None
            self._blank()
        elif tag in ("p", "div", "section", "blockquote"):
            self._blank()
        elif re.fullmatch(r"h[1-6]", tag):
            self._blank()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.SKIP:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_pre:
            self.out.append(data)
            return
        cleaned = data.replace("\u00a0", " ")
        if cleaned.strip():
            self.out.append(cleaned)

    # --- 收尾: 把图片挂到最近一个段落后面
    def finalize(self, images: list[str]) -> str:
        md = "".join(self.out)
        md = re.sub(r"[ \t]+\n", "\n", md)
        md = re.sub(r"\n{3,}", "\n\n", md)
        # 清理空强调标记。注意: 第二条要求星号之间有空白, 否则会把 **正文** 的 ** 误删
        md = re.sub(r"\*\*[ \t]*\*\*", "", md)
        md = re.sub(r"(?<!\*)\*[ \t]+\*(?!\*)", "", md)
        if images:
            md = md.rstrip() + "\n\n" + "\n\n".join(images) + "\n"
        return md.strip() + "\n"


def html_to_markdown(fragment: str, img_mapper=None) -> str:
    """img_mapper: callable(src) -> 本地相对路径 or None, 用于 --images"""
    parser = _MDParser()
    parser.feed(fragment)
    parser.close()

    images: list[str] = []
    for src, alt in parser._pending_img:
        if img_mapper:
            local = img_mapper(src)
            images.append(f"![{alt or 'img'}]({local})" if local else f"![{alt or 'img'}]({src})")
        else:
            images.append(f"![{alt or 'img'}]({src})")

    return parser.finalize(images)


# ---------------------------------------------------------------- 图片下载

def download_images(urls: list[str], assets_dir: Path) -> dict:
    assets_dir.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    mapping = {}
    for idx, u in enumerate(urls, 1):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": random.choice(UA_POOL),
                                                     "Referer": "https://mp.weixin.qq.com/"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                data = r.read()
            ext = Path(u.split("?")[0]).suffix.lower()
            if ext not in IMAGE_EXT:
                ext = ".jpg"
            name = f"img_{idx:03d}{ext}"
            (assets_dir / name).write_bytes(data)
            mapping[u] = f"assets/{name}"
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] 图片下载失败 {u[:60]}... ({e})", file=sys.stderr)
    return mapping


# ---------------------------------------------------------------- 落盘

def safe_filename(title: str, date_hint: str = "") -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title).strip()
    name = re.sub(r"\s+", "_", name)
    name = name[:60] or "untitled"
    prefix = date_hint + "_" if date_hint else ""
    return prefix + name + ".md"


def build_markdown(meta: dict, body_md: str, url: str) -> str:
    lines = [f"# {meta['title'] or '未命名文章'}", ""]
    lines.append("> 来源: 微信公众号" + (f"「{meta['account']}」" if meta["account"] else ""))
    if meta["publish_time"]:
        lines.append(f"> 发布时间: {meta['publish_time']}")
    lines.append(f"> 原文链接: {url}")
    lines.append(f"> 抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    if meta["desc"] and len(meta["desc"]) > 10:
        lines.append(f"**摘要**: {meta['desc']}")
        lines.append("")
    lines.append(body_md)
    return "\n".join(lines)


def process(url: str, out_dir: Path, *, keep_html=False, to_stdout=False,
            with_images=False) -> tuple[bool, str]:
    url = url.strip()
    if not url or url.startswith("#"):
        return False, "空行/注释, 跳过"
    if "mp.weixin.qq.com" not in url:
        return False, f"不是微信文章链接: {url}"

    print(f"[fetch] {url}")
    try:
        raw = fetch_html(url)
    except RuntimeError as e:
        return False, str(e)

    dead = check_dead(raw)
    if dead:
        return False, dead

    meta = extract_metadata(raw)
    frag = extract_content_html(raw)
    if not frag or len(frag) < 200:
        return False, "未提取到正文 (页面结构异常或需登录)"

    if keep_html:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "_raw" / (safe_filename(meta["title"])[:-3] + ".html")).parent.mkdir(
            parents=True, exist_ok=True)
        (out_dir / "_raw" / (safe_filename(meta["title"])[:-3] + ".html")).write_text(
            raw, encoding="utf-8")

    mapper = None
    if with_images:
        imgs = re.findall(r'(?:data-src|src)=["\'](//mmbiz[^"\']+|https?://mmbiz[^"\']+)["\']', frag)
        imgs = [("https:" + i if i.startswith("//") else i) for i in imgs]
        mapping = download_images(imgs, out_dir / "assets")
        mapper = lambda s: mapping.get(s)  # noqa: E731

    body_md = html_to_markdown(frag, img_mapper=mapper)
    md = build_markdown(meta, body_md, url)

    if to_stdout:
        print(md)
        return True, ""

    out_dir.mkdir(parents=True, exist_ok=True)
    date_hint = (meta["publish_time"] or "").split(" ")[0].replace("-", "")
    fname = safe_filename(meta["title"], date_hint)
    path = out_dir / fname
    path.write_text(md, encoding="utf-8")
    print(f"    -> {path}  ({len(md)} 字符)")
    return True, str(path)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="抓取微信公众号文章为 Markdown (需要文章 URL, 不支持历史列表/搜索)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("urls", nargs="*", help="文章 URL, 可多个")
    ap.add_argument("-f", "--file", help="URL 列表文件, 每行一个, # 开头为注释")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT_DIR, help=f"输出目录 (默认 {DEFAULT_OUT_DIR})")
    ap.add_argument("--images", action="store_true", help="下载图片到本地 assets/")
    ap.add_argument("--keep-html", action="store_true", help="同时保存原始 HTML 到 _raw/")
    ap.add_argument("--stdout", action="store_true", help="打印到屏幕不落盘")
    ap.add_argument("--delay", type=float, default=2.0, help="多篇之间的间隔秒数 (默认 2.0, 防风控)")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"[error] 列表文件不存在: {p}", file=sys.stderr)
            return 1
        urls += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    urls = [u for u in urls if u and not u.startswith("#")]
    if not urls:
        ap.print_help()
        return 1

    out_dir = Path(args.out)
    ok, fail = 0, []
    for i, u in enumerate(urls):
        success, detail = process(u, out_dir, keep_html=args.keep_html,
                                  to_stdout=args.stdout, with_images=args.images)
        if success:
            ok += 1
        else:
            fail.append((u, detail))
            print(f"    [FAIL] {detail}", file=sys.stderr)
        if i < len(urls) - 1 and args.delay > 0:
            time.sleep(args.delay + random.random())

    print(f"\n完成: 成功 {ok} / 共 {len(urls)}")
    if fail:
        print("失败明细:")
        for u, d in fail:
            print(f"  - {u}\n      {d}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
