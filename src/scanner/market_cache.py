"""全市场行情数据缓存 - Scanner基础设施

核心职责：
1. 获取全市场A股/ETF行情数据（efinance批量接口）
2. 内存缓存 + TTL过期策略
3. 行业板块 / 概念板块数据缓存
4. 盘中/盘后/非交易日区分

数据源（v0.8.0 Phase 2 更新）：
- 全市场A股: efinance.stock.get_realtime_quotes() ~0.4s（替代AKShare爬虫）
- ETF/行业板块: 仍用AKShare（efinance无对应接口）

设计约束：
- efinance 批量API 0.4秒获取5800+只A股，稳定可靠
- 盘中行情变化快，TTL=5min
- 盘后数据不变，长缓存至次日开盘
"""

import logging
import os
import time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_SINA_MARKET_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_LOCAL_NETWORK_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "network.local.yaml"


def _get_sina_market_url() -> str:
    """解析新浪行情地址：共享默认 HTTPS，本机可用 gitignored 配置覆盖。"""
    env_url = os.environ.get("MUYUN_SINA_MARKET_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")

    try:
        import yaml

        if _LOCAL_NETWORK_CONFIG.exists():
            local = yaml.safe_load(_LOCAL_NETWORK_CONFIG.read_text(encoding="utf-8")) or {}
            local_url = str(local.get("network", {}).get("sina_market_url", "")).strip()
            if local_url.startswith(("http://", "https://")):
                return local_url.rstrip("/")
            if local_url:
                logger.warning("MarketCache: 忽略无效的本机 sina_market_url（仅支持 HTTP/HTTPS）")
    except Exception as exc:
        logger.warning("MarketCache: 读取本机网络配置失败，使用共享 HTTPS 默认值: %s", exc)

    return _DEFAULT_SINA_MARKET_URL


def _retry_akshare(func, max_retries=3, base_delay=1.0):
    """AKShare API调用重试装饰器（指数退避）

    AKShare偶尔返回 Connection aborted 等网络错误，
    此装饰器提供最多3次重试，间隔 1s/2s/4s。

    注意：RemoteDisconnected（服务器 TCP 层直接断开）通常是服务端封禁，
    多次重试不会有效，但保留重试逻辑以兼容偶发性网络抖动。
    """
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                # RemoteDisconnected 是服务端主动断开，无需多次重试，减少等待
                exc_str = str(exc)
                if "RemoteDisconnected" in exc_str or "Connection aborted" in exc_str:
                    if attempt == 0:
                        logger.warning(
                            f"AKShare API调用失败(第{attempt+1}次): {exc}，"
                            f"1s后重试（RemoteDisconnected，可能为服务端封禁）..."
                        )
                        time.sleep(1.0)
                        continue
                    else:
                        break  # RemoteDisconnected 第二次还失败，直接放弃
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"AKShare API调用失败(第{attempt+1}次): {exc}，"
                        f"{delay:.0f}s后重试..."
                    )
                    time.sleep(delay)
        raise last_exc
    return wrapper


# efinance原始列名 → 标准化列名（与原AKShare列名一致，下游无需改动）
_EFINANCE_COLUMN_MAP = {
    "股票代码": "代码",
    "股票名称": "名称",
    "最新价": "最新价",
    "涨跌幅": "涨跌幅",
    "涨跌额": "涨跌额",
    "成交量": "成交量",
    "成交额": "成交额",
    "最高": "最高",
    "最低": "最低",
    "今开": "今开",
    "昨日收盘": "昨收",
    "量比": "量比",
    "换手率": "换手率",
    "动态市盈率": "市盈率-动态",
    "总市值": "总市值",
    "流通市值": "流通市值",
}


class MarketCache:
    """全市场行情数据缓存

    使用场景：Scanner初筛需要全市场行情数据。

    缓存策略：
    - L1 全市场A股: efinance.stock.get_realtime_quotes() ~0.4s, 盘中5min/盘后至次日
    - L1 全市场ETF: ak.fund_etf_spot_em() ~5s, 同上
    - L2 行业板块列表: ak.stock_board_industry_name_em() ~3s, 10min
    - L2 行业成分股: ak.stock_board_industry_cons_em() ~1s/行业, 30min
    - L2 概念板块列表: ak.stock_board_concept_name_em() ~3s, 10min
    - L2 概念成分股: ak.stock_board_concept_cons_em() ~1s/概念, 30min
    """

    # 默认盘中缓存TTL（秒）
    DEFAULT_TRADING_TTL = 300  # 5分钟

    # ── L1 全市场快照：类级共享（v0.8.7.2 修复）──
    # 历史上是实例属性，而 macro.assess_market_breadth / benzong.data_provider 等调用方
    # 每次 `MarketCache()` 新建实例 → 实例级 TTL 永不命中 → 每次 l/la 都对新浪做
    # 73 页全量分页拉取（高频使用触发反爬限流："新浪间歇失效+东财断连"，连带单股
    # 行情一起挂）。改为类级后，所有实例共享同一份快照与 TTL，无论谁 new 实例。
    # 线程安全：无锁，并发最坏情况是多拉一次快照，无害。
    _shared_stock_df: Optional[pd.DataFrame] = None
    _shared_stock_ts: float = 0.0
    _shared_stock_count: int = 0
    # 失败冷却：双源全失败且无旧缓存可兜底时，60 秒内不再重试
    # （被限流后继续猛打会延长封禁；60 秒足够短，不影响真实恢复场景）
    _shared_fail_ts: float = 0.0
    FAIL_COOLDOWN_SECONDS = 60

    # ── L1(ETF) 与 L2 板块类缓存：同样类级共享（v0.8.7.2 补齐，与 L1 快照同款病灶修复）──
    # 使用方（CLI 每条 scan/industries/concepts 命令新建 ScannerEngine→MarketCache）反复新建
    # 实例会让实例级 TTL 永不命中；其中成分股接口是 THS HTML 翻页爬虫（全库最贵端点、反爬敏感），
    # 同主题词连发两条命令本会整页重爬。类级化后跨命令跨实例共享。
    _shared_etf_df: Optional[pd.DataFrame] = None
    _shared_etf_ts: float = 0.0
    _shared_etf_count: int = 0
    _shared_industry_df: Optional[pd.DataFrame] = None
    _shared_industry_ts: float = 0.0
    _shared_concept_df: Optional[pd.DataFrame] = None
    _shared_concept_ts: float = 0.0
    _shared_industry_stocks: dict = {}
    _shared_industry_stocks_ts: dict = {}
    INDUSTRY_STOCKS_TTL = 1800  # 30分钟
    _shared_concept_stocks: dict = {}
    _shared_concept_stocks_ts: dict = {}
    CONCEPT_STOCKS_TTL = 1800   # 30分钟

    def __init__(self, ttl_seconds: int = 0):
        """初始化缓存

        Args:
            ttl_seconds: 盘中缓存TTL（秒），0使用默认值300

        注意：L1 全市场快照缓存在类级共享（见类属性注释），本构造器只设置本实例的 TTL 判定。
        """
        self._ttl = ttl_seconds or self.DEFAULT_TRADING_TTL
        # 源失效标记：第一条规则发现源失效后设True，后续规则不重试(避免--allrules刷4×3=12条warning)
        self._market_source_dead: bool = False
        self._market_source_dead_ts: float = 0.0

        # L1(ETF) 与 L2(行业/概念板块及成分股) 缓存已全部提升为类属性 _shared_*（见类头部）

    @staticmethod
    def _without_proxy():
        """返回一个上下文管理器，临时清除HTTP代理环境变量

        国内金融数据API（东方财富等）直连即可，走代理反而会导致：
        1. 代理无法正确转发这些请求（返回空响应/JSON解析失败）
        2. 代理增加延迟（原本0.4s的请求变4min）
        3. 代理IP可能被金融网站封禁

        用法：
            with MarketCache._without_proxy():
                df = ef.stock.get_realtime_quotes()
        """
        import contextlib

        @contextlib.contextmanager
        def _proxy_disabled():
            proxy_keys = [
                "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy",
            ]
            saved = {}
            for key in proxy_keys:
                if key in __import__("os").environ:
                    saved[key] = __import__("os").environ.pop(key)
            try:
                yield
            finally:
                __import__("os").environ.update(saved)

        return _proxy_disabled()

    @staticmethod
    def _snapshot_quality_ok(df) -> bool:
        """快照质量校验（v0.8.7.5 审计修复 A08：原判定条件反向——无"最新价"列时
        prices=None 反而放行写入缓存，残缺全市场快照被当成功缓存）。

        - 有"最新价"列：有效价格行数必须 >10，全零/近全零视为降级快照拒绝入缓存
        - 无"最新价"列：小表(<=100行)放行，兼容只含最小字段的测试替身
          （LRN-20260825-001）；大表缺价格列=残缺快照，一律拒绝（真实全市场快照必有该列）
        """
        if "最新价" in df.columns:
            prices = pd.to_numeric(df["最新价"], errors="coerce").fillna(0)
            return (prices > 0).sum() > 10
        return len(df) <= 100

    def _accept_snapshot(self, df, source_label: str) -> bool:
        """质量校验通过则写入类级共享缓存并返回 True，否则 False。"""
        if not self._snapshot_quality_ok(df):
            logger.warning(f"MarketCache: {source_label}返回无效行情(全零或缺价格列)，拒绝写入缓存")
            return False
        MarketCache._shared_stock_df = df
        MarketCache._shared_stock_ts = time.time()
        MarketCache._shared_stock_count = len(df)
        MarketCache._shared_fail_ts = 0.0
        logger.info(f"MarketCache: A股行情获取成功({len(df)}只, {source_label})")
        return True

    def get_all_stocks(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取全市场A股行情（带缓存+重试）

        数据源优先级：
        1. 新浪财经API（并行分页，~0.3秒，字段齐全含市净率）
        2. efinance（东方财富API，备用，无市净率）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            全市场行情DataFrame（列名标准化），空DataFrame表示获取失败
        """
        # 禁用AKShare/efinance内部的tqdm进度条
        import os
        os.environ["TQDM_DISABLE"] = "1"

        if not force_refresh and MarketCache._shared_stock_df is not None and not self._is_expired(MarketCache._shared_stock_ts):
            logger.info(f"MarketCache: A股缓存命中({MarketCache._shared_stock_count}只)")
            return MarketCache._shared_stock_df

        # 失败冷却：刚经历过双源全失败（且当时无旧缓存兜底），短暂歇手再试，
        # 防止被限流状态下继续高频全量拉取延长封禁
        if not force_refresh and (time.time() - MarketCache._shared_fail_ts) < MarketCache.FAIL_COOLDOWN_SECONDS:
            logger.warning("MarketCache: 双源失败冷却中(60秒内)，本次跳过全市场拉取")
            return pd.DataFrame()

        # 主数据源：新浪财经API（偶发返回空，重试2次；连续3次空才判源失效）
        import time as _t
        df = None
        for attempt in range(3):
            df = self._fetch_sina_market()
            if df is not None and not df.empty:
                break
            if attempt < 2:
                _t.sleep(1.5)  # 偶发空，等1.5秒重试
        if df is not None and not df.empty:
            if self._accept_snapshot(df, f"新浪源, 第{attempt+1}次"):
                return df

        # 新浪3次都空 → 试 efinance 兜底（不锁死，每次都试，避免偶发失败放大）
        df = self._fetch_efinance_market()
        if df is not None and not df.empty:
            if self._accept_snapshot(df, "efinance备用"):
                return df

        # 全部失败：返回过期缓存兜底，不标记锁死（下次命令仍可重试，因新浪是间歇性失效）
        if MarketCache._shared_stock_df is not None:
            logger.info("MarketCache: 新浪+东财均失败，用过期缓存兜底(新浪间歇性失效，下次可重试)")
            return MarketCache._shared_stock_df
        # 无旧缓存可兜底：记失败时间戳进入冷却，60 秒内的后续调用直接返回空不再打源
        MarketCache._shared_fail_ts = time.time()
        logger.warning("MarketCache: 全市场行情源均失败(新浪间歇失效+东财断连)，改用主题词/单股模式")
        return pd.DataFrame()

    def _sina_source_dead(self) -> bool:
        """检测新浪全市场源是否失效（接口返回HTML/非200=失效，非偶发空）。"""
        try:
            import requests as _requests
            with self._without_proxy():
                r = _requests.get(
                    _get_sina_market_url(),
                    params={"page": 1, "num": 5, "sort": "changepercent", "asc": 0, "node": "hs_a", "symbol": "", "_s_r_a": "init"},
                    timeout=8, headers={"User-Agent": "Mozilla/5.0"},
                )
            # 返回HTML或非200=源失效（接口废弃/被封）
            if r.status_code != 200 or "html" in (r.headers.get("content-type", "") + r.text[:50]).lower():
                return True
            return False
        except Exception:
            return False  # 网络异常不算源失效（可能偶发）

    def _fetch_sina_market(self) -> Optional[pd.DataFrame]:
        """通过新浪财经API获取全市场A股行情（并行分页）

        接口：vip.stock.finance.sina.com.cn Market_Center.getHQNodeData
        特点：每页最多80条，并行获取约73页，总计0.3-5秒
        字段齐全：涨跌幅、换手率、市盈率、市净率、总市值等

        Returns:
            标准化后的DataFrame，失败返回None
        """
        import requests as _requests
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

        base_url = _get_sina_market_url()
        COMMON_PARAMS = "&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=init"
        HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        PAGE_SIZE = 80

        # 新浪API不走代理（直连更稳定）
        session = _requests.Session()
        session.trust_env = False
        session.proxies = {"http": None, "https": None}

        def _fetch_page(page: int) -> list:
            try:
                resp = session.get(
                    f"{base_url}?page={page}&num={PAGE_SIZE}{COMMON_PARAMS}",
                    timeout=30, headers=HEADERS,
                )
                if resp.status_code == 200:
                    return resp.json() or []
            except Exception:
                pass
            return []

        # 估算总页数（A股约5800只，每页80）
        total_pages = 73
        all_stocks = []

        try:
            # v0.8.7.5 审计修复 A41：不能用 with 块（shutdown(wait=True) 会 join 卡死线程），
            # 且 as_completed 必须带总超时——73页×30s÷10并发最坏240s冻结，
            # 与 data_provider.py 全市场拉取 45s 预算对齐。
            executor = ThreadPoolExecutor(max_workers=10)
            try:
                futures = {executor.submit(_fetch_page, p): p for p in range(1, total_pages + 1)}
                for future in as_completed(futures, timeout=45):
                    result = future.result()
                    all_stocks.extend(result)
            except FuturesTimeoutError:
                # v0.8.7.9 裁决（E 轮，原 R4 延后项 D-RG-2）：超时丢弃部分页是**特性**——
                # _snapshot_quality_ok 只查">10 行有效价"，40/73 页的部分快照会通过校验
                # 被当全市场快照缓存，静默偏置量比/涨幅排名；换源重拉才保完整性。
                _done = sum(1 for f in futures if f.done())
                logger.error(
                    f"MarketCache: 新浪API并行获取超时(45s)，放弃剩余页"
                    f"(已完成{_done}/{total_pages}页，将换efinance源重拉)"
                )
                return None
            except Exception as e:
                logger.error(f"MarketCache: 新浪API并行获取异常: {e}")
                return None
            finally:
                executor.shutdown(wait=False)
        except Exception as e:
            logger.error(f"MarketCache: 新浪API并行获取异常: {e}")
            return None

        if not all_stocks:
            logger.debug("MarketCache: 新浪API返回空数据")
            return None

        # 去重
        seen = set()
        unique = []
        for s in all_stocks:
            code = s.get("code", "")
            if code and code not in seen:
                seen.add(code)
                unique.append(s)

        # 转为DataFrame并标准化列名
        df = pd.DataFrame(unique)
        df = self._normalize_sina_columns(df)
        return df

    @staticmethod
    def _normalize_sina_columns(df: pd.DataFrame) -> pd.DataFrame:
        """将新浪API原始字段名标准化为AKShare兼容格式

        新浪字段 → 标准列名：
        code→代码, name→名称, trade→最新价, changepercent→涨跌幅,
        settlement→昨收, open→今开, high→最高, low→最低,
        volume→成交量, amount→成交额, turnoverratio→换手率,
        per→市盈率-动态, pb→市净率, mktcap→总市值, nmc→流通市值

        同时计算：振幅 = (最高 - 最低) / 昨收 * 100
        """
        # 字段映射
        rename_map = {
            "code": "代码", "name": "名称", "trade": "最新价",
            "changepercent": "涨跌幅", "settlement": "昨收",
            "open": "今开", "high": "最高", "low": "最低",
            "volume": "成交量", "amount": "成交额",
            "turnoverratio": "换手率", "per": "市盈率-动态",
            "pb": "市净率", "mktcap": "总市值", "nmc": "流通市值",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 保留标准列
        keep_cols = [c for c in df.columns if c in set(rename_map.values()) | {"振幅"}]

        # 计算振幅
        if "最高" in df.columns and "最低" in df.columns and "昨收" in df.columns:
            high = pd.to_numeric(df["最高"], errors="coerce")
            low = pd.to_numeric(df["最低"], errors="coerce")
            prev_close = pd.to_numeric(df["昨收"], errors="coerce")
            df["振幅"] = ((high - low) / prev_close * 100).round(2)
            keep_cols.append("振幅")

        # 市值单位：新浪返回万元，转为元（与AKShare/efinance一致）
        for col in ["总市值", "流通市值"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") * 10000

        # 涨跌幅：Sina非交易时段返回0；如全为0则本地用(trade-settlement)/settlement计算
        if "涨跌幅" in df.columns and "最新价" in df.columns and "昨收" in df.columns:
            change = pd.to_numeric(df["涨跌幅"], errors="coerce")
            if change.fillna(0).abs().gt(1e-9).sum() == 0:
                latest = pd.to_numeric(df["最新价"], errors="coerce")
                prev = pd.to_numeric(df["昨收"], errors="coerce")
                mask = (latest > 1e-9) & (prev > 1e-9)
                computed = change.copy()
                computed[mask] = ((latest[mask] - prev[mask]) / prev[mask] * 100).round(2)
                df["涨跌幅"] = computed
                logger.info("MarketCache: Sina changepercent全为0，改用本地计算涨跌幅")

        # 只保留标准列
        df = df[[c for c in keep_cols if c in df.columns]]

        return df

    def _fetch_efinance_market(self) -> Optional[pd.DataFrame]:
        """通过efinance获取全市场行情（备用数据源）

        efinance使用东方财富API，无市净率字段。
        东财 push2.eastmoney.com 偶发 RemoteDisconnected（反爬/服务端断连），
        失败一次即放弃（不刷屏重试），降级走过期缓存兜底。

        Returns:
            标准化后的DataFrame，失败返回None
        """
        try:
            import efinance as ef
            # 静音 efinance 内部 urllib3 分页重试刷屏（RemoteDisconnected 时它会重试4次刷屏）
            import logging as _log
            _log.getLogger("urllib3").setLevel(_log.CRITICAL)
            with self._without_proxy():
                df = ef.stock.get_realtime_quotes()
            if df is not None and not df.empty:
                return self._normalize_efinance_columns(df)
        except Exception as e:
            logger.debug(f"MarketCache: efinance备用源失败({type(e).__name__})，走过期缓存兜底")
        return None

    @staticmethod
    def _normalize_efinance_columns(df: pd.DataFrame) -> pd.DataFrame:
        """将efinance原始列名标准化为AKShare兼容格式

        同时：
        1. 重命名列（按_EFINANCE_COLUMN_MAP）
        2. 计算振幅 = (最高 - 最低) / 昨收 * 100
        3. 将 '-' 字符串替换为 NaN（efinance用'-'表示无效值）
        4. 删除不需要的efinance特有列

        Args:
            df: efinance原始DataFrame

        Returns:
            标准化后的DataFrame
        """
        # Step 1: 将 '-' 替换为 NaN（efinance的空值标记）
        df = df.replace("-", pd.NA)

        # Step 2: 计算振幅（efinance无此字段，需手动算）
        if "最高" in df.columns and "最低" in df.columns and "昨日收盘" in df.columns:
            high = pd.to_numeric(df["最高"], errors="coerce")
            low = pd.to_numeric(df["最低"], errors="coerce")
            prev_close = pd.to_numeric(df["昨日收盘"], errors="coerce")
            df["振幅"] = ((high - low) / prev_close * 100).round(2)

        # Step 3: 重命名列
        rename_map = {k: v for k, v in _EFINANCE_COLUMN_MAP.items() if k in df.columns}
        df = df.rename(columns=rename_map)

        # Step 4: 保留下游需要的列（删除efinance特有列如"行情ID"、"市场类型"等）
        keep_cols = [c for c in df.columns if c in set(_EFINANCE_COLUMN_MAP.values()) | {"振幅"}]
        df = df[keep_cols]

        return df

    def get_all_etfs(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取全市场ETF行情（带缓存）

        数据源: ak.fund_etf_spot_em()
        约5秒完成。

        Args:
            force_refresh: 是否强制刷新

        Returns:
            ETF行情DataFrame
        """
        if not force_refresh and MarketCache._shared_etf_df is not None and not self._is_expired(MarketCache._shared_etf_ts):
            logger.info(f"MarketCache: ETF缓存命中({MarketCache._shared_etf_count}只)")
            return MarketCache._shared_etf_df

        logger.info("MarketCache: 获取全市场ETF行情...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                MarketCache._shared_etf_df = df
                MarketCache._shared_etf_ts = time.time()
                MarketCache._shared_etf_count = len(df)
                logger.info(f"MarketCache: ETF行情获取成功({MarketCache._shared_etf_count}只)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: ETF行情获取失败: {e}")
            if MarketCache._shared_etf_df is not None:
                return MarketCache._shared_etf_df
            return pd.DataFrame()

    def _get_ths_board_stocks_by_name(self, board_name: str, board_type: str) -> list:
        """通过同花顺HTML翻页获取板块成分股代码列表（THS备用数据源）

        Args:
            board_name: 板块名称（如"半导体"）
            board_type: "industry"（行业板块）或"concept"（概念板块）

        Returns:
            股票代码列表（如["688981", "002049", ...]），失败返回[]
        """
        import requests
        import random
        import py_mini_racer
        from bs4 import BeautifulSoup
        from io import StringIO
        from akshare.datasets import get_ths_js
        import akshare as ak

        if board_type == "industry":
            board_list = ak.stock_board_industry_name_ths()
            base_url = "https://q.10jqka.com.cn/thshy/detail/code/"
        else:
            board_list = ak.stock_board_concept_name_ths()
            base_url = "https://q.10jqka.com.cn/gn/detail/code/"

        row = board_list[board_list["name"] == board_name]
        if row.empty:
            logger.warning(f"MarketCache: THS板块未找到: {board_name}")
            return []

        code = str(row.iloc[0]["code"])

        # 生成 THS v_code 认证 cookie（与 AKShare 内置 THS 函数使用相同机制）
        js_racer = py_mini_racer.MiniRacer()
        with open(get_ths_js("ths.js"), encoding="utf-8") as f:
            js_content = f.read()
        js_racer.eval(js_content)

        def _fresh_v_code() -> str:
            return js_racer.call("v")

        def _make_headers(v: str) -> dict:
            """构造拟人化请求头，降低被识别为爬虫的概率"""
            return {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Cookie": f"v={v}",
                "Referer": f"https://q.10jqka.com.cn/",
            }

        v_code = _fresh_v_code()
        all_codes = []
        max_pages = 8  # 每页20只，最多获取160只

        for page in range(1, max_pages + 1):
            # 页间随机延迟，避免过快请求触发限速（测试发现第6页后可能拿不到表格）
            if page > 1:
                time.sleep(random.uniform(0.4, 0.9))

            url = base_url + code + "/" if page == 1 else base_url + code + "/page/" + str(page) + "/"
            try:
                r = requests.get(url, headers=_make_headers(v_code), timeout=12)
                if r.status_code != 200:
                    logger.debug(f"MarketCache: THS第{page}页HTTP {r.status_code}，停止翻页")
                    break

                soup = BeautifulSoup(r.text, "lxml")
                table = soup.find("table", class_="m-table")

                if not table:
                    # 表格消失通常是限速触发——刷新 v_code 后重试一次
                    logger.debug(f"MarketCache: THS第{page}页未返回表格，刷新v_code重试...")
                    v_code = _fresh_v_code()
                    time.sleep(random.uniform(1.0, 2.0))
                    r2 = requests.get(url, headers=_make_headers(v_code), timeout=12)
                    if r2.status_code == 200:
                        soup = BeautifulSoup(r2.text, "lxml")
                        table = soup.find("table", class_="m-table")
                    if not table:
                        logger.debug(f"MarketCache: THS重试后仍无表格，停止翻页于第{page}页")
                        break

                df_page = pd.read_html(StringIO(str(table)))[0]
                if df_page.empty or "代码" not in df_page.columns:
                    break
                page_codes = df_page["代码"].astype(str).str.zfill(6).tolist()
                all_codes.extend(page_codes)

                if page == 1:
                    page_info = soup.find("span", class_="page_info")
                    if page_info:
                        total_pages = int(page_info.text.split("/")[1])
                        max_pages = min(max_pages, total_pages)
                    else:
                        break  # 无分页信息，单页结束

            except Exception as e:
                logger.debug(f"MarketCache: THS成分股第{page}页获取失败: {e}")
                break

        return all_codes

    def get_industry_boards(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取行业板块列表（带缓存）

        主数据源: ak.stock_board_industry_name_em()（东方财富）
        备用数据源: ak.stock_board_industry_name_ths()（同花顺）

        Args:
            force_refresh: 是否强制刷新

        Returns:
            行业板块DataFrame（含"板块名称"列）
        """
        ttl = 600  # 行业板块10分钟TTL
        if (not force_refresh
                and MarketCache._shared_industry_df is not None
                and (time.time() - MarketCache._shared_industry_ts) < ttl):
            logger.info("MarketCache: 行业板块缓存命中")
            return MarketCache._shared_industry_df

        logger.info("MarketCache: 获取行业板块列表(THS)...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_industry_name_ths()
            if df is not None and not df.empty:
                df = df.rename(columns={"name": "板块名称"})
                MarketCache._shared_industry_df = df
                MarketCache._shared_industry_ts = time.time()
                logger.info(f"MarketCache: 行业板块获取成功(THS, {len(df)}个)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: 行业板块获取失败: {e}")
            if MarketCache._shared_industry_df is not None:
                return MarketCache._shared_industry_df
            return pd.DataFrame()

    def get_stocks_by_industry(self, industry_name: str) -> list[str]:
        """获取指定行业的成分股代码列表（带缓存）

        主数据源: ak.stock_board_industry_cons_em()（东方财富）
        备用数据源: 同花顺HTML翻页爬取

        Args:
            industry_name: 行业名称（如"半导体"）

        Returns:
            股票代码列表（如["688981", "002049", ...]）
        """
        # 缓存命中检查
        if industry_name in MarketCache._shared_industry_stocks:
            ts = MarketCache._shared_industry_stocks_ts.get(industry_name, 0)
            if (time.time() - ts) < MarketCache.INDUSTRY_STOCKS_TTL:
                logger.info(f"MarketCache: 行业成分股缓存命中({industry_name})")
                return MarketCache._shared_industry_stocks[industry_name]

        logger.info(f"MarketCache: 获取行业成分股(THS, {industry_name}, timeout=15s)...")
        try:
            import threading as _thr
            _res = [None]; _err = [None]
            def _f():
                try: _res[0] = self._get_ths_board_stocks_by_name(industry_name, "industry")
                except Exception as e: _err[0] = e
            _t = _thr.Thread(target=_f, daemon=True)
            _t.start(); _t.join(timeout=15)
            if _t.is_alive():
                logger.warning(f"MarketCache: 行业成分股获取超时({industry_name})")
                return MarketCache._shared_industry_stocks.get(industry_name, [])
            if _err[0]:
                raise _err[0]
            codes = _res[0]
            if codes:
                MarketCache._shared_industry_stocks[industry_name] = codes
                MarketCache._shared_industry_stocks_ts[industry_name] = time.time()
                logger.info(f"MarketCache: 行业成分股获取成功(THS, {industry_name}, {len(codes)}只)")
                return codes
            return []
        except Exception as e:
            logger.error(f"MarketCache: 行业成分股获取失败({industry_name}): {e}")
            return MarketCache._shared_industry_stocks.get(industry_name, [])

    def get_concept_boards(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取概念板块列表（带缓存）

        主数据源: ak.stock_board_concept_name_em()（东方财富）
        备用数据源: ak.stock_board_concept_name_ths()（同花顺）
        """
        ttl = 600  # 概念板块10分钟TTL
        if (not force_refresh
                and MarketCache._shared_concept_df is not None
                and (time.time() - MarketCache._shared_concept_ts) < ttl):
            logger.info("MarketCache: 概念板块缓存命中")
            return MarketCache._shared_concept_df

        logger.info("MarketCache: 获取概念板块列表(THS)...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_concept_name_ths()
            if df is not None and not df.empty:
                df = df.rename(columns={"name": "板块名称"})
                MarketCache._shared_concept_df = df
                MarketCache._shared_concept_ts = time.time()
                logger.info(f"MarketCache: 概念板块获取成功(THS, {len(df)}个)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: 概念板块获取失败: {e}")
            if MarketCache._shared_concept_df is not None:
                return MarketCache._shared_concept_df
            return pd.DataFrame()

    def get_stocks_by_concept(self, concept_name: str) -> list[str]:
        """获取指定概念的成分股代码列表（带缓存）

        数据源: 同花顺HTML翻页爬取
        """
        if concept_name in MarketCache._shared_concept_stocks:
            ts = MarketCache._shared_concept_stocks_ts.get(concept_name, 0)
            if (time.time() - ts) < MarketCache.CONCEPT_STOCKS_TTL:
                logger.info(f"MarketCache: 概念成分股缓存命中({concept_name})")
                return MarketCache._shared_concept_stocks[concept_name]

        logger.info(f"MarketCache: 获取概念成分股(THS, {concept_name}, timeout=15s)...")
        try:
            import threading as _thr2
            _res2 = [None]; _err2 = [None]
            def _f2():
                try: _res2[0] = self._get_ths_board_stocks_by_name(concept_name, "concept")
                except Exception as e: _err2[0] = e
            _t2 = _thr2.Thread(target=_f2, daemon=True)
            _t2.start(); _t2.join(timeout=15)
            if _t2.is_alive():
                logger.warning(f"MarketCache: 概念成分股获取超时({concept_name})")
                return MarketCache._shared_concept_stocks.get(concept_name, [])
            if _err2[0]:
                raise _err2[0]
            codes = _res2[0]
            if codes:
                MarketCache._shared_concept_stocks[concept_name] = codes
                MarketCache._shared_concept_stocks_ts[concept_name] = time.time()
                logger.info(f"MarketCache: 概念成分股获取成功(THS, {concept_name}, {len(codes)}只)")
                return codes
            return []
        except Exception as e:
            logger.error(f"MarketCache: 概念成分股(THS)获取失败({concept_name}): {e}")
            return MarketCache._shared_concept_stocks.get(concept_name, [])

    def is_trading_hours(self) -> bool:
        """判断当前是否在A股交易时段

        A股交易时间：周一至周五 9:30-11:30, 13:00-15:00
        """
        now = datetime.now()

        # 周末不交易
        if now.weekday() >= 5:
            return False

        # 检查是否在交易时段
        t = now.time()
        morning = dtime(9, 30) <= t <= dtime(11, 30)
        afternoon = dtime(13, 0) <= t <= dtime(15, 0)

        return morning or afternoon

    def _is_expired(self, timestamp: float) -> bool:
        """判断缓存是否过期（区分盘中/盘后/非交易日）

        盘中：TTL过期（默认5分钟）
        盘后/非交易日：长缓存（如果缓存是今天创建的，不过期）

        Args:
            timestamp: 缓存创建时间戳

        Returns:
            True表示已过期
        """
        if not self.is_trading_hours():
            # 非交易时段
            cache_time = datetime.fromtimestamp(timestamp)
            now = datetime.now()
            t = now.time()

            # v0.8.7.5 审计修复 A25：原第二分支"缓存日期>=昨天即不过期"没有校验缓存
            # 是否在最近收盘之后——盘中(如上午10点)生成的快照，在午休/收盘后被当
            # "最近收盘数据"一直复用（实测 expired=False）。
            # 正确口径（对齐 LRN-20260515-001 建议）：
            # - 盘后(>=15:00)：只认今天 15:00 之后生成的缓存
            # - 盘前/周末节假日：只认最近一个工作日 15:00 之后生成的缓存
            # - 午休等盘中间歇：不走特例，按正常 TTL 判过期
            is_after_close = t >= dtime(15, 0)
            is_before_open = t < dtime(9, 30)
            is_weekend = now.weekday() >= 5
            if is_after_close or is_before_open or is_weekend:
                # 最近一次收盘时刻：周末/盘前=上一个工作日15:00；工作日盘后=今天15:00
                if is_weekend or is_before_open:
                    d = now.date() - timedelta(days=1)
                    while d.weekday() >= 5:
                        d -= timedelta(days=1)
                    last_close = datetime.combine(d, dtime(15, 0))
                else:
                    last_close = datetime.combine(now.date(), dtime(15, 0))
                return cache_time < last_close

        # 盘中/午休：TTL过期
        return (time.time() - timestamp) > self._ttl

    def refresh(self):
        """强制刷新所有缓存"""
        MarketCache._shared_stock_df = None
        MarketCache._shared_stock_ts = 0.0
        MarketCache._shared_stock_count = 0
        MarketCache._shared_fail_ts = 0.0
        MarketCache._shared_etf_df = None
        MarketCache._shared_etf_ts = 0.0
        MarketCache._shared_industry_df = None
        MarketCache._shared_industry_ts = 0.0
        MarketCache._shared_concept_df = None
        MarketCache._shared_concept_ts = 0.0
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_concept_stocks.clear()
        MarketCache._shared_concept_stocks_ts.clear()
        logger.info("MarketCache: 所有缓存已清除")

    def get_cache_status(self) -> dict:
        """返回缓存状态信息（用于CLI展示）

        Returns:
            缓存状态字典
        """
        status = {
            "trading_hours": self.is_trading_hours(),
            "stocks": {
                "cached": MarketCache._shared_stock_df is not None,
                "count": MarketCache._shared_stock_count if MarketCache._shared_stock_df is not None else 0,
                "age_seconds": round(time.time() - MarketCache._shared_stock_ts) if MarketCache._shared_stock_ts else 0,
                "expired": self._is_expired(MarketCache._shared_stock_ts) if MarketCache._shared_stock_ts else True,
            },
            "etfs": {
                "cached": MarketCache._shared_etf_df is not None,
                "count": MarketCache._shared_etf_count if MarketCache._shared_etf_df is not None else 0,
                "age_seconds": round(time.time() - MarketCache._shared_etf_ts) if MarketCache._shared_etf_ts else 0,
                "expired": self._is_expired(MarketCache._shared_etf_ts) if MarketCache._shared_etf_ts else True,
            },
            "industries": {
                "cached": MarketCache._shared_industry_df is not None,
                "count": len(MarketCache._shared_industry_df) if MarketCache._shared_industry_df is not None else 0,
            },
            "concepts": {
                "cached": MarketCache._shared_concept_df is not None,
                "count": len(MarketCache._shared_concept_df) if MarketCache._shared_concept_df is not None else 0,
            },
        }
        return status
