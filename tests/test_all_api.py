"""全数据源一键连通性测试（v0.8.6.5，ISS-049）

逐个测试所有出网点：DeepSeek AI / Kimi AI / Baostock登录/K线/行业/大盘
/新浪全市场/同花顺主营/东方财富新闻/巨潮公告。
每个独立 timeout + 重试 + 清晰报错，不刷屏。

跑法：PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_all_api.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout


def _timed(label, fn, timeout=30):
    """带超时跑 fn。fn 返回 (ok, detail) 元组。返回 (ok, detail, elapsed)。
    v0.8.6.5 修复：之前只看"没抛异常"就标True，把返回False的也标✅，误导。"""
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            r = ex.submit(fn).result(timeout=timeout)
        # 测试函数返回 (ok, detail) 元组，解包判定
        if isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], bool):
            return r[0], r[1], time.time() - t0
        return True, str(r)[:60], time.time() - t0
    except FuturesTimeout:
        return False, f"超时(>{timeout}s)", time.time() - t0
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t0


def test_deepseek():
    from src.core.benzong.auto_scorer import _build_ai_client
    from src.cli.main import load_config
    cfg = load_config()
    client, model = _build_ai_client(cfg)
    if client is None:
        return False, "AI client 构建失败(key未配?)"
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "回复OK"}],
        max_tokens=5, timeout=30,
    )
    return True, f"model={model} 回复={resp.choices[0].message.content[:20]}"


def test_kimi():
    from src.cli.main import load_config
    from openai import OpenAI
    cfg = load_config().get("ai", {})
    kimi = cfg.get("kimi", {})
    if not kimi.get("api_key"):
        return False, "Kimi key 未配，跳过"
    client = OpenAI(api_key=kimi["api_key"], base_url=kimi.get("base_url"), timeout=30, max_retries=1)
    resp = client.chat.completions.create(
        model=kimi.get("model", "moonshot-v1-8k"), messages=[{"role": "user", "content": "OK"}], max_tokens=5,
    )
    return True, f"Kimi 回复={resp.choices[0].message.content[:20]}"


def test_baostock_login():
    from src.data.akshare_client import _ensure_baostock_login, _baostock_logout
    _baostock_logout()  # 强制重登测试
    ok = _ensure_baostock_login()
    return ok, "登录成功" if ok else "登录失败"


def test_baostock_kline():
    import baostock as bs
    from src.data.akshare_client import _ensure_baostock_login
    if not _ensure_baostock_login():
        return False, "未登录"
    rs = bs.query_history_k_data_plus("sh.600000", "date,close", start_date="2026-06-01", end_date="2026-06-28", frequency="d")
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    return len(rows) > 0, f"K线 {len(rows)} 条"


def test_baostock_industry():
    import baostock as bs
    from src.data.akshare_client import _ensure_baostock_login
    if not _ensure_baostock_login():
        return False, "未登录"
    rs = bs.query_stock_industry(code="sh.600000")
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    return len(rows) > 0, f"行业 {rows[0] if rows else '空'}"


def test_sina_market():
    from src.scanner.market_cache import MarketCache
    df = MarketCache().get_all_stocks()
    return df is not None and not df.empty, f"全市场 {len(df) if df is not None else 0} 只"


def test_ths_business():
    from src.core.benzong.data_provider import get_business_introduction
    intro = get_business_introduction("600519")
    return bool(intro), f"主营介绍 {len(intro) if intro else 0} 字"


def test_em_news():
    from src.data.source_check import fix_curl_ssl_paths
    fix_curl_ssl_paths()
    from src.core.benzong.data_provider import get_recent_announcements
    news = get_recent_announcements("600519", days=30)
    return len(news) > 0, f"新闻 {len(news)} 条"


def test_cninfo_disclosure():
    from src.core.benzong.data_provider import _fetch_cninfo_disclosure
    import akshare as ak
    df = _fetch_cninfo_disclosure(ak, "600519", 30)
    return df is not None and not df.empty, f"巨潮公告 {len(df) if df is not None else 0} 条"


def test_market_turnover():
    from src.core.benzong.data_provider import get_market_turnover
    t = get_market_turnover()
    return t is not None, f"成交额 {t} 万亿" if t else "成交额获取失败"


TESTS = [
    ("DeepSeek AI", test_deepseek, 40),
    ("Kimi AI", test_kimi, 40),
    ("Baostock 登录", test_baostock_login, 30),
    ("Baostock K线", test_baostock_kline, 30),
    ("Baostock 行业", test_baostock_industry, 30),
    ("新浪 全市场快照", test_sina_market, 40),
    ("同花顺 主营介绍", test_ths_business, 30),
    ("东方财富 个股新闻", test_em_news, 40),
    ("巨潮 官方公告", test_cninfo_disclosure, 40),
    ("新浪 全市场成交额", test_market_turnover, 30),
]


def main():
    print("=" * 70)
    print("  全数据源一键连通性测试（ISS-049）")
    print("=" * 70)
    print(f"  共 {len(TESTS)} 个出网点，逐个 timeout 测试，不刷屏\n")
    results = []
    for name, fn, to in TESTS:
        print(f"▶ 测试 {name}...", end=" ", flush=True)
        ok, detail, elapsed = _timed(name, fn, timeout=to)
        mark = "✅" if ok else "❌"
        print(f"{mark} {detail} ({elapsed:.1f}s)", flush=True)
        results.append((name, ok))
    print()
    ok_n = sum(1 for _, ok in results if ok)
    fail = [n for n, ok in results if not ok]
    print("=" * 70)
    print(f"  结果: {ok_n}/{len(results)} 通过")
    if fail:
        print(f"  失败: {', '.join(fail)}")
        print("  排查: 看上面每个❌的报错，区分代理/防火墙/SSL/服务端断连")
    else:
        print("  全部出网点正常")
    print("=" * 70)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
