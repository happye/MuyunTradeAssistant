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

import copy
import json
import logging
import math
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
# v0.8.7.6：B23 修复——v0.8.6.5 后 8 个 commit 改过 benzong（含 deepseek-chat→
#           deepseek-v4-flash 换模型=换评分分布）未 bump，旧缓存命中"改代码不生效"
# v0.8.8.7：ISS-083 景气度接线——industry_prosperity SYSTEM_PROMPT 增客观证据
#           优先规则 + user_prompt 注入【客观行业数据】块 + 兜底变严（三源全空
#           才 50），旧景气度缓存全部失效重算
CACHE_VERSION = "v0.8.29.1"  # ISS-117 Q3：AI invalidate 降红线候选（S0/S1 批后再次变更同一字段语义，旧缓存作废）


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


# ── T05-K：缓存 payload 资格门（读/写共用）────────────────────────────
# 同股同日 + 当前 CACHE_VERSION 只是必要条件，不是充分条件：坏值命中会让
# auto_score 跳过维度重算、展示层 join/splitlines 出错或把脏分数拼进总分。
# 这里只做本地类型/范围校验（不联网、不重算评分、不改公式/prompt/缓存键）。
# 必需字段：score/confidence；已消费可选字段：invalidate/warnings/sources/
# reasoning/market_turnover（其中 market_turnover 缺失/None 或 >=0 有限数，
# 显式 0 保留消费方既有 `or 1.0` 回落）。未知额外 metadata 保持兼容。
_PAYLOAD_MISSING = object()


def _finite_json_number(value):
    """JSON int/float 且有限；bool 是 int 子类，必须先排除。非法返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _payload_invalid_field(payload) -> Optional[str]:
    """返回首个不合格字段的固定类别名；全部合格返回 None。

    不回显字段值/原始 payload——告警只能带固定类别，避免把坏数据或异常正文
    打进日志。未知额外字段不做完整 schema 校验（向后兼容）。
    """
    if not isinstance(payload, dict):
        return "payload"
    score = _finite_json_number(payload.get("score", _PAYLOAD_MISSING))
    if score is None or not 0.0 <= score <= 100.0:
        return "score"
    confidence = _finite_json_number(payload.get("confidence", _PAYLOAD_MISSING))
    # 0/负/非有限/>1 都代表"没有可复用结果"
    if confidence is None or not 0.0 < confidence <= 1.0:
        return "confidence"
    if "invalidate" in payload:
        value = payload["invalidate"]
        if value is not None and not isinstance(value, bool):
            return "invalidate"
    for field in ("warnings", "sources"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, list) or any(
                    not isinstance(item, str) for item in value):
                return field
    if "reasoning" in payload and not isinstance(payload["reasoning"], str):
        return "reasoning"
    if "market_turnover" in payload:
        value = payload["market_turnover"]
        if value is not None:
            number = _finite_json_number(value)
            if number is None or number < 0.0:
                return "market_turnover"
    return None


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
        # v0.8.7.6 审计修复 B04：顶层非 dict（如 [1,2,3]）原抛 AttributeError 直接炸 bz；
        # UnicodeDecodeError（截断/坏编码，实测56%截断偏移中招）不是 JSONDecodeError 逃逸
        if not isinstance(data, dict):
            logger.warning(f"缓存内容格式异常(非dict)，当未命中: {p.name}")
            return None
        # 版本校验：旧版本缓存当未命中（改了维度逻辑必须 bump CACHE_VERSION）
        if data.get("_cache_version") != CACHE_VERSION:
            return None
        # T05-K：版本相同也必须过 payload 资格门；坏值当未命中，坏文件原地保留
        invalid_field = _payload_invalid_field(data)
        if invalid_field is not None:
            logger.warning(
                f"笨总缓存读取 payload 资格校验失败（字段 {invalid_field} 非法），"
                f"当未命中: {p.name}")
            return None
        return data
    except Exception as e:
        # B04：坏缓存文件（掉电/磁盘满/并发写交错）只当未命中，绝不让 bz 整条命令崩
        logger.warning(f"缓存读取失败，当未命中 {p.name}: {type(e).__name__}: {e}")
        return None


def set(code: str, date: str, dim: str, result: dict) -> bool:
    """写缓存。返回是否成功（失败不抛异常，仅记录）

    T05-K：只接受真正的 dict；先对实际将被写入的 record 做深层独立快照（含
    sources/warnings/未知 metadata 等嵌套可变数据），再加 metadata、对冻结副本
    校验。非法/快照失败在计算缓存路径/初始化目录/打开临时文件/原子替换之前
    返回 False，既有合法缓存字节不动；任何失败都不回显异常正文。
    """
    if not isinstance(result, dict):
        # 严格原输入 dict：list-of-pairs/items() 等即使能 dict() 转换也不得绕过
        logger.warning(
            "笨总缓存写入 payload 资格校验失败（字段 payload 非 dict），拒绝写入")
        return False
    try:
        # 深层快照：写盘记录与调用方后续对嵌套 list/dict 的突变完全隔离
        record = copy.deepcopy(result)
    except Exception as e:
        logger.warning(
            f"笨总缓存写入失败（快照 {type(e).__name__}），缓存未更新")
        return False
    record["_cached_at"] = datetime.now().isoformat()
    record["_cache_version"] = CACHE_VERSION
    invalid_field = _payload_invalid_field(record)
    if invalid_field is not None:
        logger.warning(
            f"笨总缓存写入 payload 资格校验失败（字段 {invalid_field} 非法），拒绝写入")
        return False
    try:
        p = _cache_path(code, date, dim)
    except (OSError, TypeError, ValueError):
        # 路径/根目录不可用：失败持久化，绝不假装已缓存
        logger.warning("笨总缓存写入失败（缓存路径不可用），缓存未更新")
        return False
    try:
        # v0.8.7.6 审计修复 B24：原子写（tmp + os.replace）——原直接 open("w") truncate，
        # 多线程同 key 并发写会交错出混合内容（实测 8线程×20次写后文件 1341 字节≠单条 570），
        # 也是 B04 坏缓存崩溃的成因。os.replace 同目录原子替换。
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except (OSError, TypeError, ValueError) as e:
        # 只保留异常类别，绝不回显任意异常正文（可能含调用方数据/秘密）
        logger.warning(f"笨总缓存写入失败（{type(e).__name__}），缓存未更新")
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
