"""ba 批量评分的任务账本时序锁（C2，监督员批 4 核对 P1）。

65f81c6 曾把 batch_task_start 放在 auto_score_batch 之后——_progress 回调里的
逐项 mark 对未登记任务是 no-op，中断场景账本连任务都没有（修复未生效却声称
已修，文档冒充修复形态）。本测试锁死时序：回调首次触发时任务必须已登记。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.cli.session_state as ss
from src.cli import main as cli_main


def test_ba_ledger_registered_before_first_progress_cb(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")

    def fake_auto_score(codes, top_n, force_refresh, progress_cb):
        # 模拟评分中途：第一只评完即回调（此时账本里必须已有任务）
        progress_cb(1, len(codes), "600519", "50/B")
        captured["cb_seen"] = True
        return {
            "ranked": [{"code": "600519", "name": "茅台", "effective_grade": "B",
                        "confidence": 0.7, "invalidate": False,
                        "dim_scores": {"s": 50}, "dim_confidences": {"s": 1},
                        "normalized_score": 50}],
            "failures": [],
        }

    captured = {}
    # benzong_batch_analyze 内是函数级 from-import——patch 源模块属性才会生效
    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_auto_score)

    cli_main.benzong_batch_analyze([{"code": "600519", "name": "茅台"}],
                                   src_label="bz scan 测试")

    assert captured.get("cb_seen")
    t = next(t for t in ss.get_recent_batch_tasks() if t["key"] == "bz scan 测试")
    assert "600519" in t["done"], \
        "progress_cb 触发时任务必须已登记——start 在 auto_score_batch 之前（时序锁）"
