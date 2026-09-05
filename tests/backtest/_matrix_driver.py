# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, r"G:\Tools\暮云思辨投资助手")
for k in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"
code, year, mode = sys.argv[1], sys.argv[2], sys.argv[3]
from src.cli.main import load_config, load_pyramid_config, normalize_stock_code
from src.core.backtest_engine import BacktestEngine
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
cfg = load_config()
eng = BacktestEngine(
    stock_code=normalize_stock_code(code),
    start_date=f"{year}-01-01", end_date=f"{year}-12-31",
    initial_capital=200000.0,
    execution_mode="framework_strict", layer_mode="decision_strategy_execution",
    skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
    signal_weights=cfg.get("decision", {}).get("signal_weights", None),
    skill_types=cfg.get("skills", {}).get("types", None),
    entry_exit_config=cfg.get("entry_exit", None),
    pyramid_config=load_pyramid_config(cfg),
    enable_trade_plan=True,
    qizong_codes={code} if mode == "qizong" else None,
    jianzong_codes={code} if mode == "jianzong" else None,
    benzong_auto_mode=False,
)
with ThreadPoolExecutor(max_workers=1) as ex:
    fut = ex.submit(eng.run)
    try:
        res = fut.result(timeout=1250)
        print(json.dumps({"ret": round(res.total_return_pct, 2), "trades": res.total_trades,
                          "mdd": round(res.max_drawdown_pct, 2) if res.max_drawdown_pct else None}))
    except FuturesTimeout:
        print(json.dumps({"ret": None}))
