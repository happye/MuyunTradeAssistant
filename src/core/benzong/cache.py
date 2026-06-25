"""笨总 6 维评分缓存层（v0.8.6.2）

设计：
- 文件级缓存，路径 ~/.muyun/benzong_cache/{stock_code}_{date}_{dim}.json
- 缓存键：(stock_code, date, dimension) — 同股同日同维度只算一次
- 跨日自动失效（用日期切片）
- 无外部依赖（只用 stdlib：pathlib + json）
- 用途：避免每次跑 bz 600519 重复 6 次 AI 调用 ($$$+耗时)

API：
- get(code, date, dim) -> Optional[dict]
- set(code, date, dim, result: dict)
- clear(code=None, before_date=None)
- get_cache_dir() -> Path  （便于诊断）
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# 缓存版本号：改了任何维度评分逻辑(prompt/降级规则/公式)必须 bump，
# 否则旧缓存会被命中导致"改了代码不生效"。get() 读到旧版本自动当未命中。
# v0.8.6.4：risk_deduction 新闻缺失降级从 score=0 改为 score=50
# v0.8.6.5：business_purity prompt 限 reasoning≤60字 + max_tokens 1500→2000，
#           截断退化 confidence 0.5→0.3
CACHE_VERSION = "v0.8.6.5"


# 缓存根目录（用户级，跨项目共享）
def _get_cache_root() -> Path:
    """缓存根：~/.muyun/benzong_cache/。失败回退到项目内 .cache/

    v0.8.6.3：拼音修正 benzhong→benzong。若旧目录 ~/.muyun/benzhong_cache/
    存在，自动迁移其内容到新目录（一次性，迁移后删旧目录），不浪费用户已算的缓存。
    """
    try:
        home = Path.home()
        cache = home / ".muyun" / "benzong_cache"
        cache.mkdir(parents=True, exist_ok=True)

        # 一次性迁移旧拼写目录
        old = home / ".muyun" / "benzhong_cache"
        if old.exists() and old != cache:
            try:
                moved = 0
                for f in old.glob("*.json"):
                    dest = cache / f.name
                    if not dest.exists():
                        f.replace(dest)
                        moved += 1
                old.rmdir()  # 空了才删，非空保留不强制
                if moved:
                    logger.info(f"缓存迁移：benzhong_cache→benzong_cache，{moved} 个文件")
            except OSError as e:
                logger.warning(f"旧缓存目录迁移失败（不影响使用）：{e}")

        return cache
    except (OSError, PermissionError):
        # 兜底：项目内 .cache/
        proj_root = Path(__file__).resolve().parents[3]
        cache = proj_root / ".cache" / "benzong"
        cache.mkdir(parents=True, exist_ok=True)
        logger.warning(f"用户目录不可写，缓存改用 {cache}")
        return cache


_CACHE_ROOT = None  # 懒初始化


def get_cache_dir() -> Path:
    """获取缓存目录（懒初始化，便于测试 monkey-patch）"""
    global _CACHE_ROOT
    if _CACHE_ROOT is None:
        _CACHE_ROOT = _get_cache_root()
    return _CACHE_ROOT


def _cache_path(code: str, date: str, dim: str) -> Path:
    """生成缓存文件路径。文件名安全：替换危险字符"""
    safe_code = str(code).replace("/", "_").replace("\\", "_")
    safe_date = str(date).replace("/", "-")
    safe_dim = str(dim).replace("/", "_")
    return get_cache_dir() / f"{safe_code}_{safe_date}_{safe_dim}.json"


def get(code: str, date: str, dim: str) -> Optional[dict]:
    """读缓存。命中返回 dict，未命中返回 None。

    Args:
        code: 股票代码（如 "600519"）
        date: YYYY-MM-DD（如 "2026-06-20"）
        dim: 维度名（如 "industry_prosperity"）
    """
    p = _cache_path(code, date, dim)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        # 版本校验：旧版本缓存当未命中（改了维度逻辑必须 bump CACHE_VERSION）
        if data.get("_cache_version") != CACHE_VERSION:
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"缓存读取失败 {p.name}: {e}")
        return None


def set(code: str, date: str, dim: str, result: dict) -> bool:
    """写缓存。返回是否成功（失败不抛异常，仅记录）"""
    p = _cache_path(code, date, dim)
    try:
        # 加 metadata
        record = dict(result)
        record["_cached_at"] = datetime.now().isoformat()
        record["_cache_version"] = CACHE_VERSION
        with p.open("w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return True
    except (OSError, TypeError) as e:
        logger.warning(f"缓存写入失败 {p.name}: {e}")
        return False


def clear(code: Optional[str] = None, before_date: Optional[str] = None) -> int:
    """清缓存。

    Args:
        code: 仅清此股，None 表示清所有
        before_date: 仅清此日期之前的（YYYY-MM-DD），None 表示无日期过滤

    Returns:
        删除的文件数
    """
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return 0
    deleted = 0
    for f in cache_dir.glob("*.json"):
        # 解析文件名 {code}_{date}_{dim}.json
        try:
            parts = f.stem.split("_")
            if len(parts) < 3:
                continue
            file_code = parts[0]
            file_date = parts[1]
            if code and file_code != code:
                continue
            if before_date and file_date >= before_date:
                continue
            f.unlink()
            deleted += 1
        except (OSError, ValueError):
            continue
    if deleted:
        logger.info(f"清理缓存 {deleted} 个文件 (code={code}, before={before_date})")
    return deleted


def list_cache(code: Optional[str] = None) -> list[dict]:
    """列出缓存（诊断用）。返回 [{code, date, dim, cached_at, file_size}]"""
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return []
    items = []
    for f in cache_dir.glob("*.json"):
        try:
            parts = f.stem.split("_")
            if len(parts) < 3:
                continue
            file_code, file_date = parts[0], parts[1]
            file_dim = "_".join(parts[2:])
            if code and file_code != code:
                continue
            items.append({
                "code": file_code,
                "date": file_date,
                "dim": file_dim,
                "size": f.stat().st_size,
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda x: x["mtime"], reverse=True)
