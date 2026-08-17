"""ISS-053 数据源可得性探测（评估脚本，非测试）

探测持有期"重大利空硬退出"所需的 3 个结构化信号能否真取到：
1. ST/*ST 状态：akshare stock_zh_a_st_em() (ST列表成员判定) + baostock query_stock_basic(type字段)
2. 业绩预告预亏/预减：baostock query_forecast_report(code, start, end) (预告类型字段)
3. 业绩快报：baostock query_performance_express_report

公告关键词源 (news_client.stock_news_em) 已被 risk_deduction 用，已知可用，不重复探测。

跑法:
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/probe_iss053_data.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timedelta


def probe_akshare_st_list():
    """探测1a: akshare ST 股列表接口"""
    print("\n--- 探测1a: akshare stock_zh_a_st_em() ST列表 ---")
    try:
        import akshare as ak
        from src.data.source_check import fix_curl_ssl_paths
        fix_curl_ssl_paths()
        df = ak.stock_zh_a_st_em()
        if df is None or df.empty:
            print("  ✗ 返回空")
            return
        print(f"  ✓ 取到 ST 列表 {len(df)} 行, 列: {list(df.columns)[:8]}")
        # 找一只已知 ST 股验证 (ST星源 000005 历史ST, 用任意在列表里的样本)
        code_col = next((c for c in ['代码', 'code', '股票代码'] if c in df.columns), None)
        if code_col:
            sample = df.head(3)[code_col].tolist()
            print(f"  样本代码: {sample}")
        # 检查是否有名称列含 ST
        name_col = next((c for c in ['名称', 'name', '股票简称'] if c in df.columns), None)
        if name_col:
            names = df.head(5)[name_col].tolist()
            print(f"  样本名称: {names}")
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")


def probe_baostock_stock_basic():
    """探测1b: baostock query_stock_basic 是否返回 ST 标识"""
    print("\n--- 探测1b: baostock query_stock_basic (ST字段?) ---")
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            print(f"  ✗ 登录失败: {lg.error_msg}")
            return
        # 测一只正常股 + 一只历史ST股
        for code in ["sh.600519", "sz.000005"]:
            rs = bs.query_stock_basic(code=code)
            fields = rs.fields
            row = None
            while rs.next():
                row = rs.get_row_data()
            if row:
                print(f"  {code}: fields={fields}")
                print(f"           values={row}")
            else:
                print(f"  {code}: 无数据 (error={rs.error_msg})")
        bs.logout()
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")


def probe_baostock_forecast():
    """探测2: baostock query_forecast_report 业绩预告 (预亏/预减判定)"""
    print("\n--- 探测2: baostock query_forecast_report 业绩预告 ---")
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            print(f"  ✗ 登录失败: {lg.error_msg}")
            return
        # 近1年半的业绩预告 (覆盖年报+中报+季报预告)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=540)).strftime("%Y-%m-%d")
        for code in ["sh.600519", "sz.000651", "sz.300750"]:
            rs = bs.query_forecast_report(code=code, start_date=start, end_date=end)
            fields = rs.fields
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                print(f"  {code}: {len(rows)} 条预告, fields={fields}")
                for r in rows[-2:]:
                    print(f"           最新: {r}")
            else:
                print(f"  {code}: 无预告数据 (error={rs.error_msg})")
        bs.logout()
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")


def probe_baostock_perf_express():
    """探测3: baostock query_performance_express_report 业绩快报"""
    print("\n--- 探测3: baostock query_performance_express_report 业绩快报 ---")
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            print(f"  ✗ 登录失败: {lg.error_msg}")
            return
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=540)).strftime("%Y-%m-%d")
        rs = bs.query_performance_express_report(code="sh.600519", start_date=start, end_date=end)
        fields = rs.fields
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            print(f"  ✓ 600519: {len(rows)} 条快报, fields={fields}")
            for r in rows[-2:]:
                print(f"      最新: {r}")
        else:
            print(f"  600519: 无快报 (error={rs.error_msg})")
        bs.logout()
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("=== ISS-053 数据源可得性探测 ===")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    probe_akshare_st_list()
    probe_baostock_stock_basic()
    probe_baostock_forecast()
    probe_baostock_perf_express()
    print("\n=== 探测完成 ===")
