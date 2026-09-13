"""恐慌指数数据源探针（直跑脚本，非 pytest 用例）。

用途：实施前验证恐慌指数所需 akshare 接口在本环境的真实可用性、
参数签名与返回字段结构。跑法：
    cd 仓库根 && .venv/Scripts/python.exe tests/data_sources/probe_fear_data.py
产出：逐接口打印 OK/FAIL + 字段清单 + 样本行，供 fear_index metrics 实现对齐字段名。
每项独立 try/except，单项失败不拖垮其余探针。全程绕系统代理（东财/金融源直连）。
"""
import inspect
import os
import sys
from datetime import datetime, timedelta

# 金融数据源直连，绕系统代理（AGENTS：127.0.0.1:7890 干扰东财系）
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import akshare as ak

TODAY = datetime.now().strftime("%Y%m%d")
HIST = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
YEAR_START = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe(label: str, fn, max_rows: int = 3) -> None:
    try:
        df = fn()
        if df is None:
            print(f"[FAIL] {label}: 返回 None")
            return
        print(f"[OK] {label}: {len(df)} rows")
        print("  columns:", list(df.columns))
        with_print = df.tail(2)
        for _, row in with_print.iterrows():
            print("  sample:", dict(row))
    except Exception as e:  # noqa: BLE001 探针需要吞掉一切失败继续
        print(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:200]}")


def probe_sig(label: str, fn) -> None:
    try:
        print(f"[SIG] {label}{inspect.signature(fn)}")
    except Exception as e:  # noqa: BLE001
        print(f"[SIG-FAIL] {label}: {e}")


# ── 1. 签名总览 ──────────────────────────────────────────
section("1. 接口签名")
for name in (
    "stock_zt_pool_em", "stock_zt_pool_zbgc_em", "stock_zt_pool_dtgc_em",
    "stock_market_activity_legu", "stock_a_gxl_lg", "stock_index_pe_lg",
    "index_zh_a_hist", "stock_margin_sse", "stock_margin_szse",
    "option_current_em", "bond_zh_us_rate",
):
    probe_sig(name, getattr(ak, name))

# ── 2. 涨停/炸板/跌停池：当日 + 历史（回填可行性） ────────
section("2. 东财涨停池系（当日 + 历史回填）")
probe(f"stock_zt_pool_em({TODAY})", lambda: ak.stock_zt_pool_em(date=TODAY))
probe(f"stock_zt_pool_em({HIST}) 历史回填", lambda: ak.stock_zt_pool_em(date=HIST))
probe(f"stock_zt_pool_zbgc_em({TODAY}) 炸板池", lambda: ak.stock_zt_pool_zbgc_em(date=TODAY))
probe(f"stock_zt_pool_dtgc_em({TODAY}) 跌停池", lambda: ak.stock_zt_pool_dtgc_em(date=TODAY))

# ── 3. 乐咕市场活跃度（涨跌家数交叉验证源候选） ──────────
section("3. 乐咕市场活跃度")
probe("stock_market_activity_legu()", ak.stock_market_activity_legu)

# ── 4. 股债性价比：股息率 / 指数PE 历史 ──────────────────
section("4. 股债性价比数据源")
probe('stock_a_gxl_lg(symbol="沪深300")', lambda: ak.stock_a_gxl_lg(symbol="沪深300"))
probe('stock_index_pe_lg(symbol="沪深300")', lambda: ak.stock_index_pe_lg(symbol="沪深300"))

# ── 5. 两市成交额历史：上证 + 深证综指日线 ───────────────
section("5. 指数日线（成交额历史回填口径：沪+深）")
probe(f'index_zh_a_hist("000001",{YEAR_START},{TODAY})',
      lambda: ak.index_zh_a_hist(symbol="000001", period="daily",
                                 start_date=YEAR_START, end_date=TODAY))
probe(f'index_zh_a_hist("399106",{YEAR_START},{TODAY})',
      lambda: ak.index_zh_a_hist(symbol="399106", period="daily",
                                 start_date=YEAR_START, end_date=TODAY))

# ── 6. 两融余额汇总历史 ──────────────────────────────────
section("6. 两融汇总（历史回填）")
probe(f"stock_margin_sse({YEAR_START},{TODAY})",
      lambda: ak.stock_margin_sse(start_date=YEAR_START, end_date=TODAY))
probe(f"stock_margin_szse({YEAR_START},{TODAY})",
      lambda: ak.stock_margin_szse(start_date=YEAR_START, end_date=TODAY))

# ── 7. 10Y 国债收益率 ────────────────────────────────────
section("7. 无风险利率")
probe("bond_zh_us_rate()", ak.bond_zh_us_rate)

# ── 8. 期权行情（PCR 候选，P2 探针） ─────────────────────
section("8. 期权（PCR 候选）")
probe('option_current_em(symbol="沪深300ETF期权")',
      lambda: ak.option_current_em(symbol="沪深300ETF期权"))

print("\n探针完成。")
