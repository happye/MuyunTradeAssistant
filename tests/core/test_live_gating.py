"""报告② live 门控测试：股东户数/融资余额仅 live=True 才调，回测(live=False)跳过 akshare。

运行：PYTHONUTF8=1 HF_HUB_OFFLINE=1 ./.venv/Scripts/python.exe tests/test_live_gating.py
"""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.exit_signals.stock import check_stock_top_signal, _daily_cache_get, _daily_cache_set, _MISS
from src.data.models import StockData

def _sd(**kw):
    base = dict(stock_code="600519", stock_name="茅台", price=10, volume=1000)
    base.update(kw)
    return StockData(**base)

def test_live_false_skips_akshare():
    """live=False（回测）不应触发 akshare 网络调用（快速返回）"""
    sd = _sd()
    t0 = time.time()
    r = check_stock_top_signal(sd, "600519", turnover_pct=None, announcements=None, live=False)
    dt = time.time() - t0
    # 无三倍定律触发（low_60d缺）-> None；关键是不 hang（<30s 无 akshare）
    assert dt < 25, f"live=False 不应慢({dt:.1f}s)，可能误调 akshare"
    print(f"✓ live=False 快速返回({dt:.2f}s)，未触发 akshare")

def test_cache_miss_sentinel():
    """_MISS 哨兵正确区分未命中 vs 缓存 None"""
    # 清掉可能的旧缓存（保证幂等）
    from src.core.exit_signals.stock import _holder_cache_dir
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    for f in _holder_cache_dir().glob(f"TESTCODE_{today}_*.json"):
        f.unlink()
    # 未命中
    assert _daily_cache_get("TESTCODE", "test_sig") is _MISS, "未命中应返回 _MISS"
    # 缓存 None
    _daily_cache_set("TESTCODE", "test_sig", None)
    r = _daily_cache_get("TESTCODE", "test_sig")
    assert r is None, f"缓存 None 应回读 None（非 _MISS），got {r!r}"
    # 缓存字符串
    _daily_cache_set("TESTCODE", "test_sig2", "个股:测试信号")
    assert _daily_cache_get("TESTCODE", "test_sig2") == "个股:测试信号"
    print("✓ 日缓存 _MISS 哨兵 + None 序列化正确")

def main():
    tests = [test_live_false_skips_akshare, test_cache_miss_sentinel]
    failed = 0
    for t in tests:
        try: t()
        except Exception as e:
            failed += 1; print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"=== {'全部' if not failed else '部分失败'} {len(tests)-failed}/{len(tests)} PASS ===")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
