"""L2 公开检查点端到端回归测试（plan/fusion iteration5，DELIVERY_PLAN L2；R11 V4 反例）

锁死语义（检查点从公开入口可建立、可复核——全部实走 parse_input→run_cli）：
1. V4 正例：research --claims --checkpoint --confirm-risk --ref <证据ID> → 检查点命题
   window_and_refutation 可建立（TRUE）——证据引用从公开入口绑定（不再固定 []）
2. 稳定证据ID：主张文件 claim_id 尊重/缺省内容确定性生成——同命令重放不换引用；
   未给 --ref 时列可选证据ID与缺口（不自动绑定任何主张）
3. 伪引用/错主体拒绝；缺原件（未达已核验）给明确下一步；未确认风险不建立
4. 全链：research → plan2 accept --primary → 隔离 l（行情数据替身=数据隔离）→
   影子/报告；命题真值、计划状态、报告分母**单独断言**（不用 observation_kind
   掩盖 UNESTABLISHED）；重启后持久化恢复；变更新候选未接受（双槽）

隔离纪律：portfolio/proposals/ledger/plans/RESEARCH_DIR/shadow 全部重定向并断言在
临时根内；零 AI（研究链本地规则核验）。跑法：pytest tests/core/test_l2_public_checkpoint.py -q
"""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.horizon_plans as horizon_plans_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import src.data.research_store as research_store_module
import start as start_module
from src.core.shadow_diff import build_shadow_report
from src.data.account_service import AccountService
from src.data.horizon_plans import HorizonPlanStore
from src.data.models import StockData
from src.data.research_store import AssessmentStore

CODE = "600519"
PUB = "2026-09-01T08:00:00+00:00"
BODY = "公司签订900万元订单。"
QUOTE = "2026-09-30"


def _claims_file(tmp_path, *, with_body=True, extra=None, name="claims.json"):
    """K2a 主张文件（完整原件资料正例；with_body=False 模拟缺原件）。"""
    items = [{"statement": BODY, "event_type": "order", "value": 900, "unit": "万元",
              "quote_text": BODY, "source_uri": "probe://order", "published_at": PUB}]
    if with_body:
        items[0]["body"] = BODY
    if extra:
        items.extend(extra)
    p = tmp_path / name
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _isolate(tmp_path, monkeypatch):
    """持久化路径全量重定向（先于一切构造）+ 健全性断言。"""
    research_dir = tmp_path / "research"
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    monkeypatch.setattr(research_store_module, "RESEARCH_DIR", research_dir)
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")
    root = str(tmp_path.resolve())
    for p in (str(research_dir), str(tmp_path / "horizon_plans.json")):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"
    # 账户账本种子（l 影子捕获要读账户版本）
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0, trade_date="2026-09-20",
        lots=[{"security_id": CODE, "quantity": 1000, "cost_price": 10.0,
               "acquired_at": "2026-09-20"}])
    pm = __import__("src.data.portfolio", fromlist=["PortfolioManager"]).PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)


def _repl(cmd: str) -> str:
    """真实 REPL 全链：parse_input → run_cli（console 重定向捕获渲染；stdout 原样返回）。"""
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, f"REPL 解析失败: {cmd}"
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def _window_value(assessment_id: str) -> str:
    """从评估唯一真值库读 window_and_refutation 命题评估值（单独断言用）。"""
    asm = AssessmentStore(research_store_module.RESEARCH_DIR).load(assessment_id)
    assert asm is not None, f"评估不可解析: {assessment_id}"
    w = next(a for a in asm.required_assertions
             if a.proposition_type == "window_and_refutation")
    return w.evaluation.value


# ── V4 正例：全链 research --ref → accept --primary → l → 影子/报告 ──

def test_v4_full_chain_research_accept_shadow(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    # 第 1 跑：未给引用 → 列可选证据（JSON 暴露稳定 ID），命题 UNKNOWN（不自动绑定）
    out1 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    r1 = json.loads(out1[out1.index("{"):])
    assert r1.get("verified_claim_ids"), "结果必须暴露稳定证据ID（--ref 可绑定）"
    cid = r1["verified_claim_ids"][0]
    assert _window_value(r1["assessment_ids"]["MID"]) == "UNKNOWN", \
        "未给引用时检查点命题必须 UNKNOWN（不得自动绑定任何主张）"
    # 第 2 跑：--ref 绑定 → 命题可建立；重放不换引用
    out2 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --ref {cid} --json")
    r2 = json.loads(out2[out2.index("{"):])
    assert r2["verified_claim_ids"] == r1["verified_claim_ids"], \
        "同输入重放不得更换证据ID（稳定引用）"
    assert _window_value(r2["assessment_ids"]["MID"]) == "TRUE", \
        "公开入口 --ref 绑定已核验证据+已确认风险 → 检查点命题必须可建立（V4 正例）"
    # plan2 accept --primary（公开入口激活）
    _repl(f"plan2 accept {CODE} mid --primary")
    store = HorizonPlanStore()
    plan = store.get(CODE, "MID")
    assert plan is not None and plan.activated, "计划必须已激活"
    assert store.is_accepted_version(plan), "精确接受版本引用必须成立"
    # 隔离 l（行情数据替身=数据隔离；研究/接受/捕获全真）→ 影子/报告
    # （l 命令函数内 from src.data.akshare_client import get_stock_data——patch 源模块）
    stub = StockData(stock_code=CODE, stock_name="测试股", price=10.0, volume=100000,
                     change_pct=1.0, avg_volume_20=100000.0,
                     ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of=QUOTE)
    with patch.object(akshare_module, "get_stock_data", lambda code: stub):
        _repl(f"l {CODE}")
    report = build_shadow_report(store_path=shadow_module.SHADOW_STORE_PATH, days=7)
    # 单独断言：命题真值 / 计划状态 / 报告分母——三者独立核对
    assert _window_value(r2["assessment_ids"]["MID"]) == "TRUE"
    assert store.get(CODE, "MID").activated
    assert report["v7_mid_effective"] == 1, \
        f"已接受计划+可解析评估+账户版本+行情时点齐 → 影子有效分母 1: {report['v7_drop_counts']}"


# ── 缺引用/伪引用/错主体/缺原件/未确认风险 ─────────────────────────

def test_v4_missing_ref_lists_available_evidence(tmp_path, monkeypatch):
    """未给 --ref：列出可选证据ID与缺口（人话），命题保持 UNKNOWN。"""
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    out = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 --confirm-risk")
    assert "证据ID" in out or "证据 ID" in out, f"必须列出可选证据ID: {out[-600:]}"
    assert "--ref" in out, "必须给出下一步（--ref 用法）"


def test_v4_unknown_ref_rejected(tmp_path, monkeypatch):
    """伪引用（不存在的 ID）：拒绝并列出可用 ID，不产生带坏引用的评估。"""
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    out = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                f"--confirm-risk --ref bogus_id --json")
    assert "bogus_id" in out and ("不在本批主张" in out or "不可用" in out), \
        f"伪引用必须被拒绝并说明: {out[-600:]}"


def test_v4_wrong_subject_ref_rejected(tmp_path, monkeypatch):
    """错主体引用（别家公告）：拒绝（K2a 反向门同精神——公开入口同样不放过）。
    错主体主张的确定性 ID 按加载器合同（主体|主张|来源URI hash）直接计算。"""
    _isolate(tmp_path, monkeypatch)
    import hashlib as _h
    stmt = "别家公司签订大额订单。"
    other_id = "claim_" + _h.sha256(
        f"000002|{stmt}|probe://other".encode("utf-8")).hexdigest()[:12]
    claims = _claims_file(tmp_path, extra=[
        {"statement": stmt, "event_type": "order",
         "quote_text": stmt, "source_uri": "probe://other",
         "body": stmt, "published_at": PUB,
         "security_id": "000002"}], name="claims_mixed.json")
    out = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                f"--confirm-risk --ref {other_id} --json")
    assert ("主体" in out and ("拒绝" in out or "不符" in out)), \
        f"错主体引用必须被拒绝: {out[-600:]}"


def test_v4_unverified_ref_gives_next_step(tmp_path, monkeypatch):
    """缺原件（无 body → 未达已核验）：--ref 通过入口校验（ID 在批内）但评估按
    UNKNOWN 如实降级，并给明确下一步（补原文重跑）——不冒充已核验（守卫 P2-2：
    精确命中「批内未核验」分支，不用 or 掩盖）。"""
    _isolate(tmp_path, monkeypatch)
    import hashlib as _h
    claims = _claims_file(tmp_path, with_body=False, name="claims_nobody.json")
    out1 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    r1 = json.loads(out1[out1.index("{"):])
    assert r1.get("verified_claim_ids") == [], "缺原件不得冒充已核验"
    assert r1.get("unverified_claims", 0) >= 1
    # 批内未核验主张的确定性 ID（loader 合同：主体|主张|来源URI hash）
    cid = "claim_" + _h.sha256(
        f"{CODE}|{BODY}|probe://order".encode("utf-8")).hexdigest()[:12]
    # 提示在渲染路径（--json 不含引导文案）——非 json 跑一次抓 console
    out2 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --ref {cid}")
    assert "未达已核验" in out2, \
        f"批内未核验引用必须给明确下一步（补原文）: {out2[-600:]}"


def test_v4_ref_without_claims_rejected(tmp_path, monkeypatch):
    """守卫 P2-1：--ref 无 --claims → 入口显式拒绝（跑后诊断不再误导）。"""
    _isolate(tmp_path, monkeypatch)
    out = _repl(f"research {CODE} --checkpoint 到期复核 --confirm-risk "
                f"--ref claim_x --json")
    assert "--claims" in out and "拒绝" in out or "需配合" in out, \
        f"无主张时 --ref 必须被入口拒绝: {out[-600:]}"


def test_v4_unconfirmed_risk_stays_unknown(tmp_path, monkeypatch):
    """给了 --ref 但缺 --confirm-risk：风险确认不可替代——命题保持 UNKNOWN。"""
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    out1 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    cid = json.loads(out1[out1.index("{"):])["verified_claim_ids"][0]
    out2 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--ref {cid} --json")
    r2 = json.loads(out2[out2.index("{"):])
    assert _window_value(r2["assessment_ids"]["MID"]) == "UNKNOWN", \
        "未显式确认风险意愿 → 检查点命题不得建立（接受计划不自动替代风险确认）"


# ── 重启恢复与双槽（变更新候选未接受）────────────────────────────

def test_v4_restart_persists_accepted_state(tmp_path, monkeypatch):
    """重启（全新存储实例读盘）：已激活计划、评估引用、命题真值全部恢复。"""
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    out1 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    cid = json.loads(out1[out1.index("{"):])["verified_claim_ids"][0]
    out2 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --ref {cid} --json")
    aid = json.loads(out2[out2.index("{"):])["assessment_ids"]["MID"]
    _repl(f"plan2 accept {CODE} mid --primary")
    # 重启：模块级 monkeypatch 不变（等价新实例）——直接新构造存储读取
    store = HorizonPlanStore()
    plan = store.get(CODE, "MID")
    assert plan.activated and store.is_accepted_version(plan)
    assert _window_value(aid) == "TRUE", "重启后评估唯一真值必须可恢复"


def test_v4_new_material_goes_candidate_not_accepted(tmp_path, monkeypatch):
    """接受后研究出新材料 → 入候选槽，已接受版本保持 active（不被覆盖）。"""
    _isolate(tmp_path, monkeypatch)
    claims = _claims_file(tmp_path)
    out1 = _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    cid = json.loads(out1[out1.index("{"):])["verified_claim_ids"][0]
    _repl(f"research {CODE} --claims {claims} --checkpoint 到期复核 "
          f"--confirm-risk --ref {cid} --json")
    _repl(f"plan2 accept {CODE} mid --primary")
    store_before = HorizonPlanStore()
    accepted_before = store_before.get(CODE, "MID")
    # 新材料：追加一条不同主张 → 研究产生新草稿
    claims2 = _claims_file(tmp_path, extra=[
        {"statement": "公司中标新的电网项目。", "event_type": "order",
         "quote_text": "公司中标新的电网项目。", "source_uri": "probe://grid",
         "body": "公司中标新的电网项目。", "published_at": PUB}],
        name="claims_v2.json")
    out3 = _repl(f"research {CODE} --claims {claims2} --checkpoint 到期复核 "
                 f"--confirm-risk --json")
    r3 = json.loads(out3[out3.index("{"):])
    assert r3["draft_kinds"].get("MID") in ("candidate", "updated"), \
        f"新材料草稿入候选/更新槽: {r3.get('draft_kinds')}"
    store_after = HorizonPlanStore()
    plan_after = store_after.get(CODE, "MID")
    assert plan_after.activated and plan_after.content_hash() == accepted_before.content_hash(), \
        "已接受版本不得被新材料研究覆盖（双槽语义）"
