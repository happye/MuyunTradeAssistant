"""数据资格现场探查（plan/fusion DATA_COVERAGE.md「待现场探查」三行，external opt-in）

探查目标（每项打印结构化结论，供回写 DATA_COVERAGE.md）：
1. 财务三表公布日 PIT 判据：baostock query_profit_data / query_balance_data /
   query_cashflow_data / query_growth_data / query_operation_data 的字段清单——
   关键看 pubDate（季报公布日）是否官方可得。有 pubDate → 财务证据可进严格 PIT
   快照（E2/E4 因子解锁）；无 → 只能 live，不进回测
2. 行业指数：baostock query_stock_industry（行业归属）+ akshare 申万指数日线
   （relative_trend_v1 因子的分母）——指数 bar 天然 PIT（同行情），验证接口可用性
3. 停牌状态：baostock query_history_k_data_plus 的 tradestatus 字段
   （trading_capacity_v1 的状态分量）

只读探查（不改任何本地缓存/配置）。跑法（真实网络，opt-in）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/data_sources/probe_fin_pubdate.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)

SAMPLES = [("sh.600519", "贵州茅台"), ("sz.300750", "宁德时代"), ("sh.688256", "寒武纪")]


def probe_profit_pubdate(bs):
    print("=" * 70)
    print("【1】财务季频接口 pubDate 可得性（PIT 判据）")
    interfaces = [
        ("query_profit_data", bs.query_profit_data),
        ("query_balance_data", bs.query_balance_data),
        ("query_cash_flow_data", bs.query_cash_flow_data),
        ("query_operation_data", bs.query_operation_data),
        ("query_growth_data", bs.query_growth_data),
    ]
    findings = {}
    for name, fn in interfaces:
        try:
            rs = fn(code="sh.600519", year=2023, quarter=4)
            fields = list(rs.fields) if rs.fields else []
            row = None
            while rs.next():
                row = rs.get_row_data()
                break
            pub_idx = fields.index("pubDate") if "pubDate" in fields else -1
            pub_val = row[pub_idx] if (row and pub_idx >= 0) else None
            findings[name] = {"has_pubDate": pub_idx >= 0, "pubDate_sample": pub_val,
                              "error": rs.error_code if rs.error_code != "0" else None}
            print(f"  {name}: pubDate={'✅' if pub_idx >= 0 else '❌'}"
                  f" 样本值={pub_val} 字段数={len(fields)} error={rs.error_code}")
            if name == "query_profit_data" and fields:
                print(f"    字段清单: {fields}")
        except Exception as e:
            findings[name] = {"error": str(e)}
            print(f"  {name}: 异常 {e}")
    # 同一期多次查询一致性 + 相邻期 pubDate 递增（重述可分性粗验）
    try:
        rs = bs.query_profit_data(code="sh.600519", year=2023, quarter=4)
        first = None
        while rs.next():
            first = rs.get_row_data()
            break
        fields = list(rs.fields)
        pub = first[fields.index("pubDate")] if first else None
        rs2 = bs.query_profit_data(code="sh.600519", year=2023, quarter=4)
        second = None
        while rs2.next():
            second = rs2.get_row_data()
            break
        pub2 = second[fields.index("pubDate")] if second else None
        print(f"  同期两次查询 pubDate 一致: {pub == pub2}（{pub} vs {pub2}）")
        findings["_repeat_consistent"] = pub == pub2
    except Exception as e:
        print(f"  重复查询一致性: 异常 {e}")
    return findings


def probe_industry(bs):
    print("=" * 70)
    print("【2】行业归属与行业指数")
    out = {}
    try:
        rs = bs.query_stock_industry(code="sh.600519")
        fields = list(rs.fields) if rs.fields else []
        row = None
        while rs.next():
            row = rs.get_row_data()
            break
        print(f"  baostock query_stock_industry: fields={fields} 样本={row}")
        out["stock_industry"] = {"fields": fields, "sample": row}
    except Exception as e:
        print(f"  baostock query_stock_industry: 异常 {e}")
        out["stock_industry"] = {"error": str(e)}
    # akshare 申万指数日线（relative_trend_v1 分母候选）
    try:
        import akshare as ak
        import inspect
        cand = [n for n in dir(ak) if "sw" in n.lower() and ("index" in n.lower() or "daily" in n.lower())]
        print(f"  akshare 申万候选接口: {cand[:10]}")
        for fn_name in ("index_hist_sw", "sw_index_daily", "sw_index_daily_indicator"):
            if hasattr(ak, fn_name):
                fn = getattr(ak, fn_name)
                try:
                    sig = str(inspect.signature(fn))
                    print(f"  {fn_name}{sig}")
                    if fn_name == "index_hist_sw":
                        df = fn(symbol="801010", period="day")
                        print(f"    index_hist_sw(801010) 返回 {len(df)} 行, 列={list(df.columns)}")
                        print(f"    首行={df.iloc[0].to_dict()}")
                        out["sw_index"] = {"interface": fn_name, "rows": len(df),
                                           "columns": list(df.columns)}
                        break
                except Exception as e:
                    print(f"  {fn_name} 调用异常: {e}")
    except Exception as e:
        print(f"  akshare 探查异常: {e}")
    return out


def probe_tradestatus(bs):
    print("=" * 70)
    print("【3】停牌状态 tradestatus 字段")
    out = {}
    try:
        rs = bs.query_history_k_data_plus(
            "sh.600519", "date,code,tradestatus,isST",
            start_date="2024-01-01", end_date="2024-03-31", frequency="d", adjustflag="3")
        fields = list(rs.fields) if rs.fields else []
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rs.error_code != "0":
            print(f"  baostock 返回错误: code={rs.error_code} msg={rs.error_msg}")
        if not fields and not rows:
            print(f"  ⚠ 空结果（fields/rows 均空）error_code={rs.error_code}——字段名非法或频控，探查未通过")
        status_values = sorted(set(r[fields.index("tradestatus")] for r in rows)) if rows and "tradestatus" in fields else []
        print(f"  fields={fields}")
        print(f"  2024Q1 {len(rows)} 个交易日, tradestatus 取值集合={status_values}")
        print(f"  样本行: {rows[0] if rows else None} / 末行: {rows[-1] if rows else None}")
        out = {"fields": fields, "status_values": status_values, "rows": len(rows)}
    except Exception as e:
        print(f"  tradestatus 探查异常: {e}")
        out = {"error": str(e)}
    return out


def main():
    import baostock as bs
    lg = bs.login()
    print(f"baostock login: {lg.error_code} {lg.error_msg}")
    if lg.error_code != "0":
        print("登录失败——网络不可用，探查中止（结果如实标注）")
        return
    try:
        r1 = probe_profit_pubdate(bs)
        r2 = probe_industry(bs)
        r3 = probe_tradestatus(bs)
        print("=" * 70)
        print("【结论汇总（供 DATA_COVERAGE.md 回写）】")
        fin_ok = all(v.get("has_pubDate") for k, v in r1.items()
                     if k.startswith("query_") and "error" not in v)
        print(f"  1. 财务季频 pubDate: {'可 PIT（E2/E4 财务因子解锁候选）' if fin_ok else '部分/不可——逐接口见上'}")
        print(f"  2. 行业指数: 见【2】输出（接口可用性与字段）")
        print(f"  3. 停牌状态: {'tradestatus 可用' if r3.get('status_values') else '不可用/字段缺失'}")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
