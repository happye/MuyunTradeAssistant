"""runtime 装配工厂契约测试（ADR-02，plan/ M 系列后批 1）。

锁死语义：
1. build_live_orchestrator 装配契约：config 各键逐项映射到 Orchestrator 参数
   （与历史 6 处手写装配逐键一致——漏传 entry_exit/rag 是 chat H2/ISS-032 同族事故）
2. RAG 由调用方持有：工厂透传 rag_service，不自行加载
3. load_config：路径基于项目根不依赖 cwd；local 覆盖深合并
4. 回测装配独立：backtest_engine 不经过 live 工厂（源码级守卫）
"""

import os
import sys
from types import SimpleNamespace

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core import runtime


def test_build_live_orchestrator_maps_all_config_keys(monkeypatch):
    """config 各键必须逐项映射到 Orchestrator 构造参数（漏传=H2/ISS-032 事故复发）。"""
    captured = {}

    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            self.get_available_skills = lambda: []

    monkeypatch.setattr("src.core.orchestrator.Orchestrator", _FakeOrchestrator)

    config = {
        "skills": {"dir": "/tmp/skills", "enabled": ["s1", "s2"], "types": {"s1": "base"}},
        "decision": {"signal_weights": {"w": 1}},
        "ai": {"provider": "deepseek"},
        "event": {"enabled": True},
        "entry_exit": {"chandelier": 3.0},
    }
    rag = object()
    o = runtime.build_live_orchestrator(config, rag_service=rag)

    assert isinstance(o, _FakeOrchestrator)
    assert captured["args"] == ("/tmp/skills", ["s1", "s2"], {"w": 1}, {"s1": "base"})
    assert captured["kwargs"]["ai_config"] == {"provider": "deepseek"}
    assert captured["kwargs"]["event_config"] == {"enabled": True}
    assert captured["kwargs"]["entry_exit_config"] == {"chandelier": 3.0}, \
        "entry_exit_config 漏传 = 买卖点整体失效（ISS-032）"
    assert captured["kwargs"]["rag_service"] is rag, "RAG 必须透传调用方实例"


def test_build_live_orchestrator_tolerates_missing_sections():
    """config 缺 skills/decision 等节 → 参数取默认 None，不 KeyError（空配置可装配）。"""
    captured = {}

    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            captured["args"], captured["kwargs"] = args, kwargs

    import src.core.orchestrator as orch_mod
    real = orch_mod.Orchestrator
    orch_mod.Orchestrator = _FakeOrchestrator
    try:
        runtime.build_live_orchestrator({}, rag_service=None)
    finally:
        orch_mod.Orchestrator = real
    assert captured["args"] == ("./src/skills", None, None, None)
    assert captured["kwargs"]["rag_service"] is None


def test_load_config_cwd_independent(monkeypatch, tmp_path):
    """load_config 路径基于项目根——chdir 到任意目录仍能加载（ADR-02 验收）。"""
    from src.config import load_config

    monkeypatch.chdir(tmp_path)   # 离开项目根
    cfg = load_config()
    assert isinstance(cfg, dict) and "skills" in cfg, "cwd=tmp 时必须仍加载到项目根配置"


def test_load_config_local_override_deep_merges(monkeypatch, tmp_path):
    """settings.local.yaml 深合并覆盖基础配置（ISS-042 语义保持）。"""
    import src.config as cfg_mod

    base = tmp_path / "settings.yaml"
    local = tmp_path / "settings.local.yaml"
    base.write_text(yaml.safe_dump(
        {"ai": {"provider": "deepseek", "model": "deepseek-flash"}, "skills": {"dir": "x"}}),
        encoding="utf-8")
    local.write_text(yaml.safe_dump({"ai": {"model": "kimi-k2.6"}}), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", base)
    monkeypatch.setattr(cfg_mod, "_LOCAL_PATH", local)

    cfg = cfg_mod.load_config()
    assert cfg["ai"]["model"] == "kimi-k2.6"          # local 覆盖
    assert cfg["ai"]["provider"] == "deepseek"        # 未覆盖键保留（深合并非替换）
    assert cfg["skills"]["dir"] == "x"


def test_load_config_custom_path():
    """显式传 config_path 时按传入路径加载（兼容出口签名保持）。"""
    from src.config import load_config
    cfg = load_config(os.path.join(os.path.dirname(__file__), "..", "..",
                                   "configs", "settings.yaml"))
    assert isinstance(cfg, dict)


def test_load_config_validates_root_type(monkeypatch, tmp_path):
    """ADR-02 配置校验：根非字典（YAML 损坏）→ ValueError 带文件路径，
    不让 config.get 在调用方深处抛 AttributeError。"""
    import pytest
    import src.config as cfg_mod

    bad = tmp_path / "settings.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")   # 根是列表
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", bad)
    with pytest.raises(ValueError, match="根必须是字典"):
        cfg_mod.load_config()

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", empty)
    with pytest.raises(ValueError, match="配置文件为空"):
        cfg_mod.load_config()


def test_load_config_local_override_non_dict_root_rejected(monkeypatch, tmp_path):
    """local 覆盖文件根非字典 → 明确报错（不给静默深合并失败留门）。"""
    import pytest
    import src.config as cfg_mod
    base = tmp_path / "settings.yaml"
    local = tmp_path / "settings.local.yaml"
    base.write_text("skills: {dir: x}\n", encoding="utf-8")
    local.write_text("- bad\n", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", base)
    monkeypatch.setattr(cfg_mod, "_LOCAL_PATH", local)
    with pytest.raises(ValueError, match="本地覆盖文件根必须是字典"):
        cfg_mod.load_config()


def test_backtest_assembly_does_not_use_live_factory():
    """回测装配独立于 live 工厂（ADR-02 验收：源码级守卫，防误收敛）。"""
    bt = os.path.join(os.path.dirname(__file__), "..", "..", "src", "core", "backtest_engine.py")
    with open(bt, encoding="utf-8") as f:
        src = f.read()
    assert "build_live_orchestrator" not in src, \
        "回测不得经过 live 装配工厂（is_backtest/关闭实时输入的纪律依赖独立装配）"
