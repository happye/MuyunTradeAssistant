"""E2b/E3b/E4b 实验骨架（plan/fusion iteration2 R7，VALIDATION §4）。

三个实验共享同一资格门：输入信息集不含 STRICT 级历史资格 → BLOCKED_DATA 如实拒绝
（R1 暂停点：latest-only 财务不入严格历史快照——严格收益结论保持阻断）。
本骨架交付：manifest v2 预注册 + 资格门 + 单一变量臂定义——真实执行待严格历史
数据（原始档案路径或前瞻当时捕获积累）解锁。

跑法：PYTHONUTF8=1 python tests/backtest/e2b_e3b_e4b_skeletons.py
预期输出：三实验全部 BLOCKED_DATA（离线无严格历史输入——这就是诚实结果，不是失败）。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.experiment import (
    ExperimentManifestV2,
    check_strict_eligibility_or_block,
    derive_temporal_eligibility,
)

ART = Path(__file__).resolve().parents[1] / "artifacts"

# 单一变量臂定义（VALIDATION §4：E2b/E3b/E4b 分别只改研究资格/持有纪律/技术择时；
# 固定其余评分/择时/预算——臂清单预注册，不可见结果后加臂）
ARMS = {
    "E2b": [
        {"name": "无新增资格门", "single_variation": "无"},
        {"name": "MID 资格门", "single_variation": "MID 命题资格（research.py 模板）"},
        {"name": "LONG 资格门", "single_variation": "LONG 命题资格（分周期分别报告）"},
    ],
    "E3b": [
        {"name": "legacy 纪律", "single_variation": "旧持有策略"},
        {"name": "fusion_mid", "single_variation": "中期决策表纪律（fusion_mid_v1）"},
        {"name": "fusion_long", "single_variation": "长期决策表纪律（fusion_long_v1）——不含不符合 LONG 资格的全体股票"},
    ],
    "E4b": [
        {"name": "无技术择时", "single_variation": "预设可交易时点入场（未触发保留现金入分母）"},
        {"name": "技术触发", "single_variation": "技术条件入场/加仓（relative_return_v2 日期对齐口径）"},
    ],
}


def _manifest(eid: str) -> ExperimentManifestV2:
    m = ExperimentManifestV2(
        experiment_id=eid,
        arms_and_single_variation=ARMS[eid],
        selection_source="共同候选集合（同 cutoff——严格历史快照未建库）",
        mode_source="rule_bz_proxy",
        thesis_source="system_draft（R3 research_service）",
        extraction_source="deterministic",
        primary_metric={"E2b": "覆盖率与拒绝原因分布", "E3b": "收益/回撤/换手/滞留时长",
                        "E4b": "等待成本与机会捕捉"}[eid],
        guardrail_metrics=["T+1 违规=0", "现金守恒", "未触发样本保留"],
        stop_rule="预算耗尽或资格失败即停，保存中间产物",
    )
    return m


def main() -> dict:
    report = {"experiments": {}, "note": "骨架交付：预注册 manifest + 单一变量臂 + 资格门——"
                                          "真实执行待严格历史输入（BLOCKED_DATA=诚实结果，不是失败）"}
    for eid in ("E2b", "E3b", "E4b"):
        m = _manifest(eid)
        # 当前全库无 STRICT 级历史输入（latest-only 财务 + 无历史停牌/成员数据）
        input_eligs = ["NON_STRICT", "BLOCKED_DATA"]
        try:
            tag = check_strict_eligibility_or_block(m, input_eligs)
            report["experiments"][eid] = {"eligibility": tag, "arms": ARMS[eid],
                                          "status": "would_run（不应出现）"}
        except RuntimeError as e:
            report["experiments"][eid] = {"eligibility": "BLOCKED_DATA",
                                          "blocked_reason": str(e),
                                          "arms_preregistered": ARMS[eid],
                                          "status": "blocked（如实）"}
        assert m.temporal_eligibility == "BLOCKED_DATA"
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "e2b_e3b_e4b_registration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    main()
