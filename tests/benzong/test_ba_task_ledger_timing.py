"""ba 批量评分的任务账本时序锁（C2 监督员批 4 核对 P1；T05-H 结构化结果改造）。

65f81c6 曾把 batch_task_start 放在 auto_score_batch 之后——_progress 回调里的
逐项 mark 对未登记任务是 no-op，中断场景账本连任务都没有（修复未生效却声称
已修，文档冒充修复形态）。T05-H 起记账只由结构化逐项结果回调驱动（progress
只展示），本测试锁死时序：首条结构化结果回调触发时，本 run 必须已登记。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.cli.session_state as ss
from src.cli import main as cli_main


def _fake_batch(code="600519"):
    """构造只回调结构化结果、不返回 ranked 的伪批量评分（防回填救活断言）。"""
    def fake_auto_score(codes, top_n, force_refresh, progress_cb=None, config=None,
                        result_cb=None):
        # 模拟评分中途：第一只结果已返回即回调（此时账本里必须已有本 run 令牌）。
        # 返回空 ranked/failures——运行后无料可补，done 里该股的唯一来源就是回调 mark；
        # 若时序回退（start 挪回 run 后）或 progress 文案参与记账，本测试必红。
        if result_cb:
            result_cb({"code": code, "ok": True, "status": "complete"})
        captured["cb_seen"] = True
        return {"ranked": [], "failures": [], "top_n": [], "scanned": len(codes),
                "scored": 0, "failed": 0}
    return fake_auto_score


captured = {}


def test_ba_ledger_registered_before_first_result_cb(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")
    captured.clear()
    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", _fake_batch())

    cli_main.benzong_batch_analyze([{"code": "600519", "name": "茅台"}],
                                   src_label="bz scan 测试")

    assert captured.get("cb_seen")
    t = next(t for t in ss.get_recent_batch_tasks() if t["key"] == "bz scan 测试")
    assert isinstance(t.get("run_id"), str) and t["run_id"]
    assert "600519" in t["done"], \
        "结构化结果回调触发时本 run 必须已登记且令牌可写（start 在 auto_score_batch 之前）"


def test_ba_progress_text_does_not_write_ledger(monkeypatch, tmp_path):
    """progress 文案（含“失败”）不得决定记账：只展示，不写 done/failed。"""
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")
    captured.clear()

    def fake_auto_score(codes, top_n, force_refresh, progress_cb=None, config=None,
                        result_cb=None):
        # 故意只触发展示回调、不返回结构化结果：账本必须保持“未跑”
        progress_cb(1, len(codes), "600519", "失败")
        return {"ranked": [{"code": "600519", "name": "茅台", "total_score": 50,
                            "normalized_score": 50, "grade": "B", "effective_grade": "B",
                            "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
                            "warnings": [], "invalidate": False}],
                "top_n": [], "failures": [], "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_auto_score)
    cli_main.benzong_batch_analyze([{"code": "600519", "name": "茅台"}],
                                   src_label="bz scan 测试")

    t = next(t for t in ss.get_recent_batch_tasks() if t["key"] == "bz scan 测试")
    assert t["done"] == {} and t["failed"] == {} and t.get("degraded", {}) == {}, \
        "progress 文案不是成功/失败依据，结束也不得按 kind/key 盲补"
