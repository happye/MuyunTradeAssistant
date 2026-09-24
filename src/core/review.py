"""复盘（scan review / watch）纯计算层（plan/ ADR-03 第一批，M4）。

从 src/cli/main.py 提取的无 IO 计算：迷你走势、同窗对齐聚合、收盘路径。
输入输出全是内存数据（数值列表 / dict / DataFrame），零网络、零文件、零终端——
CLI 与 chat 可以共享同一实现，展示层留在 cli（Rich 的动态 stdout 行为是
chat Tee 捕获的依赖，不进本模块）。

main.py 保留旧下划线名的兼容导出（tests 与既有调用点逐步迁移后再清理）。
"""
from __future__ import annotations

SPARK_CHARS = "▁▂▃▄▅▆▇"


def sparkline(values: list, max_points: int = 10) -> str:
    """数值序列 → Unicode 迷你走势（8 档 min-max 归一化）。

    超过 max_points 均匀降采样（保首尾端点，形状不变）——30 天窗口的完整逐日
    数值在复盘报告里，控制台曲线只保留形状。少于 2 个有效点返回 —。
    NaN/None 视为无效点（停牌行数据源可能给 NaN）。
    """
    vals = [v for v in values if v is not None and v == v]
    if len(vals) < 2:
        return "—"
    if len(vals) > max_points:
        idx = [round(i * (len(vals) - 1) / (max_points - 1)) for i in range(max_points)]
        vals = [vals[i] for i in idx]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK_CHARS[3] * len(vals)   # 全平
    return "".join(SPARK_CHARS[min(6, int((v - lo) / (hi - lo) * 7))] for v in vals)


def offset_curve(paths: list) -> dict:
    """多条收盘路径按「锚点后第 k 个点」对齐取累计涨幅均值：{k: 均值%}（锚点=k0=0）。

    锚点缺失（base0 空）的路径整条跳过——与逐票缺价剔除的口径一致。
    """
    buckets: dict = {}
    for pts in paths:
        if not pts:
            continue
        base0 = pts[0][1]
        if not base0:
            continue
        for k, (_, v) in enumerate(pts):
            buckets.setdefault(k, []).append((v / base0 - 1) * 100)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def curve_spark(curve: dict) -> str:
    """均值曲线 → 迷你走势。空曲线返回 —。"""
    if not curve:
        return "—"
    return sparkline([curve[k] for k in sorted(curve)])


def bench_point(bench_rows, bench_last, date: str):
    """基准同窗对齐（M4 第二批：scan_review/watch_pool 同构逻辑收敛）。

    返回 (基准锚点收盘, 同窗涨跌%)；基准缺失或锚点前无数据返回 (None, None)。
    bench_rows: [{date, close}]（升序）；bench_last: 最新收盘（None=基准缺失）。
    """
    if not bench_rows or bench_last is None:
        return None, None
    base = None
    for r in bench_rows:
        if r["date"] <= date:
            base = r["close"]
        else:
            break
    if not base:
        return None, None
    return base, (bench_last - base) / base * 100


def compute_chg_excess(base, latest, bench_chg):
    """逐票涨跌与同窗超额：(chg%, excess%)；base/latest/bench_chg 任一缺失则 (None, None)。"""
    if base and latest:
        chg = (latest - base) / base * 100
        if bench_chg is not None:
            return chg, chg - bench_chg
        return chg, None
    return None, None


def bench_path(bench_base, bench_rows, anchor_date: str):
    """基准同窗路径：[(锚, base)] + 锚点日之后的逐日 [(date, close)]，供 offset_curve 聚合。"""
    if not bench_base:
        return []
    return ([("锚", bench_base)]
            + [(rb["date"], rb["close"]) for rb in bench_rows if rb["date"] > anchor_date])


def review_path(code: str, scan_date: str, base, latest, df_cache: dict, today_str: str):
    """扫描日→今的收盘路径 [(标签, 价)]：锚点(扫描日基准价) + 其后逐 bar 收盘 + 实时价收尾。

    锚点优先用落盘快照价（扫描时刻真实价格）；K线序列从 df_cache 取（须已预热，
    IO 由调用方完成——本函数只做内存过滤与拼装）。末 bar 非今日时把实时价接为
    最后一点（baostock 当日 bar 17:30 后才就绪，盘中也有走势）。
    """
    df = df_cache.get(code)
    if df is None:
        return []
    date_col = "日期" if "日期" in df.columns else "date"
    close_col = "收盘" if "收盘" in df.columns else "close"
    if date_col not in df.columns or close_col not in df.columns:
        return []
    try:
        sub = df[df[date_col].astype(str).str[:10] > scan_date]
        pts = []
        if base:
            pts.append(("扫描日", float(base)))
        for _, r in sub.iterrows():
            c = r[close_col]
            if c and c == c:   # NaN != NaN：停牌行收盘价可能为 NaN
                pts.append((str(r[date_col])[:10], float(c)))
        last_date = str(sub.iloc[-1][date_col])[:10] if not sub.empty else None
        if latest and (last_date is None or last_date < today_str):
            pts.append(("现价", float(latest)))
        return pts
    except (ValueError, TypeError, KeyError):
        return []
