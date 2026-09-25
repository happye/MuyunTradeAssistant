# 只读隔离探针记录

2026-09-25，执行时HEAD `bdebf13`；探针涉及的源码与初查`449bbfb`一致。只从源码AST提取纯函数，注入最小展示桩；scorer使用标准库runpy读取。没有导入CLI应用、调用行情/AI、写持仓或更改产品代码。这些探针验证局部分支，**不代表完整行情集成测试或收益回测已通过**。

## 结果

| 探针 | 输入 | 实际输出 | 可支持的结论 |
|---|---|---|---|
| A 摘要冲突 | 原始SELL、entry_exit.exit_triggered=True，最终HOLD/HOLD_POSITION，有持仓 | `今天出现【卖出信号：EXIT】。按纪律次日开盘执行，别拖。` | 摘要没有以最终策略动作为准 |
| B 摘要冲突 | 原始HOLD、entry_exit为空，最终SELL/CLOSE_ALL，有持仓 | `没有买卖信号：继续持有，今天什么都不用做。` | 后置强制退出不能仅靠当前摘要表达 |
| C 分数方向 | bearish，confidence=.8，sentiment_weight=.3，无其他修正 | adjustment=-.24；SELL强度.8按编排公式变.56 | 调节函数没有区分选定动作方向；下游影响需F2端到端验证 |
| D 排名分量 | SELL score=.9 与 BUY score=.6 | 技术分分别90与60 | 技术分量是动作强度映射，并非买入吸引力；未声称全榜一定排SELL第一 |
| E 等级口径 | 正向五维80、风险0；成交额0.7/1.0/1.6万亿 | raw=76.8/96/115.2；normalized均80；grade=C/A/A | 展示分不变不代表等级或mode不变；不是保真公式计算错误 |

A/B首次输出遇Windows中文编码显示乱码；随后用`json.dumps(..., ensure_ascii=True)`重新运行，`A_says_sell=true`与`B_says_hold=true`均确认。数值探针C/D/E正常输出。

## 可复现脚本（从项目根执行；只读）

将下列Python代码通过标准输入交给已可用的Python解释器即可；无需新建产品脚本或修改测试目录。Windows注意标准输入/输出编码，结果用JSON转义输出。

```python
import ast, json, runpy, types
from pathlib import Path

def load_fn(path, name, extra):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[
        ast.ImportFrom(module='__future__',
                       names=[ast.alias(name='annotations')], level=0), node
    ], type_ignores=[])
    ns = dict(extra)
    exec(compile(ast.fix_missing_locations(module), path, 'exec'), ns)
    return ns[name]

S = types.SimpleNamespace
output = []
summary = load_fn('src/cli/main.py', '_print_plain_summary', {
    '_exit_action_cn': lambda *args: 'EXIT',
    '_plain_stage': lambda stock: 'S2',
    '_cached_benzong_brief': lambda code: None,
    'Panel': lambda body, **kwargs: body,
    'console': S(print=output.append),
})
stock = S(stock_code='DEMO', price=10)
position = S(current_ratio=.2, entry_price=10, trade_plan=None)
summary(S(decision=S(value='SELL')),
        S(decision=S(value='HOLD'), position_action='HOLD_POSITION',
          entry_exit={'exit_triggered': True, 'exit_action': 'EXIT'}),
        stock, position)
print(json.dumps({'A': output[-1].splitlines()[0]}, ensure_ascii=True))
summary(S(decision=S(value='HOLD')),
        S(decision=S(value='SELL'), position_action='CLOSE_ALL', entry_exit={}),
        stock, position)
print(json.dumps({'B': output[-1].splitlines()[0]}, ensure_ascii=True))

modify = load_fn('src/core/ai_modifier.py', '_apply_modification', {})
ai = S(sentiment='bearish', confidence=.8, risk_level='low', event_type='none',
       narrative_shift=False, position_cap=1, score_adjustment=0)
modify(S(sentiment_weight=.3, risk_position_cap=.5), ai, None)
print('C', round(ai.score_adjustment, 3), round(.8 + ai.score_adjustment, 3))

rank = load_fn('src/core/ranking_layer.py', '_calc_technical_score', {})
obj = S(_clip=lambda value, low, high: max(low, min(high, value)))
print('D', rank(obj, S(score=.9, decision=S(value='SELL'))),
      rank(obj, S(score=.6, decision=S(value='BUY'))))

score_one = runpy.run_path('src/core/benzong/scorer.py')['score_one']
for turnover in (.7, 1., 1.6):
    score = score_one(80, 80, 80, 80, 80, 0, turnover)
    print('E', turnover, score.total_score,
          score.normalized_score(), score.effective_grade())
```

## 环境记录与下一步

沙箱内`.venv/Scripts/python.exe -`无法启动它引用的uv Python；经自动审批允许的沙箱外只读执行成功。未重装解释器、未修改PATH或依赖。该失败不是产品回归失败。

F1/F2必须把上述分支写成真实回归测试，并补上从PlanGuard/Execution实际生成终态的完整路径；不能只把此AST探针复制成测试就宣称真实接线已验证。当前源码问题均保持未修复状态。
