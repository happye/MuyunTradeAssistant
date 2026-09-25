"""配置加载（ADR-02，plan/ARCHITECTURE §三）：settings.yaml + settings.local.yaml 深合并。

路径基于项目根推导（__file__），不依赖调用者 cwd——此前相对路径 "./configs/..."
要求 cwd=项目根（test_chat_command_bridge 不得不 os.chdir 兜底）。
自 main.load_config 迁入；main.load_config 保留兼容出口，迁移完调用方后清理。
"""
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PATH = _ROOT / "configs" / "settings.yaml"
_LOCAL_PATH = _ROOT / "configs" / "settings.local.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base（override 优先），返回新 dict 不改入参。"""
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(config_path: str = None) -> dict:
    """加载配置文件。

    若存在 configs/settings.local.yaml（gitignored），深合并覆盖到基础配置之上——
    用于存放 API key 等敏感项（ISS-042）。环境变量 DEEPSEEK_API_KEY / KIMI_API_KEY
    仍由各 AI 入口自行兜底。

    config_path 缺省用项目根下的 configs/settings.yaml（不依赖调用者 cwd）。

    配置校验（ADR-02：只检查必需字段，错误给出字段路径和修正方式；不因未配置
    可选 AI 而阻止纯客观命令——ai 节不校验）：
    - 根必须是字典（YAML 顶层列表/标量=配置文件损坏，给出文件路径）
    """
    path = Path(config_path) if config_path else _DEFAULT_PATH
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    if config is None:
        raise ValueError(f"配置文件为空: {path}（可对照 configs/settings.yaml.example 重建）")
    if not isinstance(config, dict):
        raise ValueError(f"配置文件根必须是字典（键值对）: {path}——"
                         f"当前类型 {type(config).__name__}，可能是文件损坏，"
                         f"请对照 configs/settings.yaml.example 检查")
    if _LOCAL_PATH.exists():
        with open(_LOCAL_PATH, 'r', encoding='utf-8') as f:
            local = yaml.safe_load(f) or {}
        if local:
            if not isinstance(local, dict):
                raise ValueError(f"本地覆盖文件根必须是字典: {_LOCAL_PATH}")
            config = _deep_merge(config, local)
    return config
