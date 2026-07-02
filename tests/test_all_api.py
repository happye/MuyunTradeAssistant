"""全数据源一键连通性测试（v0.8.6.6，ISS-049 彻底版）

第一性原理审查后重写，覆盖全部死角：
1. 模拟实跑环境：设 NO_PROXY=*（和 start.py 一致），不只 pop 代理env
2. 代理绕过验证：故意设 HTTP_PROXY + NO_PROXY=*，验证能绕过（治本验证）
3. SSL修复独立测试：证书文件是否生成到ASCII路径
4. Baostock 大盘指数（l命令判MarketState靠它）
5. Baostock 心跳重连（断线自动恢复）
6. THS 板块成分股（bz scan 主题词通道B靠它）
7. detail 拼接bug修复 + baostock登录只测一次不反复logout

跑法：PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_all_api.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 模拟实跑环境（和 start.py / src/cli/main.py 启动逻辑一致）
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"      # 关键：和实跑一致，强制直连
os.environ["no_proxy"] = "*"
os.environ["TQDM_DISABLE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout


def _timed(fn, timeout=30):
    """带超时跑 fn。fn 返回 (ok, detail)。返回 (ok, detail, elapsed)。
    严格按返回值判定，不把"没抛异常"当通过。"""
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            r = ex.submit(fn).result(timeout=timeout)
        if isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], bool):
            return r[0], r[1], time.time() - t0
        return True, str(r)[:60], time.time() - t0
    except FuturesTimeout:
        return False, f"超时(>{timeout}s)", time.time() - t0
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t0


# ========== 代理绕过验证（治本核心）==========
def test_proxy_bypass():
    """验证 NO_PROXY=* 能绕过代理：故意设代理env，看能否直连成功"""
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    try:
        import requests
        r = requests.get("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                         params={"page": 1, "num": 3, "node": "hs_a", "_s_r_a": "init"},
                         timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        ok = r.status_code == 200 and "html" not in r.headers.get("content-type", "").lower()
        return ok, f"代理env已设+NO_PROXY=* → 状态{r.status_code} {'直连成功' if ok else '被代理拦截'}"
    finally:
        # 测完清掉，不影响后续测试
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


# ========== AI 调用 ==========
def test_deepseek():
    from src.core.benzong.auto_scorer import _build_ai_client
    from src.cli.main import load_config
    client, model = _build_ai_client(load_config())
    if client is None:
        return False, "AI client 构建失败(key未配?)"
    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "回复OK"}], max_tokens=5, timeout=30)
    return True, f"model={model} 回复={resp.choices[0].message.content[:20]}"


def test_kimi():
    from src.cli.main import load_config
    from openai import OpenAI
    kimi = load_config().get("ai", {}).get("kimi", {})
    if not kimi.get("api_key"):
        return False, "Kimi key 未配，跳过"
    client = OpenAI(api_key=kimi["api_key"], base_url=kimi.get("base_url"), timeout=30, max_retries=1)
    resp = client.chat.completions.create(model=kimi.get("model", "moonshot-v1-8k"), messages=[{"role": "user", "content": "OK"}], max_tokens=5)
    return True, f"Kimi 回复={resp.choices[0].message.content[:20]}"


# ========== Baostock ==========
def test_baostock_login():
    from src.data.akshare_client import _ensure_baostock_login
    return _ensure_baostock_login(), "登录成功" if _ensure_baostock_login() else "登录失败"


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
    return len(rows) > 0, f"行业 {rows[0][3] if rows and len(rows[0])>3 else '空'}"


def test_baostock_index():
    """大盘指数(沪深300) - l命令判MarketState靠它"""
    import baostock as bs
    from src.data.akshare_client import _ensure_baostock_login
    if not _ensure_baostock_login():
        return False, "未登录"
    rs = bs.query_history_k_data_plus("sh.000300", "date,close,preclose", start_date="2026-06-01", end_date="2026-06-28", frequency="d")
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    return len(rows) > 0, f"沪深300 K线 {len(rows)} 条"


def test_baostock_reconnect():
    """心跳重连：logout后能否自动重login"""
    from src.data.akshare_client import _ensure_baostock_login, _baostock_logout
    _baostock_logout()  # 强制断开
    ok = _ensure_baostock_login()  # 应自动重连
    return ok, "断线后自动重连成功" if ok else "重连失败"


# ========== 新浪全市场 ==========
def test_sina_market():
    from src.scanner.market_cache import MarketCache
    df = MarketCache().get_all_stocks()
    ok = df is not None and not df.empty
    return ok, f"全市场 {len(df) if df is not None else 0} 只"


def test_market_turnover():
    from src.core.benzong.data_provider import get_market_turnover
    t = get_market_turnover()
    ok = t is not None
    return ok, f"成交额 {t} 万亿" if ok else "成交额获取失败"


# ========== SSL修复 ==========
def test_ssl_fix():
    """SSL证书是否生成到ASCII路径（中文项目路径致curl_cffi找不到证书的修复）"""
    from src.data.source_check import fix_curl_ssl_paths
    ok = fix_curl_ssl_paths()
    if not ok:
        return False, "SSL修复失败(证书未生成)"
    cacert = os.path.expanduser("~/.muyun_cacert.pem")
    exists = os.path.exists(cacert)
    return exists, f"证书文件 {'存在' if exists else '不存在'}: {cacert}"


# ========== akshare 各接口 ==========
def test_ths_business():
    from src.core.benzong.data_provider import get_business_introduction
    intro = get_business_introduction("600519")
    ok = bool(intro)
    return ok, f"主营介绍 {len(intro) if intro else 0} 字"


def test_em_news():
    from src.data.source_check import fix_curl_ssl_paths
    fix_curl_ssl_paths()
    from src.core.benzong.data_provider import get_recent_announcements
    news = get_recent_announcements("600519", days=30)
    ok = len(news) > 0
    return ok, f"新闻 {len(news)} 条"


def test_cninfo_disclosure():
    from src.core.benzong.data_provider import _fetch_cninfo_disclosure
    import akshare as ak
    df = _fetch_cninfo_disclosure(ak, "600519", 30)
    ok = df is not None and not df.empty
    return ok, f"巨潮公告 {len(df) if df is not None else 0} 条"


def test_ths_industry_stocks():
    """THS板块成分股 - bz scan 主题词通道B靠它"""
    from src.scanner.market_cache import MarketCache
    try:
        codes = MarketCache().get_stocks_by_industry("半导体")
        ok = codes is not None and len(codes) > 0
        return ok, f"半导体板块成分股 {len(codes) if codes else 0} 只"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}"


# ========== 测试清单（按依赖顺序）==========
TESTS = [
    # 代理治本验证（最先测，这是根因）
    ("代理绕过(NO_PROXY=*)", test_proxy_bypass, 20),
    # AI
    ("DeepSeek AI", test_deepseek, 40),
    ("Kimi AI", test_kimi, 40),
    # Baostock（登录先测，后续依赖）
    ("Baostock 登录", test_baostock_login, 30),
    ("Baostock K线", test_baostock_kline, 30),
    ("Baostock 行业", test_baostock_industry, 30),
    ("Baostock 大盘指数", test_baostock_index, 30),
    ("Baostock 心跳重连", test_baostock_reconnect, 30),
    # SSL修复
    ("SSL证书修复", test_ssl_fix, 15),
    # 新浪全市场
    ("新浪 全市场快照", test_sina_market, 40),
    ("新浪 全市场成交额", test_market_turnover, 30),
    # akshare 各接口
    ("同花顺 主营介绍", test_ths_business, 30),
    ("东方财富 个股新闻", test_em_news, 40),
    ("巨潮 官方公告", test_cninfo_disclosure, 40),
    ("THS 板块成分股", test_ths_industry_stocks, 30),
]


def main():
    print("=" * 70)
    print("  全数据源一键连通性测试（ISS-049 彻底版）")
    print("=" * 70)
    print(f"  共 {len(TESTS)} 个出网点，模拟实跑环境(NO_PROXY=*)，严格判定\n")
    results = []
    for name, fn, to in TESTS:
        print(f"▶ 测试 {name}...", end=" ", flush=True)
        ok, detail, elapsed = _timed(fn, timeout=to)
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
        print("  排查: 代理绕过❌=NO_PROXY未生效；SSL❌=证书路径；其他=对应源故障")
    else:
        print("  全部出网点正常，可正常使用 bz scan / l / bz 等命令")
    print("=" * 70)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
