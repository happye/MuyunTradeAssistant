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
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 模拟实跑环境（和 start.py / src/cli/main.py 启动逻辑一致）
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"      # 关键：和实跑一致，强制直连
os.environ["no_proxy"] = "*"
os.environ["TQDM_DISABLE"] = "1"
os.environ["PYTHONUTF8"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.data.source_check import fix_curl_ssl_paths

fix_curl_ssl_paths()

_RESULT_PREFIX = "__MUYUN_TEST_RESULT__="


def _format_exception(exc: Exception) -> str:
    causes = []
    current = exc
    while current is not None and len(causes) < 4:
        causes.append(f"{type(current).__name__}: {str(current)}")
        current = current.__cause__ or current.__context__
    return " <- ".join(causes)[:240]


def _timed(test_index: int, timeout=30):
    """在独立进程运行单项测试，超时可真正终止第三方库中的卡死线程。"""
    t0 = time.time()
    try:
        completed = subprocess.run(
            [sys.executable, __file__, "--single", str(test_index)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
        result_line = next(
            (line for line in reversed(completed.stdout.splitlines())
             if line.startswith(_RESULT_PREFIX)),
            None,
        )
        if result_line is None:
            detail = (completed.stderr or completed.stdout or "子进程无结果")[-240:]
            return False, detail.strip(), time.time() - t0
        result = json.loads(result_line[len(_RESULT_PREFIX):])
        return bool(result["ok"]), str(result["detail"]), time.time() - t0
    except subprocess.TimeoutExpired:
        return False, f"超时(>{timeout}s)", time.time() - t0
    except Exception as e:
        return False, _format_exception(e), time.time() - t0


# ========== 代理绕过验证（治本核心）==========
def test_proxy_bypass():
    """验证 NO_PROXY=* 能绕过代理env：直接查 requests 代理解析，不依赖外网可达性。

    不能用 sina 做可达性靶子——sina nginx 自带反爬会间歇返回 456「拒绝访问」，
    那是服务端反爬不是代理问题，会误判 NO_PROXY。这里直接验证 bypass 机制本身。
    """
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    try:
        import requests.utils
        proxies = requests.utils.get_environ_proxies(
            "https://vip.stock.finance.sina.com.cn/")
        ok = proxies == {}
        return ok, f"NO_PROXY=* bypass {'生效(直连)' if ok else '未生效(走代理)'} {proxies}"
    finally:
        # 测完清掉，不影响后续测试
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


# ========== AI 调用 ==========
def test_deepseek():
    from src.cli.main import load_config
    from openai import OpenAI
    deepseek = load_config().get("ai", {}).get("deepseek", {})
    if not deepseek.get("api_key"):
        return False, "DeepSeek key 未配，跳过"
    model = deepseek.get("model", "deepseek-v4-flash")
    client = OpenAI(
        api_key=deepseek["api_key"],
        base_url=deepseek.get("base_url"),
        timeout=30,
        max_retries=1,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "回复OK"}],
        max_tokens=5,
        **({"extra_body": {"thinking": {"type": "disabled"}}}
           if str(model).startswith("deepseek-v4") else {}),
    )
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
    if ok:
        return True, f"全市场 {len(df)} 只"
    return False, "全市场 0 只 (sina外源间歇456反爬/东财断连,非代码bug,重试可恢复)"


def test_market_turnover():
    from src.core.benzong.data_provider import get_market_turnover
    t = get_market_turnover()
    if t is not None:
        return True, f"成交额 {t} 万亿"
    return False, "成交额获取失败 (依赖sina全市场快照,外源间歇,非代码bug)"


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


def _run_single(test_index: int) -> int:
    _, fn, _ = TESTS[test_index]
    try:
        result = fn()
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            ok, detail = result
        else:
            ok, detail = True, str(result)[:60]
    except Exception as exc:
        ok, detail = False, _format_exception(exc)
    print(_RESULT_PREFIX + json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
    return 0


def main():
    print("=" * 70)
    print("  全数据源一键连通性测试（ISS-049 彻底版）")
    print("=" * 70)
    print(f"  共 {len(TESTS)} 个出网点，模拟实跑环境(NO_PROXY=*)，严格判定\n")
    results = []
    for test_index, (name, _, to) in enumerate(TESTS):
        print(f"▶ 测试 {name}...", end=" ", flush=True)
        ok, detail, elapsed = _timed(test_index, timeout=to)
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
        print("  排查: sina❌=新浪外源反爬(456)/间歇,非代码bug,重试可恢复；SSL❌=证书路径；其他=对应源故障")
    else:
        print("  全部出网点正常，可正常使用 bz scan / l / bz 等命令")
    print("=" * 70)
    return 0 if not fail else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--single":
        sys.exit(_run_single(int(sys.argv[2])))
    sys.exit(main())
