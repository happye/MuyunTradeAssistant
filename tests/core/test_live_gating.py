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
    r = check_stock_top_signal(sd, "600519", announcements=None, live=False)
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
    assert _daily_cache_get("TESTCODE", "test_sig", "research.stock.test") is _MISS, "未命中应返回 _MISS"
    # 缓存 None（无信号 = 合法缓存值）
    _daily_cache_set("TESTCODE", "test_sig", None)
    r = _daily_cache_get("TESTCODE", "test_sig", "research.stock.test")
    assert r is None, f"缓存 None 应回读 None（非 _MISS），got {r!r}"
    # v2 缓存 SignalFinding（ISS-117 S1）：dict 含 signal_id 才有效
    from src.data.models import SignalFinding
    f = SignalFinding(signal_id="research.stock.test", detail="测试")
    _daily_cache_set("TESTCODE", "test_sig2", f)
    r2 = _daily_cache_get("TESTCODE", "test_sig2", "research.stock.test")
    assert isinstance(r2, dict) and r2.get("signal_id") == "research.stock.test"
    # v0.8.29 S1：旧版纯字符串缓存不复活为信号（回读 _MISS）
    import json as _json
    old = _holder_cache_dir() / f"TESTCODE_{today}_legacy_v1.json"
    old.write_text(_json.dumps("个股:旧版硬信号文本"), encoding="utf-8")
    # 注：旧文件名无 _v2 后缀，v2 读取天然 MISS——写入同键 v2 字符串再验
    old2 = _holder_cache_dir() / f"TESTCODE_{today}_legacy_v2.json"
    old2.write_text(_json.dumps("个股:旧版硬信号文本"), encoding="utf-8")
    assert _daily_cache_get("TESTCODE", "legacy", "research.stock.legacy") is _MISS, "旧版字符串缓存不得复活"
    print("✓ 日缓存 _MISS 哨兵 + None/SignalFinding 序列化 + 旧字符串缓存不复活")

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
