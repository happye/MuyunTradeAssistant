"""恐慌指数多周期走势图（matplotlib，Agg 后端，图片存本地）。

- 一张全景图：恐慌总分折线 + 五档情绪色带背景 + 周期窗口分界竖线
- 中文字体降级链：Microsoft YaHei -> SimHei -> 英文标签兜底（图形永远出得来）
- 任何绘图失败返回 None 并记人话告警，文字摘要功能不受影响（--no-chart 可跳过）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TIER_BANDS = [
    (0, 20, "#e8a09a", "极度贪婪"),    # 红（反向警示）
    (20, 40, "#f2d5a0", "贪婪"),
    (40, 60, "#d9d9d9", "中性"),
    (60, 80, "#a8c8e8", "恐慌"),
    (80, 100, "#5b9bd5", "极度恐慌"),
]


def _setup_cjk_font() -> tuple[bool, str]:
    """探测可用中文字体，设置 rcParams。返回 (是否中文, 字体名)。"""
    try:
        import matplotlib
        from matplotlib import font_manager
        installed = {f.name for f in font_manager.fontManager.ttflist}
        for want in ("Microsoft YaHei", "SimHei", "DengXian"):
            if want in installed:
                matplotlib.rcParams["font.sans-serif"] = [want]
                matplotlib.rcParams["axes.unicode_minus"] = False
                return True, want
    except Exception as e:
        logger.debug(f"中文字体探测失败: {e}")
    return False, ""


def render_market_chart(points: list[dict], out_path: Path,
                        windows: tuple[int, ...] = (5, 10, 22, 66),
                        source_note: str = "") -> Optional[str]:
    """绘制恐慌指数走势图并保存 PNG。points 升序 [{date, score}]。

    Returns: 保存路径字符串；失败返回 None（不抛异常，文字功能不受影响）。
    """
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        cjk, font_name = _setup_cjk_font()
        L = (lambda zh, en: zh if cjk else en)

        scores = [float(p["score"]) for p in points]
        dates = [p["date"] for p in points]
        if len(scores) < 3:
            logger.warning("恐慌指数图表生成跳过：序列样本不足（至少3个交易日）")
            return None

        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        for lo, hi, color, _name in _TIER_BANDS:
            ax.axhspan(lo, hi, color=color, alpha=0.35, zorder=0)
        for y in (20, 40, 60, 80):
            ax.axhline(y, color="white", lw=0.8, zorder=1)

        ax.plot(range(len(scores)), scores, color="#1f4e79", lw=1.6,
                marker="o", ms=2.5, zorder=3)
        last = scores[-1]
        ax.scatter([len(scores) - 1], [last], color="#c00000", s=40, zorder=4)

        # 周期窗口分界竖线（从右往左数第 n 个交易日）
        for w in windows:
            if w < len(scores):
                ax.axvline(len(scores) - w, color="#7f7f7f", ls="--", lw=0.9, zorder=2)
                ax.text(len(scores) - w, 101.5, L(f"-{w}日", f"-{w}d"),
                        ha="center", va="bottom", fontsize=8, color="#595959")

        step = max(1, len(dates) // 8)
        ticks = list(range(0, len(dates), step))
        if ticks[-1] != len(dates) - 1:
            ticks.append(len(dates) - 1)
        # 末尾相邻刻度太近会文字重叠（如 09-10/09-11），剔除靠内的那个
        while len(ticks) >= 2 and ticks[-1] - ticks[-2] < max(1, step // 2):
            ticks.pop(-2)
        ax.set_xticks(ticks)
        ax.set_xticklabels([dates[i][5:] for i in ticks], rotation=45, fontsize=8)
        ax.set_ylim(0, 104)
        ax.set_xlim(0, len(scores) - 1)
        ax.set_ylabel(L("恐慌分(0-100)", "Fear score (0-100)"), fontsize=10)
        ax.set_title(
            L(f"市场恐慌指数 · 近{len(scores)}个交易日走势（越高越恐慌）",
              f"Market Fear Index · last {len(scores)} trading days"),
            fontsize=13, pad=14)
        handles = [plt.Rectangle((0, 0), 1, 1, color=c, alpha=0.5) for _, _, c, _ in _TIER_BANDS]
        ax.legend(handles, [L(n, n) for _, _, _, n in _TIER_BANDS],
                  loc="upper left", fontsize=8, ncol=5, framealpha=0.6)
        if source_note:
            ax.text(0.99, -0.13, source_note, transform=ax.transAxes,
                    ha="right", fontsize=7.5, color="#7f7f7f")
        fig.tight_layout()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        logger.warning(f"恐慌指数图表生成失败，文字摘要不受影响: {type(e).__name__}: {e}")
        return None
