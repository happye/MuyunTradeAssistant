# -*- coding: utf-8 -*-
"""ISS-003 大盘数据可行性测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baostock as bs
import pandas as pd

def test_index_data():
    """测试Baostock获取沪深300指数数据"""
    lg = bs.login()
    print(f"Baostock login: {lg.error_msg}")

    # 获取沪深300最近60个交易日K线
    rs = bs.query_history_k_data_plus(
        "sh.000300",
        "date,close,volume",
        start_date="2026-01-01",
        end_date="2026-04-18",
        frequency="d",
    )

    data = []
    while (rs.error_code == '0') and rs.next():
        data.append(rs.get_row_data())

    bs.logout()

    if not data:
        print("FAIL: no index data returned")
        return

    df = pd.DataFrame(data, columns=rs.fields)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

    print(f"Rows: {len(df)}")
    print(f"Date range: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    print(f"Latest close: {df['close'].iloc[-1]}")

    # 计算MA20和MA60
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma60 = df['close'].rolling(60).mean().iloc[-1]
    print(f"MA20: {ma20:.2f}")
    print(f"MA60: {ma60:.2f}")

    if ma20 and ma60:
        if ma20 > ma60:
            print("Index trend: BULLISH (MA20 > MA60)")
        else:
            print("Index trend: BEARISH (MA20 < MA60)")

    print("OK: index data feasible via Baostock")

if __name__ == '__main__':
    test_index_data()
