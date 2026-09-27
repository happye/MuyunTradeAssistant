"""K0a 投影水位与「股数是事实，建议是意图」回归测试（plan/fusion iteration4 K0a，D3/D4）

锁死语义（架构师深审 D3/D4——反例先红后绿）：
1. D3 投影幂等水位：applied_fills 与持仓投影值同一次 _save 原子落盘（「投影写后/
   水位前」成为不可出现状态）；投影后/账本前崩溃 → 重试只补账本、不重复变动持仓
2. D4 股数是事实、建议是意图：数量确认部分卖出 → 持仓记录保留、输出显示
   已成交/剩余、不显示清仓完成；建议只在事实达成时 CONFIRMED（否则待确认+偏离）；
   加仓成本派生自批次均价（不覆盖为最新买价）；真实成交日贯穿比例视图

隔离纪律：构造前断言所有持久化路径（portfolio/proposals/account ledger）在临时根。
跑法：pytest tests/core/test_k0a_projection_intent.py -q
"""
import io
import os
import sys
from unittest.mock import patch

import pytest
import yaml
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 类测试顶部先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.account_service as account_module
import src.data.horizon_plans as horizon_plans_module
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager
from src.data.proposals import Proposal


def _assert_temp_root(tmp_path, pm, ledger):
    """隔离断言：本测试所有持久化路径必须在临时根内（HOME 重定向不算隔离）。"""
    root = str(tmp_path.resolve())
    for p in (str(pm.portfolio_path), str(pm._proposals.path), str(ledger)):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"


# ── 1. D3：投影后/账本前崩溃 → 重试不重复变动 ─────────────

def test_d3_crash_between_projection_and_ledger_no_double_apply(tmp_path):
    """D3 主反例：portfolio 已保存、proposals.record_fill 前崩溃 → 重试同 fill_id
    比例不再叠加（旧实现 0.2→0.3、duplicate=False）；账本被幂等修复补记。"""
    folder = tmp_path / "d3"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", entry_price=10, ratio=0.1)
    with patch.object(pm._proposals, "record_fill",
                      side_effect=RuntimeError("模拟投影后崩溃")):
        with pytest.raises(RuntimeError):
            pm.confirm_fill("600519", "BUY", 0.1, price=10, fill_id="same_fill")
    pm2 = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    _assert_temp_root(tmp_path, pm2, folder / "proposals.json")
    before = pm2.get_position("600519").current_ratio
    receipt = pm2.confirm_fill("600519", "BUY", 0.1, price=10, fill_id="same_fill")
    after = pm2.get_position("600519").current_ratio
    assert after == before == pytest.approx(0.2), \
        f"重试不得重复变动持仓（旧实现 {before}→{after}）"
    assert receipt.ok and receipt.duplicate
    assert pm2._proposals.has_fill("same_fill"), "重试应修复成交账本（簿记是可重建派生结果）"


def test_d3_watermark_persisted_with_projection_same_file(tmp_path):
    """水位与投影值同文件持久化（K0a/D3：幂等标记不落第三份文件）。"""
    folder = tmp_path / "d3c"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", entry_price=10, ratio=0.1)
    assert pm.confirm_fill("600519", "BUY", 0.05, price=10, fill_id="f_w").ok
    data = yaml.safe_load((folder / "portfolio.yaml").read_text(encoding="utf-8"))
    assert (data.get("applied_fills") or {}).get("f_w"), "水位必须与投影值同文件持久化"


def test_d3_two_real_fills_same_price_both_applied(tmp_path):
    """两笔同价真实成交（逐笔部分成交）：各自 fill_id 独立入账互不吞并。"""
    folder = tmp_path / "d3d"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", entry_price=10, ratio=0.1)
    assert pm.confirm_fill("600519", "BUY", 0.05, price=10, fill_id="p#1").ok
    assert pm.confirm_fill("600519", "BUY", 0.05, price=10, fill_id="p#2").ok
    assert pm.get_position("600519").current_ratio == pytest.approx(0.2)
    assert pm._proposals.has_fill("p#1") and pm._proposals.has_fill("p#2")


# ── 2. D4：数量事实 vs 建议意图（真实 CLI 入口）────────────

def _setup_d4(tmp_path, action="CLOSE_ALL", target_ratio=0.0):
    """D4 场景：比例视图 600519 仓位 10% + 待确认建议；数量账本期初 1000 股@10、现金 10000。"""
    folder = tmp_path / "d4"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    proposal = pm._proposals.add_proposal(Proposal(
        stock_code="600519", stock_name="测试股", position_action=action,
        target_ratio=target_ratio, current_ratio=0.1))
    pm._proposals._save()
    ledger = folder / "events.jsonl"
    svc = AccountService(ledger)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20",
                       lots=[{"security_id": "600519", "quantity": 1000,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    return pm, proposal, svc, ledger


def _run_cli_confirm(pm, ledger, tmp_path, *, crash_projection=False, **kw):
    output = io.StringIO()
    patches = [
        patch.object(cli_main, "PortfolioManager", return_value=pm),
        patch.object(cli_main, "console", Console(file=output, width=200)),
        patch.object(account_module, "DEFAULT_LEDGER_PATH", ledger),
        patch.object(horizon_plans_module, "PLANS_FILE", tmp_path / "plans.json"),
    ]
    if crash_projection:
        # 模拟「账本已写、投影未同步」窗口：apply_quantity_fill 入口前崩溃
        patches.append(patch.object(pm, "apply_quantity_fill",
                                    side_effect=RuntimeError("模拟投影前崩溃")))
    with patches[0], patches[1], patches[2], patches[3]:
        if crash_projection:
            with patches[4]:
                cli_main.manage_positions("confirm", **kw)
        else:
            cli_main.manage_positions("confirm", **kw)
    return output.getvalue()


def test_d4_partial_sell_keeps_record_and_shows_truth(tmp_path):
    """D4 主反例：实际卖 100/剩 900——记录必须保留、输出显示已成交/剩余，
    不显示清仓完成；建议不得被虚判 CONFIRMED；真实成交日贯穿比例视图。"""
    pm, proposal, svc, ledger = _setup_d4(tmp_path)
    _assert_temp_root(tmp_path, pm, ledger)
    out = _run_cli_confirm(pm, ledger, tmp_path, stock_code="600519", qty=100, price=10,
                           trade_date="2026-09-21")
    remaining = sum(h.quantity for h in svc.snapshot().holdings)
    assert remaining == 900, "账户事实：剩 900 股"
    view = pm.get_position("600519")
    assert view is not None, "部分卖出不得删掉持仓记录（旧实现虚判清仓）"
    assert "已成交 100" in out and "剩余 900" in out, f"输出必须如实显示股数事实:\n{out}"
    assert "清仓完成" not in out, "未清仓不得显示清仓完成"
    current = next(p for p in pm._proposals._proposals
                   if p.proposal_id == proposal.proposal_id)
    assert current.status.value in ("PROPOSED", "PARTIAL"), \
        f"事实未达成建议目标，不得 CONFIRMED: {current.status.value}"
    assert view.last_action_date == "2026-09-21", "真实成交日须贯穿到比例视图记录"
    assert view.ratio_stale, "NAV 未知时比例视图须标记待重估（不以旧比例冒充事实）"
    assert view.quantity_fact and view.quantity_fact.get("quantity") == 900


def test_d4_full_sell_completes_proposal_factually(tmp_path):
    """实际全卖（剩 0 股）= 事实清仓 → 清仓语义成立、建议 CONFIRMED、显示清仓完成。"""
    pm, proposal, svc, ledger = _setup_d4(tmp_path)
    _assert_temp_root(tmp_path, pm, ledger)
    out = _run_cli_confirm(pm, ledger, tmp_path, stock_code="600519", qty=1000, price=10,
                           trade_date="2026-09-21")
    assert sum(h.quantity for h in svc.snapshot().holdings) == 0
    assert "清仓完成" in out
    current = next(p for p in pm._proposals._proposals
                   if p.proposal_id == proposal.proposal_id)
    assert current.status.value == "CONFIRMED", "事实达成清仓 → 建议确认"


def test_d3_state3_legacy_ledger_without_watermark_backfills(tmp_path):
    """监督审查 P2-4：state-3（旧数据 fixture——账本有 fill、水位缺失，无水位时代
    的历史数据）→ 视为已应用（不重放持仓），回填水位并幂等跳过；再次重试无副作用。"""
    folder = tmp_path / "d3s"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", entry_price=10, ratio=0.1)
    assert pm.confirm_fill("600519", "BUY", 0.1, price=10, fill_id="legacy_f").ok
    # 构造旧格式：抹掉水位标记（账本保留 fill 记录）
    pm2 = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm2._data.pop("applied_fills", None)
    assert pm2._save().ok
    pm3 = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    _assert_temp_root(tmp_path, pm3, folder / "proposals.json")
    assert pm3._proposals.has_fill("legacy_f") and not pm3._has_applied_fill("legacy_f")
    r2 = pm3.confirm_fill("600519", "BUY", 0.1, price=10, fill_id="legacy_f")
    assert r2.ok and r2.duplicate, "state-3：视为已应用，不重放"
    assert pm3.get_position("600519").current_ratio == pytest.approx(0.2), "持仓不得重复变动"
    # 回填水位持久化（重启后幂等判定仍成立）
    pm4 = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    assert pm4._has_applied_fill("legacy_f"), "水位已回填"
    r3 = pm4.confirm_fill("600519", "BUY", 0.1, price=10, fill_id="legacy_f")
    assert r3.ok and r3.duplicate
    assert pm4.get_position("600519").current_ratio == pytest.approx(0.2)


def test_d4_buy_add_cost_from_batches_not_latest_price(tmp_path):
    """加仓成本派生自批次均价（300@10 + 100@15 → 11.25），不得用最新买价覆盖历史成本。"""
    folder = tmp_path / "d4b"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm._proposals.add_proposal(Proposal(stock_code="600519", stock_name="测试股",
                                        position_action="ADD", target_ratio=0.15,
                                        current_ratio=0.1))
    pm._proposals._save()
    ledger = folder / "events.jsonl"
    svc = AccountService(ledger)
    svc.opening_import(opening_cash=100000.0, trade_date="2026-09-01",
                       lots=[{"security_id": "600519", "quantity": 300, "cost_price": 10.0,
                              "acquired_at": "2026-09-01"}])
    _assert_temp_root(tmp_path, pm, ledger)
    out = _run_cli_confirm(pm, ledger, tmp_path, stock_code="600519", qty=100, price=15,
                           trade_date="2026-09-10")
    snap = svc.snapshot()
    holding = next(h for h in snap.holdings if h.security_id == "600519")
    assert holding.quantity == 400
    view = pm.get_position("600519")
    assert view is not None
    assert view.entry_price == pytest.approx(11.25), \
        f"加仓后成本=批次均价（旧实现被最新买价 15 覆盖）: {view.entry_price}"
    assert view.last_action_date == "2026-09-10"
    assert "已成交 100" in out and "400" in out, f"输出须显示股数事实:\n{out}"


def test_d4_p1_review_close_all_majority_partial_sell_crash_recover(tmp_path):
    """监督审查 P1-1 回归：CLOSE_ALL 部分卖（卖过半，如 150 持仓卖 100，deviation=True）
    在「账本已写、投影未同步」窗口崩溃后重跑——恢复判定不得依赖 deviation 位重算
    （崩溃后 held_before 变化 → deviation 重算翻转 → 指纹失配 → 永不恢复）。
    修复合同：pick_reusable 按核心指纹（去 deviation）识别恢复态 + 恢复时从已入账
    事件回读 payload（完整指纹一致 → DUPLICATE → 投影补做）。"""
    folder = tmp_path / "d4c"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm._proposals.add_proposal(Proposal(stock_code="600519", stock_name="测试股",
                                        position_action="CLOSE_ALL", target_ratio=0,
                                        current_ratio=0.1))
    pm._proposals._save()
    ledger = folder / "events.jsonl"
    svc = AccountService(ledger)
    svc.opening_import(opening_cash=100000.0, trade_date="2026-09-20",
                       lots=[{"security_id": "600519", "quantity": 150,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    _assert_temp_root(tmp_path, pm, ledger)
    # 第一次确认：账本入账（卖 100/剩 50），随后注入崩溃（投影未同步、水位缺失）
    with pytest.raises(RuntimeError):
        _run_cli_confirm(pm, ledger, tmp_path, stock_code="600519", qty=100, price=10,
                         trade_date="2026-09-21", crash_projection=True)
    assert sum(h.quantity for h in svc.snapshot().holdings) == 50, "第一笔已入账（事实）"
    # 重启（新实例）重跑同一命令：必须幂等恢复投影——不新增事件、不超卖拒绝
    pm2 = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    out2 = _run_cli_confirm(pm2, ledger, tmp_path, stock_code="600519", qty=100, price=10,
                            trade_date="2026-09-21")
    assert sum(h.quantity for h in svc.snapshot().holdings) == 50, "重跑不得重复入账"
    sells = [e for e in svc.log.events() if e.event_type.value == "SELL"]
    assert len(sells) == 1, f"恢复不得新增事件: {len(sells)} 笔卖出"
    assert "未入账" not in out2, f"恢复不得报未入账误导用户: {out2}"
    view = pm2.get_position("600519")
    assert view is not None and view.quantity_fact and view.quantity_fact.get("quantity") == 50, \
        "投影恢复：已成交100/剩余50 如实同步"
