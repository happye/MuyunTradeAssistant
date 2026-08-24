"""数据源连通性体检工具（ISS-043）

一键批量测试所有金融数据源 + AI 接口的连通性，输出结构化结果。
用途：
1. 开发时调任何数据源前先 `check_all_sources()` 验证前提（落实 LRN-20260619-001）
2. 用户网络异常时自检定位「哪个源挂了」
3. REPL/CLI 命令 `bz --check` / `check sources` 调用展示

设计原则：
- **每个源独立探测，互不影响**：一个挂不影响其他源的测试
- **失败不抛异常**：所有异常捕获为 {ok:False, error, hint}
- **轻量**：每个源只发 1 个最小请求（如 Baostock 查 1 只行业、新浪拉 1 页）
- **绕代理**：与 MarketCache 一致，金融 API 直连更稳

返回结构：
    {
      "sources": [
        {"name": "Baostock-行业", "ok": True, "latency_ms": 120, "detail": "600989→C26化学...", "error": None, "hint": None},
        {"name": "新浪-全市场", "ok": False, "latency_ms": None, "detail": None, "error": "ConnectionError", "hint": "代理可能干扰"},
        ...
      ],
      "summary": {"total": N, "ok": M, "fail": K}
    }
"""

import base64
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# 公共 SSL 路径修复（v0.8.6.3，ISS-041/ISS-045）
# 根因：curl_cffi 的 libcurl 在 Windows 上无法处理含中文的 CA 证书路径
# （项目路径含「暮云思辨投资助手」），导致 stock_news_em 等接口 curl 77。
# 修复：把 certifi 证书复制到纯 ASCII 路径，设 CURL_CA_BUNDLE 等环境变量。
# 笨总 data_provider / news_client / 任何走 curl_cffi 的接口都应调用。
_CURL_SSL_FIXED = False
_WINDOWS_TLS_SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"


def _windows_cert_trusted_for_tls_server_auth(trust) -> bool:
    """Windows 证书仅接受通配信任或显式 TLS Server Auth EKU。"""
    if trust is True:
        return True
    if not trust:
        return False
    if isinstance(trust, str):
        return trust == _WINDOWS_TLS_SERVER_AUTH_OID
    try:
        return _WINDOWS_TLS_SERVER_AUTH_OID in trust
    except TypeError:
        return False


def _der_to_pem(der_bytes: bytes) -> str:
    b64 = base64.b64encode(der_bytes).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"


def _load_windows_cert_pems(enum_certificates) -> list[str]:
    """从 Windows ROOT/CA 证书库提取可用于 TLS 服务端校验的 PEM 证书。"""
    pem_blocks = []
    seen_der = set()
    for store in ("ROOT", "CA"):
        try:
            for der_bytes, encoding, trust in enum_certificates(store):
                if encoding != "x509_asn":
                    continue
                if not _windows_cert_trusted_for_tls_server_auth(trust):
                    continue
                if der_bytes in seen_der:
                    continue
                seen_der.add(der_bytes)
                pem_blocks.append(_der_to_pem(der_bytes))
        except Exception as exc:
            logger.debug("读取 Windows 证书 store %s 失败: %s", store, exc)
    return pem_blocks


def fix_curl_ssl_paths() -> bool:
    """一次性把 certifi 证书与 Windows 系统信任证书合并到 ASCII 路径并设环境变量。幂等。

    供所有走 akshare/curl_cffi 的模块在首次调用前调用。
    Returns: True 修复成功（或已修复）
    """
    global _CURL_SSL_FIXED
    if _CURL_SSL_FIXED:
        return True
    try:
        import certifi

        ascii_cert = Path.home() / ".muyun_cacert.pem"
        full_pem_content = Path(certifi.where()).read_text(encoding="utf-8")

        if sys.platform == "win32":
            import ssl

            win_pem_blocks = _load_windows_cert_pems(ssl.enum_certificates)
            if win_pem_blocks:
                full_pem_content = (
                    f"{full_pem_content.rstrip()}\n\n"
                    "# --- Windows System Certificates (TLS Server Auth) ---\n"
                    f"{''.join(win_pem_blocks)}"
                )

        if not ascii_cert.exists() or ascii_cert.read_text(encoding="utf-8") != full_pem_content:
            ascii_cert.write_text(full_pem_content, encoding="utf-8")

        for var in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            os.environ[var] = str(ascii_cert)
        _CURL_SSL_FIXED = True
        return True
    except Exception as e:
        logger.warning(f"curl SSL 路径修复失败（curl_cffi 接口可能 SSL 报错）: {e}")
        return False


def _disable_proxy():
    """清除 HTTP 代理环境变量（金融 API 直连更稳，与 MarketCache._without_proxy 一致）"""
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)


def _classify_error(err_str: str) -> str:
    """根据错误字符串归类错误类型，给可操作 hint"""
    s = err_str.lower()
    if "proxyerror" in s or "10057" in s or "代理" in err_str:
        return "代理干扰 | 提示：临时关系统代理(127.0.0.1:7890)"
    if "remotedisconnected" in s or "connection aborted" in s or "connectionreset" in s:
        return "服务端封禁/断开 | 提示：该接口可能被反爬，换备用源或稍后重试"
    if "timeout" in s or "timed out" in s:
        return "超时 | 提示：网络慢或服务端响应慢，稍后重试"
    if "ssl" in s or "certificate" in s or "curl: (77)" in s:
        return "SSL证书 | 提示：环境证书问题，设 CURL_CA_BUNDLE 或该接口降级 verify"
    if "404" in s and "model" in s:
        return "AI模型名错误 | 提示：检查 provider 与 model 名是否匹配（kimi≠deepseek-chat）"
    if "401" in s or "permission denied" in s or "invalid api key" in s:
        return "AI认证失败 | 提示：检查 settings.yaml 的 api_key 是否有效"
    if "login" in s or "登录失败" in err_str:
        return "Baostock登录失败 | 提示：Baostock服务端临时故障，等几分钟重试"
    return f"其他错误 | {err_str[:60]}"


def _probe(label: str, fn) -> dict:
    """通用探针包装：执行 fn，计时，捕获异常归类"""
    t0 = time.time()
    try:
        detail = fn()
        latency = int((time.time() - t0) * 1000)
        return {"name": label, "ok": True, "latency_ms": latency,
                "detail": detail, "error": None, "hint": None}
    except Exception as e:
        err_str = f"{type(e).__name__}: {str(e)[:120]}"
        hint = _classify_error(err_str)
        return {"name": label, "ok": False, "latency_ms": None,
                "detail": None, "error": err_str, "hint": hint}


def _probe_t(label: str, fn, timeout: float = 20.0) -> dict:
    """带超时的探针（报告2.2/3.3 新增 akshare/baostock 源用，防 hang）。

    akshare 底层 requests 无 timeout，股东户数/融资余额等接口卡住会拖垮整个体检。
    用 ThreadPoolExecutor 包硬超时，超时即 shutdown(wait=False) 放弃不等线程。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        detail = fut.result(timeout=timeout)
        ex.shutdown(wait=False)
        latency = int((time.time() - t0) * 1000)
        return {"name": label, "ok": True, "latency_ms": latency,
                "detail": detail, "error": None, "hint": None}
    except FuturesTimeout:
        ex.shutdown(wait=False)  # 不等孤儿线程，立刻返回
        return {"name": label, "ok": False, "latency_ms": None,
                "detail": None, "error": f"超时(>{timeout:.0f}s)，接口可能 hang/反爬",
                "hint": "超时 | 提示：网络慢或服务端响应慢，稍后重试"}
    except Exception as e:
        ex.shutdown(wait=False)
        err_str = f"{type(e).__name__}: {str(e)[:120]}"
        hint = _classify_error(err_str)
        return {"name": label, "ok": False, "latency_ms": None,
                "detail": None, "error": err_str, "hint": hint}


def check_all_sources(code: str = "600989") -> dict:
    """一键测试全部数据源连通性。

    Args:
        code: 测试用的股票代码（默认 600989 宝丰能源，非ETF非北交所）

    Returns:
        {sources: [...], summary: {total, ok, fail}}
    """
    _disable_proxy()
    fix_curl_ssl_paths()  # 报告2.2: em 端点（股东户数等）需 SSL 修复
    os.environ["TQDM_DISABLE"] = "1"
    sources = []

    # --- Baostock 系列 ---
    def _bs_industry():
        from src.data.akshare_client import _ensure_baostock_login, AKShareClient
        import baostock as bs
        if not _ensure_baostock_login():
            raise RuntimeError("Baostock 登录失败")
        prefix, _ = AKShareClient._normalize_stock_code(code)
        rs = bs.query_stock_industry(code=f"{prefix}.{code}")
        if rs.error_code != '0':
            raise RuntimeError(rs.error_msg)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            raise RuntimeError("行业查询返回空")
        f = rs.fields
        return f"{code} → {rows[0][f.index('industry')]}/{rows[0][f.index('code_name')]}"

    def _bs_kline():
        from src.data.akshare_client import AKShareClient
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        df = AKShareClient._fetch_baostock_kline(code, "daily", start, end)
        if df is None or df.empty:
            raise RuntimeError("K线返回空")
        return f"{len(df)} 条日K"

    def _bs_index():
        from src.data.akshare_client import AKShareClient
        info = AKShareClient._get_index_trend(use_cache=False)  # 体检测"此刻连通性"，不吃趋势缓存
        if info is None:
            raise RuntimeError("大盘趋势返回空")
        return f"沪深300 {info['trend']} close={info['close']}"

    sources.append(_probe("Baostock-行业分类", _bs_industry))
    sources.append(_probe("Baostock-历史K线", _bs_kline))
    sources.append(_probe("Baostock-大盘趋势", _bs_index))

    # --- 新浪全市场（复用 MarketCache） ---
    def _sina_market():
        from src.scanner.market_cache import MarketCache
        mc = MarketCache()
        df = mc.get_all_stocks(force_refresh=True)
        if df is None or df.empty:
            raise RuntimeError("新浪返回空")
        total = df["成交额"].sum() if "成交额" in df.columns else 0
        return f"{len(df)} 只，全市场成交额 {total/1e12:.2f} 万亿"

    sources.append(_probe("新浪-全市场快照", _sina_market))

    # --- 同花顺主营介绍 ---
    def _ths_intro():
        import akshare as ak
        df = ak.stock_zyjs_ths(symbol=code)
        if df is None or df.empty:
            raise RuntimeError("ths 返回空")
        return f"{len(df)} 行，列: {list(df.columns)[:3]}"

    sources.append(_probe("同花顺-主营介绍", _ths_intro))

    # --- 东方财富新闻（已知常 SSL/反爬失败） ---
    def _em_news():
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            raise RuntimeError("em 新闻返回空")
        return f"{len(df)} 条新闻"

    sources.append(_probe("东方财富-个股新闻(em)", _em_news))

    # --- 报告2.2 新增：akshare 股东户数（笨总教学八筹码分散信号） ---
    def _ak_gdhs():
        import akshare as ak
        # 注意：stock_zh_a_gdhs 参数是日期(YYYYMMDD)非代码，会 hang；用 detail_em(symbol=代码)
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is None or df.empty:
            raise RuntimeError("股东户数返回空")
        cur_col = next((c for c in df.columns if "本次" in c), None)
        return f"{len(df)}期，列={list(df.columns)[:5]}，最新{df.iloc[0][cur_col] if cur_col else '?'}"

    sources.append(_probe_t("akshare-股东户数", _ak_gdhs, timeout=25))

    # --- 报告2.2 新增：akshare 融资余额（笨总教学八杠杆踩踏信号） ---
    def _ak_margin():
        import akshare as ak
        from datetime import datetime, timedelta
        # API 按日期返回全市场融资余额（非按个股），逐日查+过滤代码
        for days_ago in range(0, 5):
            d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
            try:
                if code.startswith("6"):
                    df = ak.stock_margin_detail_sse(date=d)
                else:
                    df = ak.stock_margin_detail_szse(date=d)
            except Exception:
                continue
            if df is not None and not df.empty:
                mcol = next((c for c in df.columns if "融资余额" in str(c)), None)
                # 看测试股是否在该日数据里
                code_col = next((c for c in df.columns if "代码" in str(c) or "code" in str(c).lower()), None)
                hit = ""
                if code_col is not None:
                    hit = f"，{code}{'在' if (df[code_col].astype(str)==code).any() else '不在'}数据中"
                return f"{d}: {len(df)}行，融资余额列={mcol}{hit}"
        raise RuntimeError("近5日融资余额全空（可能非交易日或接口反爬）")

    sources.append(_probe_t("akshare-融资余额", _ak_margin, timeout=25))

    # --- 报告2.1 新增：baostock 旗手近20日涨幅（笨总教学八旗手滞涨信号） ---
    def _bs_flagbearer():
        from src.core.exit_signals.sector import _fetch_20d_change_pct
        # 用一只已知龙头测试（茅台 600519，流动性好必有数据）
        chg = _fetch_20d_change_pct("600519")
        if chg is None:
            raise RuntimeError("旗手20日涨幅返回 None（baostock K线查询失败）")
        return f"600519 近20日涨幅 {chg:.2f}%"

    sources.append(_probe_t("Baostock-旗手20日涨幅", _bs_flagbearer, timeout=25))

    # --- 报告3.3 新增：新浪市场宽度（涨跌家数比，笨总通杀/分化/普涨） ---
    def _sina_breadth():
        from src.core.exit_signals.macro import assess_market_breadth
        r = assess_market_breadth()
        if r is None:
            raise RuntimeError("市场宽度返回 None（MarketCache 涨跌幅列缺失或样本不足）")
        return f"[{r[0]}] {r[1]}"

    sources.append(_probe_t("新浪-市场宽度", _sina_breadth, timeout=25))

    # --- AI 接口（deepseek + kimi，轻量探针） ---
    def _ai_probe(provider: str):
        from src.cli.main import load_config
        cfg = load_config()
        ai_cfg = cfg.get("ai", {})
        prov_cfg = ai_cfg.get(provider, {})
        api_key = prov_cfg.get("api_key", "")
        base_url = prov_cfg.get("base_url")
        model = prov_cfg.get("model", "")
        if not api_key:
            raise RuntimeError(f"{provider} api_key 未配置")
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复一个字：通"}],
            max_tokens=10, temperature=0,
            **({"extra_body": {"thinking": {"type": "disabled"}}} if str(model).startswith("deepseek-v4") else {}),
        )
        txt = (resp.choices[0].message.content or "")[:20]
        return f"model={model} resp='{txt}'"

    sources.append(_probe("AI-DeepSeek", lambda: _ai_probe("deepseek")))
    sources.append(_probe("AI-Kimi", lambda: _ai_probe("kimi")))

    # --- 预期事件日历数据源（v0.8.7 预期管理 Phase 1） ---
    def _cal_bond():
        from src.data.calendar_client import CalendarClient
        rates = CalendarClient.get_cn_10y_rate_series(years=3)
        if not rates:
            raise RuntimeError("国债收益率返回空")
        return f"10Y国债 {len(rates)} 条，最新 {rates[-1]:.4f}%"

    def _cal_index():
        from src.data.calendar_client import CalendarClient
        closes = CalendarClient.get_index_close_history(years=3)
        if not closes:
            raise RuntimeError("沪深300日K返回空")
        return f"沪深300 {len(closes)} 条，最新 {closes[-1]:.2f}"

    def _cal_disclosure():
        from src.data.calendar_client import CalendarClient, current_disclosure_periods
        periods = current_disclosure_periods()
        if not periods:
            raise RuntimeError("无当前财报期")
        events = CalendarClient.get_stock_disclosure(periods[0])
        if not events:
            raise RuntimeError(f"{periods[0]} 披露日历返回空")
        return f"{periods[0]} {len(events)} 条预约披露"

    sources.append(_probe("日历-国债收益率", _cal_bond))
    sources.append(_probe("日历-沪深300日K", _cal_index))
    sources.append(_probe("日历-财报披露", _cal_disclosure))

    ok_count = sum(1 for s in sources if s["ok"])
    return {
        "sources": sources,
        "summary": {"total": len(sources), "ok": ok_count, "fail": len(sources) - ok_count},
    }


def format_report(result: dict) -> str:
    """把 check_all_sources 结果格式化为可读文本（用于 CLI 展示）"""
    lines = []
    lines.append("=" * 64)
    lines.append("  🔌 数据源连通性体检")
    lines.append("=" * 64)
    for s in result["sources"]:
        mark = "✅" if s["ok"] else "❌"
        lat = f" {s['latency_ms']}ms" if s["latency_ms"] is not None else ""
        lines.append(f"  {mark} {s['name']:<22}{lat}")
        if s["ok"]:
            if s["detail"]:
                lines.append(f"      {s['detail']}")
        else:
            lines.append(f"      错误: {s['error']}")
            lines.append(f"      {s['hint']}")
    sm = result["summary"]
    lines.append("-" * 64)
    lines.append(f"  合计: {sm['total']} 个源 | ✅ {sm['ok']} 通 | ❌ {sm['fail']} 挂")
    lines.append("=" * 64)
    return "\n".join(lines)
