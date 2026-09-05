"""jieba 自定义词典统一加载（v0.8.9.0，修复 P2-C）

背景：此前全 src 无任何 load_userdict/add_word 调用，jieba 用默认词典分词，
"打板/低吸/气宗/剑宗/笨总"等领域词会被切碎，导致：
- KeywordRetriever 关键词召回对领域术语失灵
- TFIDFEmbedder 的 TF-IDF 特征被切碎稀释

本模块提供幂等的 load_userdict()，所有使用 jieba 分词的入口（检索层/嵌入层）
在首次分词前调用一次即可。

用法:
    from src.rag.userdict import load_userdict
    load_userdict()  # 幂等，进程内只生效一次
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 词表随仓库走：configs/jieba_userdict.txt
_USERDICT_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "jieba_userdict.txt"

_loaded = False


def load_userdict() -> bool:
    """加载金融领域 jieba 自定义词典（幂等）

    Returns:
        是否成功加载（已加载过/文件缺失均返回对应布尔，不抛异常）
    """
    global _loaded
    if _loaded:
        return True
    if not _USERDICT_PATH.exists():
        logger.warning("jieba自定义词典不存在: %s", _USERDICT_PATH)
        return False
    try:
        import jieba

        jieba.load_userdict(str(_USERDICT_PATH))
        _loaded = True
        logger.info("jieba自定义词典已加载: %s", _USERDICT_PATH.name)
        return True
    except Exception as exc:
        logger.warning("加载jieba自定义词典失败: %s", exc)
        return False
