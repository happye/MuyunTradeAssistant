"""live 装配统一工厂（ADR-02，plan/ARCHITECTURE §三）。

此前 6 处手写 Orchestrator 装配（main×4 / chat / scanner）契约散落，历史上
漏传过 entry_exit_config / rag_service（chat H2、ISS-032 同族事故）。本工厂
是 live 装配的单一入口：统一传 skills/weights/types/ai/event/entry_exit。

RAG 生命周期由入口持有者负责——工厂只接 rag_service 参数，绝不偷偷加载
（RAG 单例对齐纪律：一次会话一个实例，见 chat/tools.py init_engines 注释）。
回测装配独立（backtest_engine 自建，is_backtest=True 纪律），不经过本工厂。
"""
from __future__ import annotations


def build_live_orchestrator(config: dict, *, rag_service=None):
    """从完整 config 装配 live Orchestrator。

    Args:
        config: load_config() 产出的完整配置 dict
        rag_service: 调用方持有的 RAG 实例（可为 None——纯客观命令不受影响）

    Returns:
        Orchestrator（装配参数契约与历史 6 处手写装配逐键一致）
    """
    from src.core.orchestrator import Orchestrator

    skills = config.get("skills") or {}
    decision = config.get("decision") or {}
    return Orchestrator(
        skills.get("dir", "./src/skills"),
        skills.get("enabled", None),
        decision.get("signal_weights", None),
        skills.get("types", None),
        ai_config=config.get("ai", None),
        event_config=config.get("event", None),
        entry_exit_config=config.get("entry_exit", None),
        rag_service=rag_service,
    )
