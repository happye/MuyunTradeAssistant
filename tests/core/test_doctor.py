"""doctor 运行诊断命令测试（v0.8.16，plan/ M6）。

锁死语义：
1. 只读零 AI：不写任何文件、不发网络请求（全 mock 路径下跑通即证）
2. 分节输出齐全：解释器/依赖/配置/状态目录/RAG
3. 不泄密：settings.local.yaml 的存在可报告，但文件内容（API key）绝不回显
4. 缺失项如实标注"未生成/缺失"，不报错不崩溃
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.cli.main as cli_main


def test_doctor_outputs_all_sections(capsys):
    """doctor 输出必须覆盖：解释器/依赖/配置/状态目录 四个基本节。"""
    cli_main.doctor()
    out = capsys.readouterr().out
    for kw in ("解释器", "依赖", "配置", "状态目录"):
        assert kw in out, f"doctor 输出缺「{kw}」节"
    assert sys.executable[:3] in out or "python" in out.lower()  # 解释器路径可见


def test_doctor_never_echoes_secret_file_content(monkeypatch, tmp_path, capsys):
    """settings.local.yaml 只报存在性，内容（API key）绝不回显（M6 验收：诊断不泄密）。"""
    secret_file = tmp_path / "settings.local.yaml"
    secret_file.write_text(
        "ai:\n  api_key: 'sk-ULTRA-SECRET-KEY-12345'\n", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_DOCTOR_SETTINGS_LOCAL", secret_file)
    cli_main.doctor()
    out = capsys.readouterr().out
    assert "sk-ULTRA-SECRET-KEY-12345" not in out, "密钥内容绝不许出现在诊断输出"
    assert "sk-" not in out
    # 存在性要如实报告
    assert "已配置" in out or "存在" in out


def test_doctor_missing_state_dir_is_tolerated(monkeypatch, tmp_path, capsys):
    """~/.muyun 不存在（全新环境）→ 各项标"未生成"，不崩不告警刷屏。"""
    monkeypatch.setenv("HOME", str(tmp_path / "no_such_home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "no_such_home"))
    cli_main.doctor()   # 不抛即过
    out = capsys.readouterr().out
    assert "未生成" in out or "不存在" in out or "缺失" in out


def test_doctor_missing_dependency(monkeypatch, capsys):
    """核心依赖缺失 → 显式标"缺失"，不影响其余节输出。"""
    import importlib.util
    real = importlib.util.find_spec

    def fake_spec(name, *a, **k):
        if name == "faiss":
            return None
        return real(name, *a, **k)
    monkeypatch.setattr(importlib.util, "find_spec", fake_spec)
    cli_main.doctor()
    out = capsys.readouterr().out
    assert "faiss" in out and ("缺失" in out or "未安装" in out)


def test_doctor_wired_through_run_cli(monkeypatch, capsys):
    """接线冒烟锁（M6 审查 P0）：parse_input("doctor") 经 start.run_cli 必须真跑通。

    回归锁：run_cli 的函数级导入清单若漏 import doctor，命令运行时 NameError
    （直接调 cli_main.doctor 的单测抓不到接线断裂）。
    """
    import start

    mode, args = start.parse_input("doctor")
    assert (mode, args) == ("doctor", {})
    start.run_cli(mode, args)   # 不抛即过（内部函数级 import 断裂会 NameError）
    out = capsys.readouterr().out
    assert "运行诊断" in out


def test_doctor_c3_evidence_and_ai_config(monkeypatch, tmp_path, capsys):
    """C3：doctor 显示分析证据健康项（条数/末条距今）与 AI 配置（provider/model，
    api_key 只报布尔不回显）。"""
    _redirect = tmp_path / "home"
    (_redirect / ".muyun").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(_redirect))
    monkeypatch.setenv("USERPROFILE", str(_redirect))
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    ev_file = _redirect / ".muyun" / "analysis_evidence.jsonl"
    ev_file.write_text(_json.dumps({
        "ts": (_dt.now() - _td(hours=5)).isoformat(timespec="seconds"),
        "code": "600519", "decision": "HOLD",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    cli_main.doctor()
    out = capsys.readouterr().out
    assert "analysis_evidence" in out and "末条距今" in out
    assert "batch_tasks" in out
    assert "provider=" in out
    assert "体检耗时" in out   # C3 分阶段耗时（总耗时透明）
