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
    assert session_state.save_last_scan(items, "scan market 健康回调") is True
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

def _make_legacy_report(tmp_path, date_str="2026-09-10_10-00"):
    """用 persist_scan_report 本尊生成旧格式报告（保证格式保真），再回填历史文件名。"""
    monkey_items = [{"code": "600519", "name": "贵州茅台", "price": 1257.12,
                     "change_pct": 1.2, "turnover_rate": 0.8},
                    {"code": "000001", "name": "平安银行", "price": 11.7,
                     "change_pct": -0.5, "turnover_rate": 0.6}]
    old_report_dir = session_state._REPORT_DIR
    session_state._REPORT_DIR = tmp_path / "scan"
    try:
        path = session_state.persist_scan_report(
            monkey_items, "scan market 健康回调",
            columns=[("price", "价"), ("change_pct", "涨跌%"), ("turnover_rate", "换手%")])
    finally:
        session_state._REPORT_DIR = old_report_dir
    assert path
    target = tmp_path / "scan" / f"{date_str}_scan_market_健康回调.md"
    Path(path).rename(target)
    return target


def test_import_legacy_reports(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    _make_legacy_report(tmp_path)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", tmp_path / "scan")

    from src.cli.main import scan_review_import
    scan_review_import()
    rows = session_state.get_scan_history()
    assert len(rows) == 1
    rec = rows[0]
    assert rec["timestamp"] == "2026-09-10T10:00:00"   # 文件名时间戳回填
    assert rec["source"] == "scan market 健康回调"       # 标题行原始来源
    assert rec["count"] == 2
    by_code = {it["code"]: it for it in rec["items"]}
    assert by_code["600519"]["price"] == 1257.12        # 明细段字段数值化
    assert by_code["000001"]["change_pct"] == -0.5


def test_import_is_idempotent(monkeypatch, tmp_path, capsys):
    _redirect_state(monkeypatch, tmp_path)
    _make_legacy_report(tmp_path)
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", tmp_path / "scan")
    from src.cli.main import scan_review_import
    scan_review_import()
    scan_review_import()   # 第二次：同(时间,来源)自动跳过
    out = capsys.readouterr().out
    assert "导入 0 条" in out or "已存在跳过 1 条" in out
    assert len(session_state.get_scan_history()) == 1


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
