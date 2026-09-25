"""C6 评分证据增强测试（v0.8.17）：行业景气维的数据来源摘要行。

锁死：_bz_industry_sources_line 对 dimensions_meta 的提取与防御（缺字段/
异常 → 空串不影响输出），以及"覆盖不足时用户能看见"的语义（空 sources
不输出假来源，缺数据时行缺席本身就是信号）。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.cli.main import _bz_industry_sources_line


def _fake_score(meta):
    return SimpleNamespace(dimensions_meta=meta)


def test_sources_line_lists_objective_and_news():
    score = _fake_score({"industry_prosperity": {"sources": [
        "行业: 稀土", "商品锚(氧化镝)", "需求端: 新能源车", "新闻 12 条"]}})
    line = _bz_industry_sources_line(score)
    assert "行业景气来源" in line
    assert "商品锚(氧化镝)" in line
    assert "新闻 12 条" in line


def test_sources_line_caps_at_four_sources():
    score = _fake_score({"industry_prosperity": {"sources": [f"s{i}" for i in range(8)]}})
    line = _bz_industry_sources_line(score)
    assert "s3" in line and "s4" not in line   # 只显示前 4 项


def test_sources_line_absent_when_no_data():
    """无来源（客观数据与新闻全缺）→ 不输出假来源行（行缺席=覆盖不足的信号）。"""
    assert _bz_industry_sources_line(_fake_score({"industry_prosperity": {"sources": []}})) == ""
    assert _bz_industry_sources_line(_fake_score({})) == ""
    assert _bz_industry_sources_line(None) == ""
    assert _bz_industry_sources_line(SimpleNamespace()) == ""   # 无 dimensions_meta 属性
