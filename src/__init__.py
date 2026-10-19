"""暮云思辨投资助手 - AI驱动的A股决策辅助工具"""

__version__ = "0.1.0"

# 本地原型：网络无法连通 Baostock 时，可设 MUYUN_DATA_SHIM=tencent 启用腾讯/新浪兼容垫片
try:  # pragma: no cover - 环境相关
    from src.data.bs_shim import maybe_install_from_env as _maybe_shim
    _maybe_shim()
except Exception:  # 垫片失败不应影响主程序
    pass
