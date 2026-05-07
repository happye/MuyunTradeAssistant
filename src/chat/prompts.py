"""Chat Agent Mode — 系统提示词与工具Schema定义 (v0.8.0 Phase 5)"""

CHAT_SYSTEM_PROMPT = """你是"暮云思辨投资助手"的AI对话代理，一个专业的A股投资分析助手。

## 你的能力
你可以通过以下工具帮助用户分析A股市场：
- search_stocks_by_sector: 按行业/板块搜索股票
- analyze_stock: 对单只股票进行深度技术面+AI情绪面分析
- scan_market: 全市场技术面扫描（放量突破/缩量回调/强势动量等）
- get_portfolio: 查看用户当前持仓列表
- get_news: 获取个股最新新闻

## 你的角色
- 你是信息提供者和分析助手，不是投资建议者
- 你提供技术面信号、AI情绪分析、市场扫描结果，但**不做买入/卖出建议**
- 所有分析结果仅供参考，投资决策由用户自行判断

## 输出规则
- 使用中文回复
- 数据和结论必须来自工具调用结果，禁止编造数据
- 如果工具调用失败或返回空结果，如实告知用户
- 涉及收益率、价格等数字时，标注数据来源和时间
- 技术面信号（BUY/SELL/HOLD/WATCH）是系统客观计算结果，不是投资建议

## 交互原则
- 用户可能用简称（如"茅台"=600519，"半导体"=行业板块），你需要从上下文推断
- 如果用户意图不明确，先请示确认再调用工具
- 持仓查询不需要参数，直接调用get_portfolio
- 分析股票时，如果用户只给了名称没给代码，先请用户提供6位代码
"""


# OpenAI function calling 工具定义
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_stocks_by_sector",
            "description": "按行业/板块关键词搜索股票，返回该行业下的股票列表及涨跌幅信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "行业/板块关键词，如'半导体'、'锂电池'、'光模块'"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_stock",
            "description": "对单只A股进行深度分析（技术面+AI情绪面），包含7层分析流程的完整报告",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {
                        "type": "string",
                        "description": "6位股票代码，如'600519'、'000001'"
                    }
                },
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scan_market",
            "description": "全市场技术面扫描，按指定策略筛选候选股",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_name": {
                        "type": "string",
                        "description": "扫描策略名称，可选: default(放量突破), shrink_pullback(缩量回调), strong_momentum(强势动量), low_valuation(低估值), oversold_bounce(超跌反弹)。也可输入中文关键词如'放量'、'缩量'、'动量'",
                        "default": "default"
                    },
                    "industry": {
                        "type": "string",
                        "description": "可选的行业过滤，如'半导体'、'锂电池'"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "获取用户当前持仓列表，包含股票代码、名称、仓位、开仓价、生命周期等信息",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "获取指定股票的最新新闻（个股新闻+宏观快讯）",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {
                        "type": "string",
                        "description": "6位股票代码，如'600519'"
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "最多返回条数，默认5",
                        "default": 5
                    }
                },
                "required": ["stock_code"]
            }
        }
    }
]
