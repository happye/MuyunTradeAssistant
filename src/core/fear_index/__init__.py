"""市场恐慌指数（Fear Index）公共入口。

设计文档：docs/2026-09-13_市场恐慌指数_评估与实现方案.md
- 纯客观计算，AI 调用次数=0（P-纯客观）；数据缺失显式标 MISSING（P-显式降级）；
  成分级溯源（P-可溯源）；回测/历史只读本地序列库，无网络路径（P-回测禁网）。
- 恐慌分 0-100，越高越恐慌：0-20 极度贪婪 / 20-40 贪婪 / 40-60 中性 /
  60-80 恐慌 / 80-100 极度恐慌。

公共 API：
  get_fear_report(scope_text="", refresh=False)      一键总览（当日实时）
  get_fear_history_summary(windows, make_chart)      多周期回顾 + 走势图存本地
  run_backfill(days, progress)                       涨停池历史回填（显式命令）
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .aggregator import DEFAULT_WEIGHTS, TIERS, aggregate, tier_of
from . import history as _history
from .history import (
    backfill_zt_pools, ensure_light_history, recent_trade_date,
    save_snapshot, snapshot_path, synthesize_history, FEAR_VERSION,
)
from .metrics import MetricValue
from .metrics.market_metrics import compute_market_components
from .normalizer import clamp_score
from .scope import parse_scope

logger = logging.getLogger(__name__)

# 对外展示用快照；数据文件的版本校验统一走 _history.FEAR_VERSION（运行期单一来源，
# 防 import 期绑定与运行期值脱节）。改口径/权重逻辑必须 bump history.FEAR_VERSION。
FEAR_CACHE_VERSION = FEAR_VERSION

_WINDOW_LABELS = {5: "近5日", 10: "近10日", 22: "近1个月(22交易日)", 66: "近3个月(66交易日)"}
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_weights() -> dict:
    """configs/fear_index.yaml 的 weights 覆盖（文件不存在用内置等权）。"""
    try:
        import yaml
        p = _REPO_ROOT / "configs" / "fear_index.yaml"
        if p.exists():
            cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            w = cfg.get("weights") or {}
            return {k: float(v) for k, v in w.items() if k in DEFAULT_WEIGHTS}
    except Exception as e:
        logger.warning(f"恐慌指数权重配置读取失败，使用内置等权: {e}")
    return {}


def get_fear_report(scope_text: str = "", refresh: bool = False) -> dict:
    """一键总览：计算当日各成分并聚合，落当日快照。

    Returns: report dict；scope 不支持时 {"error": 提示}。
    """
    scope = parse_scope(scope_text)
    if not scope.supported:
        return {"error": scope.message}
    baseline = recent_trade_date()
    quality = ensure_light_history(baseline, force=refresh)
    components = compute_market_components(baseline, refresh=refresh)
    score, used, missing = aggregate(components, weights=_load_weights())

    report = {
        "scope": scope.kind,
        "as_of": baseline,
        "score": None if score is None else round(score, 1),
        "tier": tier_of(score),
        "used": used,
        "missing": missing,
        "components": [c.as_dict() for c in components],
        "quality": quality,
        "version": _history.FEAR_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _persist_daily_zt(baseline, components)
    if score is not None:
        save_snapshot({
            "date": baseline,
            "score": round(score, 1),
            "tier": report["tier"],
            "metrics": {c.name: c.raw for c in components if c.raw is not None},
            "missing": missing,
            "generated_at": report["generated_at"],
        })
    return report


def _persist_daily_zt(baseline: str, components: list[MetricValue]) -> None:
    """当日涨停池计数落盘（自动积累 zt 序列，无需等 backfill）。

    只要池数据拉到了就落盘（即使分位样本不足导致成分 MISSING——计数本身是
    真实数据，先积累序列，分位随序列增长自动转正）。
    """
    from .history import _atomic_write_json, _load_json
    zt = next((c for c in components if c.name == "zt_heat"), None)
    if not zt or not zt.extra or zt.extra.get("zt") is None:
        return
    p = (snapshot_path(baseline).parent.parent / "hist" / "hist_zt"
         / f"{baseline.replace('-', '')}.json")
    existing = _load_json(p)
    if existing is not None and existing.get("version") == _history.FEAR_VERSION:
        return  # 已落盘（回填或当日早前），不覆盖
    rec = {
        "date": baseline, "version": _history.FEAR_VERSION,
        **{k: zt.extra.get(k) for k in ("zt", "dt", "zb", "zb_rate")},
    }
    try:
        _atomic_write_json(p, rec)
    except Exception as e:
        logger.warning(f"恐慌指数当日涨停计数落盘失败（不影响指数）: {e}")


def get_fear_history_summary(
    windows: tuple[int, ...] = (5, 10, 22, 66),
    make_chart: bool = True,
    refresh: bool = False,
) -> dict:
    """多周期回顾：近5日/10日/1个月/3个月摘要 + 走势图存本地。

    数据全部来自本地序列库（轻量回填 + 合成序列 + 快照），除轻量回填外零新增请求。
    """
    baseline = recent_trade_date()
    quality = ensure_light_history(baseline, force=refresh)
    live = get_fear_report(refresh=refresh)
    if "error" in live:
        return live
    pts = synthesize_history(days=max(windows), baseline=baseline)
    cur = live.get("score")
    # 合并当日实时分（合成序列是收盘口径，末点用实时值替换/追加）
    if cur is not None:
        if pts and pts[-1]["date"] == baseline:
            pts[-1] = dict(pts[-1], score=cur)
        elif not pts or pts[-1]["date"] < baseline:
            pts.append({"date": baseline, "score": cur, "n_components": len(live.get("used", []))})

    rows = [_window_stat(pts, w) for w in windows]
    rows = [r for r in rows if r]
    summary = {
        "as_of": baseline,
        "score": cur,
        "tier": live.get("tier"),
        "windows": rows,
        "quality": quality,
        "points_count": len(pts),
        "headline": _headline(cur, live.get("tier"), rows),
        "version": _history.FEAR_VERSION,
    }
    if make_chart:
        from .charts import render_market_chart
        from .history import BASE_DIR
        out = BASE_DIR / "charts" / f"fear_market_{baseline.replace('-', '')}.png"
        note = f"数据源: 新浪快照/baostock/东财涨停池/乐咕PE | 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        chart = render_market_chart(pts[-66:], out, windows=tuple(windows), source_note=note)
        if chart:
            summary["chart_path"] = chart
        else:
            summary["chart_error"] = "走势图生成失败（文字摘要不受影响），详见日志"
    return summary


def _window_stat(pts: list[dict], w: int) -> Optional[dict]:
    seg = pts[-w:] if w < len(pts) else pts
    scores = [p["score"] for p in seg]
    if len(scores) < 2:
        return None
    mean = sum(scores) / len(scores)
    mn_i = scores.index(min(scores))
    mx_i = scores.index(max(scores))
    cur = scores[-1]
    rank = sum(1 for s in scores if s <= cur) / len(scores) * 100.0
    diff = cur - scores[0]
    trend = "恐慌加深" if diff >= 8 else "恐慌缓和" if diff <= -8 else "震荡"
    return {
        "window": w,
        "label": _WINDOW_LABELS.get(w, f"近{w}日"),
        "start": seg[0]["date"], "end": seg[-1]["date"],
        "mean": round(mean, 1),
        "min": min(scores), "max": max(scores),
        "min_date": seg[mn_i]["date"], "max_date": seg[mx_i]["date"],
        "cur_rank": round(rank, 0),
        "trend": trend,
        "samples": len(scores),
        "full": len(scores) >= w,
    }


def _headline(cur: Optional[float], tier: Optional[str], rows: list[dict]) -> str:
    """模板化人话总结（规则填数，非 AI）。"""
    if cur is None:
        return "恐慌指数无法评估（成分数据缺失过多），请检查数据源或稍后重试。"
    parts = [f"恐慌指数 {cur:.1f}（{tier}）"]
    w5 = next((r for r in rows if r["window"] == 5), None)
    w66 = next((r for r in rows if r["window"] == 66), None)
    if w5:
        parts.append(f"较{w5['label']}均值{'上升' if cur >= w5['mean'] else '下降'}"
                     f" {abs(cur - w5['mean']):.1f} 点，趋势{w5['trend']}")
    if w66 and w66.get("cur_rank") is not None:
        parts.append(f"处于{w66['label']} {w66['cur_rank']:.0f}% 分位")
    if w66 and not w66["full"]:
        parts.append(f"（序列样本 {w66['samples']}/{w66['window']}，可运行 fear backfill 回填完整历史）")
    return "，".join(parts) + "。"


def run_backfill(days: int = 250, progress=None) -> dict:
    """显式回填：轻量序列刷新 + 涨停池逐日回填（断点续传）。"""
    baseline = recent_trade_date()
    quality = ensure_light_history(baseline, force=True)
    stats = backfill_zt_pools(days=days, progress=progress)
    stats["light_history"] = quality
    return stats


__all__ = [
    "FEAR_CACHE_VERSION", "TIERS", "DEFAULT_WEIGHTS",
    "get_fear_report", "get_fear_history_summary", "run_backfill",
    "tier_of", "parse_scope",
]
