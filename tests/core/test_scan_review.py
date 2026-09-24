"""scan review 扫描复盘（v0.8.11）解析、历史层与执行路径测试

- parse_input: "scan review"/"scan review 14"/"scan 复盘" 分流 + 不破坏 scan market / 裸 scan
- session_state: append/get 往返、>90天 prune、损坏行跳过、save_last_scan 同时追加历史（收口行为锁死）
- scan_review: mock 历史/批量行情/K线兜底/指数，已知输入断言精确涨跌与统计；
  缺行情剔除、今日记录排除、行情全挂早退、报告落盘、指数当日缓存零请求

零网络、零 AI、不触碰真实 ~/.muyun（session_state 与 main 的状态文件常量重定向到 tmp_path）。
跑法：pytest tests/core/test_scan_review.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import start
from src.cli import session_state
from src.cli import main as cli_main


# ── parse_input 分流 ─────────────────────────────────────

def test_parse_scan_review_default():
    assert start.parse_input("scan review") == ("scan_review", {"days": 7})


def test_parse_scan_review_days():
    assert start.parse_input("scan review 14") == ("scan_review", {"days": 14})


def test_parse_scan_review_clamp():
    assert start.parse_input("scan review 0") == ("scan_review", {"days": 1})
    assert start.parse_input("scan review 500") == ("scan_review", {"days": 90})


def test_parse_scan_review_invalid_days_falls_back(capsys):
    assert start.parse_input("scan review abc") == ("scan_review", {"days": 7})
    assert "不是有效天数" in capsys.readouterr().out


def test_parse_scan_review_chinese_alias():
    assert start.parse_input("scan 复盘") == ("scan_review", {"days": 7})


def test_parse_scan_market_and_bare_scan_unaffected():
    assert start.parse_input("scan market 健康回调")[0] == "scan_market"
    assert start.parse_input("scan") == ("scan", {})
    assert start.parse_input("s") == ("scan", {})


# ── session_state 历史层 ─────────────────────────────────

def _redirect_state(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")


def test_append_and_get_roundtrip(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    items = [{"code": "600519", "name": "贵州茅台", "price": 100.0}]
    assert session_state.append_scan_history(items, "bz scan 测试") is True
    rows = session_state.get_scan_history()
    assert len(rows) == 1
    assert rows[0]["source"] == "bz scan 测试"
    assert rows[0]["items"][0]["code"] == "600519"
    assert rows[0]["count"] == 1


def test_get_scan_history_days_filter(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    now = datetime.now()
    rows = [
        {"timestamp": (now - timedelta(days=1)).isoformat(timespec="seconds"),
         "source": "近", "count": 1, "items": []},
        {"timestamp": (now - timedelta(days=30)).isoformat(timespec="seconds"),
         "source": "远", "count": 1, "items": []},
    ]
    tmp_path / "scan_history.jsonl"
    (tmp_path / "scan_history.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    recent = session_state.get_scan_history(days=7)
    assert [r["source"] for r in recent] == ["近"]  # 倒序 + 过滤
    assert len(session_state.get_scan_history()) == 2


def test_get_scan_history_skips_corrupt_lines(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    good = {"timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": "好", "count": 1, "items": []}
    (tmp_path / "scan_history.jsonl").write_text(
        "not-a-json-line\n" + json.dumps(good, ensure_ascii=False) + "\n", encoding="utf-8")
    rows = session_state.get_scan_history()
    assert len(rows) == 1 and rows[0]["source"] == "好"


def test_append_keeps_rows_beyond_90_days(monkeypatch, tmp_path):
    """v0.8.12.1 起 append 不再 prune（对抗审查 P0-1：prune 会把导入的旧记录静默删掉）。

    存储全量保留；展示窗口由查询侧（get_scan_history 的 days / scan review 的 clamp）控制。
    """
    _redirect_state(monkeypatch, tmp_path)
    now = datetime.now()
    old = {"timestamp": (now - timedelta(days=140)).isoformat(timespec="seconds"),
           "source": "陈年导入", "count": 1, "items": []}
    keep = {"timestamp": (now - timedelta(days=10)).isoformat(timespec="seconds"),
            "source": "保留", "count": 1, "items": []}
    (tmp_path / "scan_history.jsonl").write_text(
        json.dumps(old, ensure_ascii=False) + "\n" + json.dumps(keep, ensure_ascii=False) + "\n",
        encoding="utf-8")
    session_state.append_scan_history([{"code": "600519"}], "新扫描")
    rows = session_state.get_scan_history()
    sources = [r["source"] for r in rows]
    assert "陈年导入" in sources and "保留" in sources and "新扫描" in sources


def test_import_old_records_survive_subsequent_appends(monkeypatch, tmp_path):
    """P0-1 回归锁死：导入 >90 天的旧报告后，后续扫描追加不得删掉它们。"""
    _redirect_state(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", tmp_path / "报告")
    old_dt = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d_%H-%M")
    report_dir = tmp_path / "报告"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{old_dt}_bz_scan_陈年主题.md").write_text(
        "# 扫描结果 - bz scan 陈年主题\n\n> 时间：占位\n> 共 1 只\n\n"
        "## 明细\n\n### 1. 600519 贵州茅台\n- price: 100.0\n\n",
        encoding="utf-8")
    from src.cli.main import scan_review_import
    scan_review_import()
    # 之后再正常扫描追加两次
    session_state.append_scan_history([{"code": "000001"}], "scan market 新")
    session_state.append_scan_history([{"code": "300750"}], "bz scan 新")
    rows = session_state.get_scan_history()
    sources = [r["source"] for r in rows]
    assert "bz scan 陈年主题" in sources, "导入的 >90 天记录被后续 append 静默删除（P0-1 复发）"
    assert len(rows) == 3


def test_save_last_scan_also_appends_history(monkeypatch, tmp_path):
    """收口行为锁死：save_last_scan 的所有调用方（scan market/bz scan/chat）自动进历史。"""
    _redirect_state(monkeypatch, tmp_path)
    items = [{"code": "000001", "name": "平安银行", "price": 10.0}]
    assert session_state.save_last_scan(items, "scan market 健康回调")  # M2 起返回 ScanSaveResult，真值=快照+历史都成
    assert (tmp_path / "last_scan.json").exists()
    assert (tmp_path / "scan_history.jsonl").exists()
    rows = session_state.get_scan_history()
    assert len(rows) == 1 and rows[0]["source"] == "scan market 健康回调"


# ── 指数基准当日缓存 ─────────────────────────────────────

def test_review_index_bars_uses_same_day_cache(monkeypatch, tmp_path):
    cache = tmp_path / "scan_review_index.json"
    cache.write_text(json.dumps({
        "fetched_date": datetime.now().strftime("%Y-%m-%d"),
        "start_date": "2026-01-01",
        "rows": [{"date": "2026-09-01", "close": 3000.0},
                 {"date": "2026-09-18", "close": 3060.0}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cli_main, "_REVIEW_INDEX_CACHE", cache)

    def _must_not_be_called(*a, **k):
        raise AssertionError("当日缓存命中时不应发网络请求")

    import src.core.fear_index.history as fear_hist
    monkeypatch.setattr(fear_hist, "_bs_index_kline", _must_not_be_called)
    rows = cli_main._review_index_bars("2026-02-01")
    assert len(rows) == 2 and rows[-1]["close"] == 3060.0


def test_review_index_bars_fetches_and_caches(monkeypatch, tmp_path):
    cache = tmp_path / "scan_review_index.json"
    monkeypatch.setattr(cli_main, "_REVIEW_INDEX_CACHE", cache)
    import src.core.fear_index.history as fear_hist
    monkeypatch.setattr(
        fear_hist, "_bs_index_kline",
        lambda code, fields, start, end: [["2026-09-18", "3060.0"], ["2026-09-19", "3070.0"]])
    rows = cli_main._review_index_bars("2026-09-15")
    assert rows[-1] == {"date": "2026-09-19", "close": 3070.0}
    assert cache.exists()  # 已写当日缓存


# ── scan_review 执行路径 ─────────────────────────────────

def _patch_net_and_state(monkeypatch, tmp_path, records, quotes, kline_df=None, bars=None):
    """统一 mock：历史记录 / 批量行情 / K线兜底 / 指数 / 报告目录，全部重定向 tmp_path。

    bars=None 时指数基准 mock 为空（绝不打真实网络）。
    get_scan_history mock 按 days 参数真实过滤（模拟窗口语义，供自动扩大路径测试）。
    """
    def _fake_history(days=None):
        if days is None:
            return list(records)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [r for r in records if (r.get("timestamp") or "")[:10] >= cutoff]

    monkeypatch.setattr(session_state, "get_scan_history", _fake_history)
    monkeypatch.setattr(session_state, "_WATCH_FILE", tmp_path / "watchlist.jsonl")  # 池列读取不触碰真实文件
    monkeypatch.setattr("src.data.akshare_client.AKShareClient.get_realtime_quotes",
                        lambda codes, retry=1: quotes)
    if kline_df is not None:
        def _fake_kline(code, period="daily", adjust="qfq", start_date=None,
                        end_date=None, retry=3):
            assert adjust is None  # 复盘口径必须不复权（与扫描快照价同口径）
            return kline_df
        monkeypatch.setattr("src.data.akshare_client.AKShareClient.get_historical_kline",
                            _fake_kline)
    else:
        # M1 网络审计修复：kline_df=None（缺行情场景）也必须隔离——scan review 的
        # 逐票日线预热会对全部记录真连 baostock 登录。返回最小非空 df 保住
        # kline_cnt 计数与成本行文案的真实路径行为
        import pandas as _pd

        def _fake_kline_offline(code, period="daily", adjust="qfq", start_date=None,
                                end_date=None, retry=3):
            assert adjust is None  # 复盘口径必须不复权（与扫描快照价同口径）
            return _pd.DataFrame({"日期": ["2020-01-02"], "收盘": [99.0]})
        monkeypatch.setattr("src.data.akshare_client.AKShareClient.get_historical_kline",
                            _fake_kline_offline)
    import src.core.fear_index.history as fear_hist
    monkeypatch.setattr(fear_hist, "_bs_index_kline",
                        (lambda code, fields, start, end: bars) if bars is not None
                        else (lambda code, fields, start, end: []))
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", tmp_path / "报告")
    monkeypatch.setattr(cli_main, "_REVIEW_INDEX_CACHE", tmp_path / "idx.json")


def test_scan_review_known_numbers(monkeypatch, tmp_path, capsys):
    """已知输入 → 精确输出：快照价直用、K线兜底、涨跌/超额/统计。"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{yesterday}T14:30:00", "source": "bz scan 测试", "count": 2,
        "items": [
            {"code": "600519", "name": "贵州茅台", "price": 100.0},   # 快照价直用
            {"code": "000001", "name": "平安银行"},                    # 无价 → K线兜底
        ],
    }]
    quotes = {"600519": {"price": 110.0}, "000001": {"price": 10.0}}
    kline_df = pd.DataFrame({"日期": [yesterday], "收盘": ["5.0"]})
    # 基准：扫描日收盘 3000 → 今日收盘 3060 = +2.0%（相对日期，任何一天跑都成立）
    bars = [[yesterday, "3000.0"], [today, "3060.0"]]
    _patch_net_and_state(monkeypatch, tmp_path, records, quotes, kline_df, bars)

    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "+10.00" in out       # 茅台 (110-100)/100
    assert "+100.00" in out      # 平安 (10-5)/5（K线兜底）
    assert "+8.00" in out        # 茅台超额 10-2
    assert "+98.00" in out       # 平安超额 100-2
    assert "2涨0跌" in out or "2 涨 0 跌" in out
    assert "+55.00" in out       # 平均 (10+100)/2
    assert "跑赢基准 2/2" in out
    assert "逐票日线 2 只" in out.replace("\n", "")  # 全部票预热日线（走势列），当日缓存
    assert "▁" in out or "▂" in out or "▃" in out or "▄" in out or "▅" in out \
        or "▆" in out or "▇" in out or "—" in out  # 走势列迷你曲线存在
    # 报告落盘且内容含关键数字
    reports = list((tmp_path / "报告").glob("review_*.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")
    assert "600519" in body and "+10.00" in body


def test_scan_review_excludes_today_records(monkeypatch, tmp_path, capsys):
    today = datetime.now().strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{today}T10:00:00", "source": "今日扫描", "count": 1,
        "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}],
    }]
    _patch_net_and_state(monkeypatch, tmp_path, records,
                         {"600519": {"price": 105.0}}, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "待满 1 个交易日" in out
    assert "还没有可复盘的扫描历史" in out  # 今日记录不算可评估


def test_scan_review_missing_quote_excluded(monkeypatch, tmp_path, capsys):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{yesterday}T10:00:00", "source": "scan market 测试", "count": 2,
        "items": [
            {"code": "600519", "name": "贵州茅台", "price": 100.0},
            {"code": "000001", "name": "平安银行", "price": 10.0},  # 无行情
        ],
    }]
    quotes = {"600519": {"price": 110.0}}  # 000001 拿不到行情（停牌/退市）
    _patch_net_and_state(monkeypatch, tmp_path, records, quotes, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "无行情" in out
    assert "1涨0跌" in out or "1 涨 0 跌" in out
    assert "+10.00" in out       # 茅台 (110-100)/100
    assert "1 只次无行情未计入" in out


def test_scan_review_aborts_when_all_quotes_fail(monkeypatch, tmp_path, capsys):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{yesterday}T10:00:00", "source": "bz scan 测试", "count": 1,
        "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}],
    }]
    _patch_net_and_state(monkeypatch, tmp_path, records, {}, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "行情获取失败" in out


def test_scan_review_empty_history(monkeypatch, tmp_path, capsys):
    _patch_net_and_state(monkeypatch, tmp_path, [], {}, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "还没有可复盘的扫描历史" in out
    assert "v0.8.11 开始累积" in out


def test_scan_review_bench_missing_degrades(monkeypatch, tmp_path, capsys):
    """指数拉不到 → 基准列降级为 -，个股涨跌照常统计（不阻断）。"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{yesterday}T10:00:00", "source": "bz scan 测试", "count": 1,
        "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}],
    }]
    quotes = {"600519": {"price": 110.0}}

    def _boom(*a, **k):
        raise RuntimeError("baostock 挂了")

    _patch_net_and_state(monkeypatch, tmp_path, records, quotes, None, None)
    import src.core.fear_index.history as fear_hist
    monkeypatch.setattr(fear_hist, "_bs_index_kline", _boom)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "+10.00" in out        # 涨跌照常
    assert "同期沪深300 -" in out  # 基准降级为 -
    assert "跑赢基准 -" in out


# ── 走势迷你曲线与逐日路径 ───────────────────────────────

def test_sparkline_known_values():
    assert cli_main._sparkline([1.0, 2.0, 3.0, 4.0]) == "▁▃▅▇"
    assert cli_main._sparkline([5.0]) == "—"          # 少于2点
    assert cli_main._sparkline([]) == "—"
    assert cli_main._sparkline([3.0, 3.0, 3.0]) == "▄▄▄"  # 全平
    # 单调升：首低尾高，末字符应为最高档
    sp = cli_main._sparkline([1.0, 2.0, 3.0])
    assert sp[0] == "▁" and sp[-1] == "▇"


def test_review_pure_functions_are_core_compat_aliases():
    """M4：main 的 _sparkline/_offset_curve/_review_path 等是 src.core.review 的兼容导出。

    身份锁：防止未来有人把实现复制回 main 造成双源漂移；测试对 cli_main 别名的
    monkeypatch 也能继续影响 scan_review/watch_pool 内部调用。
    """
    from src.core import review as core_review
    assert cli_main._sparkline is core_review.sparkline
    assert cli_main._offset_curve is core_review.offset_curve
    assert cli_main._curve_spark is core_review.curve_spark
    assert cli_main._review_path is core_review.review_path


def test_review_path_anchors_and_realtime_tail(monkeypatch):
    """路径 = 锚点(扫描价) + 其后逐bar收盘 + 实时价收尾（末bar非今日时）。"""
    df = pd.DataFrame({"日期": ["2026-09-20", "2026-09-21", "2026-09-22"],
                       "收盘": ["10.0", "10.5", "10.8"]})
    cache = {"600519": df}
    # 扫描日 09-19：锚点 9.9（快照价），其后 3 根 bar，末 bar=今日 → 不接实时价
    pts = cli_main._review_path("600519", "2026-09-19", 9.9, 11.0, cache, "2026-09-22")
    assert [lbl for lbl, _ in pts] == ["扫描日", "2026-09-20", "2026-09-21", "2026-09-22"]
    assert pts[0][1] == 9.9 and pts[-1][1] == 10.8
    # 末 bar 昨日 → 实时价接尾
    pts2 = cli_main._review_path("600519", "2026-09-19", 9.9, 11.0, cache, "2026-09-23")
    assert pts2[-1] == ("现价", 11.0)
    # 无 K线（停牌）→ 空路径
    assert cli_main._review_path("000001", "2026-09-19", 5.0, None, cache, "2026-09-22") == []


# ── 旧扫描报告导入（scan review import）──────────────────

# 固定旧格式文本 fixture（M3 任务卡要求：不得用生成器伪装旧文件——生成器一变
# "兼容测试"就虚假通过）。忠实复刻 v0.8.7~v0.8.10 persist_scan_report 实际产出格式
# （真实样本：分析报告/scan/2026-07-22_02-01_bz_scan_oversold_watch.md）。
_LEGACY_REPORT_V1 = """# 扫描结果 - scan market 健康回调

> 时间：2026-09-10 10:00:00
> 共 2 只

| # | 代码 | 名称 | 价 | 涨跌% | 换手% |
|---|------|------|------|------|------|
| 1 | 600519 | 贵州茅台 | 1257.12 | 1.20 | 0.80 |
| 2 | 000001 | 平安银行 | 11.70 | -0.50 | 0.60 |

## 明细

### 1. 600519 贵州茅台
- price: 1257.12
- change_pct: 1.2
- turnover_rate: 0.8

### 2. 000001 平安银行
- price: 11.7
- change_pct: -0.5
- turnover_rate: 0.6
"""


def _write_report(report_dir, filename: str, text: str):
    """写一份指定文件名的报告文本到 report_dir（不经过任何生成器）。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    p = report_dir / filename
    p.write_text(text, encoding="utf-8")
    return p


def test_import_legacy_reports(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    _write_report(report_dir, "2026-09-10_10-00_scan_market_健康回调.md", _LEGACY_REPORT_V1)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)

    from src.cli.main import scan_review_import
    scan_review_import()
    rows = session_state.get_scan_history()
    assert len(rows) == 1
    rec = rows[0]
    assert rec["timestamp"] == "2026-09-10T10:00:00"   # 文件名时间戳回填（旧格式分钟语义）
    assert rec["source"] == "scan market 健康回调"       # 标题行原始来源
    assert rec["count"] == 2
    by_code = {it["code"]: it for it in rec["items"]}
    assert by_code["600519"]["price"] == 1257.12        # 明细段字段数值化
    assert by_code["000001"]["change_pct"] == -0.5


def test_import_is_idempotent(monkeypatch, tmp_path, capsys):
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    _write_report(report_dir, "2026-09-10_10-00_scan_market_健康回调.md", _LEGACY_REPORT_V1)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    scan_review_import()
    scan_review_import()   # 第二次：同(时间,来源)自动跳过
    out = capsys.readouterr().out
    assert "导入 0 条" in out or "已存在跳过 1 条" in out
    assert len(session_state.get_scan_history()) == 1


def test_import_same_minute_multi_sources_idempotent(monkeypatch, tmp_path):
    """同分钟两个不同来源的旧报告：全部导入，且重复执行不重入（复合键集合，M3）。

    回归锁：原实现已知记录用 {分钟: 来源} dict 表示，同分钟多来源互相覆盖，
    第二次执行会把第一个来源再导入一遍（重复行）。
    """
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    text_a = _LEGACY_REPORT_V1.replace("scan market 健康回调", "bz scan 主题A")
    text_b = _LEGACY_REPORT_V1.replace("scan market 健康回调", "bz scan 主题B")
    _write_report(report_dir, "2026-09-10_10-00_bz_scan_主题A.md", text_a)
    _write_report(report_dir, "2026-09-10_10-00_bz_scan_主题B.md", text_b)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    scan_review_import()
    assert len(session_state.get_scan_history()) == 2   # 同分钟两来源都进历史
    scan_review_import()   # 第二次：复合键 (timestamp, source) 全命中，不重入
    assert len(session_state.get_scan_history()) == 2


def test_import_same_batch_duplicate_file_no_reentry(monkeypatch, tmp_path):
    """同批两个内容相同的文件（同时间同来源不同文件名）→ 只导入一条（M3）。

    回归锁：原实现本轮新导入的键没有加入已知集合，同批重复文件会重入。
    """
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    _write_report(report_dir, "2026-09-10_10-00_scan_market_健康回调.md", _LEGACY_REPORT_V1)
    _write_report(report_dir, "2026-09-10_10-00_scan_market_健康回调_copy.md", _LEGACY_REPORT_V1)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    scan_review_import()
    assert len(session_state.get_scan_history()) == 1


def test_import_append_failure_is_retryable(monkeypatch, tmp_path, capsys):
    """追加失败 → 计数可见且不入已知集合，下次执行可重试补录（M3）。"""
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    _write_report(report_dir, "2026-09-10_10-00_scan_market_健康回调.md", _LEGACY_REPORT_V1)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    real_append = session_state.append_scan_history
    monkeypatch.setattr(session_state, "append_scan_history", lambda *a, **k: False)
    scan_review_import()
    assert "写入失败 1 条" in capsys.readouterr().out
    assert len(session_state.get_scan_history()) == 0
    monkeypatch.setattr(session_state, "append_scan_history", real_append)
    scan_review_import()   # 恢复后重试：补录成功
    assert len(session_state.get_scan_history()) == 1


def test_import_minute_file_matches_live_seconds_history(monkeypatch, tmp_path):
    """v0.8.11~13 live 扫描（历史秒级 timestamp）+ 盘上分钟格式报告 → 导入不重复。

    回归锁（监督 Agent P1 实证）：旧代码分钟截断匹配恰好兜住这种「live 已入
    历史 + 分钟报告还在盘上」的重叠；M3 一度收窄成精确键导致 :00 ≠ :45 永不
    命中、重复导入。修复 = 旧格式文件按分钟粒度查重。
    """
    _redirect_state(monkeypatch, tmp_path)
    # live 扫描已自动入历史（秒级，save_last_scan 收口）
    session_state.append_scan_history(
        [{"code": "600519", "name": "贵州茅台", "price": 1257.12}],
        "scan market 健康回调", timestamp="2026-09-22T00:56:45")
    report_dir = tmp_path / "scan"
    text = _LEGACY_REPORT_V1.replace(
        "scan market 健康回调", "scan market 健康回调").replace(
        "> 时间：2026-09-10 10:00:00", "> 时间：2026-09-22 00:56:45")
    _write_report(report_dir, "2026-09-22_00-56_scan_market_健康回调.md", text)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    scan_review_import()
    rows = session_state.get_scan_history()
    assert len(rows) == 1, "分钟文件必须命中秒级 live 历史（分钟粒度查重），不得重复导入"
    assert rows[0]["timestamp"] == "2026-09-22T00:56:45"   # 保留 live 原秒级时间
    scan_review_import()
    assert len(session_state.get_scan_history()) == 1      # 持续幂等


def test_import_v2_report_precise_timestamp(monkeypatch, tmp_path):
    """v0.8.14+ 新格式（文件名秒级）：按正文精确时间导入，重复执行幂等（M3）。"""
    _redirect_state(monkeypatch, tmp_path)
    report_dir = tmp_path / "scan"
    v2_text = _LEGACY_REPORT_V1.replace(
        "scan market 健康回调", "bz scan AI").replace(
        "> 时间：2026-09-10 10:00:00", "> 时间：2026-09-24 10:00:30")
    _write_report(report_dir, "2026-09-24_10-00-30_bz_scan_AI.md", v2_text)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", report_dir)
    from src.cli.main import scan_review_import
    scan_review_import()
    rows = session_state.get_scan_history()
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-09-24T10:00:30"   # 正文精确时间（非分钟截断）
    scan_review_import()
    assert len(session_state.get_scan_history()) == 1      # 幂等


def test_persist_report_same_minute_no_overwrite(monkeypatch, tmp_path):
    """同分钟同主题连续扫描两次 → 两份报告都保留（M3：秒级文件名+独占创建）。

    回归锁：原文件名只有分钟精度且直接 write_text，第二份覆盖第一份。
    """
    from datetime import datetime as _real_dt

    class _FrozenDatetime(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return _real_dt(2026, 9, 24, 10, 0, 30)

    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    monkeypatch.setattr(session_state, "datetime", _FrozenDatetime)
    p1 = session_state.persist_scan_report([{"code": "A", "score": 1}], "bz scan 同主题")
    p2 = session_state.persist_scan_report([{"code": "A", "score": 2}], "bz scan 同主题")
    assert p1 and p2 and p1 != p2, "同分钟同主题两次扫描必须各得一份报告"
    assert "2026-09-24_10-00-30" in p1 and "2026-09-24_10-00-30" in p2  # 秒级文件名
    assert "score: 1" in Path(p1).read_text(encoding="utf-8")
    assert "score: 2" in Path(p2).read_text(encoding="utf-8")  # 第一份内容未被覆盖


def test_persist_report_accepts_history_timestamp(monkeypatch, tmp_path):
    """timestamp 参数（与 save_last_scan 历史记录一致传递）：文件名与正文都用它（M3）。"""
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    path = session_state.persist_scan_report(
        [{"code": "A", "score": 3}], "bz scan 链路",
        timestamp="2026-09-24T10:00:30")
    assert path and "2026-09-24_10-00-30" in path
    content = Path(path).read_text(encoding="utf-8")
    assert "> 时间：2026-09-24 10:00:30" in content
    assert "报告格式：v2" in content   # 人工辨识标记（导入侧实际按文件名秒级分流）


def test_save_last_scan_result_carries_timestamp(monkeypatch, tmp_path):
    """ScanSaveResult 携带历史记录的精确 timestamp——调用方透传给报告落盘实现同源（M3）。"""
    _redirect_state(monkeypatch, tmp_path)
    r = session_state.save_last_scan([{"code": "A"}], "test")
    assert r.timestamp
    data = session_state.get_last_scan()
    assert data["timestamp"] == r.timestamp   # 与快照/历史完全一致


def test_parse_scan_review_import():
    assert start.parse_input("scan review import") == ("scan_review_import", {})
    assert start.parse_input("scan 复盘 导入") == ("scan_review_import", {})
    assert start.parse_input("scan review 14") == ("scan_review", {"days": 14})  # 不受影响


def test_sparkline_downsamples_long_paths():
    """30 天窗口 30+ 点 → 降采样到 10 点（保端点），不再被列宽截断。"""
    vals = [100.0 + i for i in range(30)]
    sp = cli_main._sparkline(vals)
    assert len(sp) == 10
    assert sp[0] == "▁" and sp[-1] == "▇"   # 单调升：首尾端点保留


# ── 自动扩大窗口 + 落盘时间标注（用户实测反馈修复）─────────

def test_scan_review_auto_widens_when_window_empty(monkeypatch, tmp_path, capsys):
    """默认7天窗口外全是老记录 → 自动扩大到全部历史并明说（导入旧记录的核心场景）。"""
    old_dt = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{old_dt}T14:30:00", "source": "bz scan 旧扫描", "count": 1,
        "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}],
    }]
    _patch_net_and_state(monkeypatch, tmp_path, records,
                         {"600519": {"price": 110.0}}, None, None)
    cli_main.scan_review(days=7)   # 默认窗口：20天前的记录本被滤掉
    out = capsys.readouterr().out
    assert "已自动扩大到全部 1 条历史" in out.replace("\n", "")
    assert "bz scan 旧扫描" in out
    assert "+10.00" in out         # 记录真实可复盘


def test_scan_review_title_shows_persist_time_and_age(monkeypatch, tmp_path, capsys):
    old_dt = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    records = [{
        "timestamp": f"{old_dt}T14:30:00", "source": "scan market 测试", "count": 1,
        "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}],
    }]
    _patch_net_and_state(monkeypatch, tmp_path, records,
                         {"600519": {"price": 105.0}}, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out
    assert "落盘" in out and "距今 5 天" in out   # 落盘时间 + 被选中多久


def test_scan_review_truly_empty_message_mentions_import(monkeypatch, tmp_path, capsys):
    _patch_net_and_state(monkeypatch, tmp_path, [], {}, None, None)
    cli_main.scan_review(days=7)
    out = capsys.readouterr().out.replace("\n", "")
    assert "还没有可复盘的扫描历史" in out
    assert "scan review import" in out   # 空态提示引导导入（rich 换行容错）
