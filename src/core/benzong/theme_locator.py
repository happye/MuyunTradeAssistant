"""笨总主题精准定位（法C，ISS-041 scan 增强 / v0.8.6.3）

绕过板块匹配的天花板：细分材料（PBO树脂/硅微粉/第四代铜箔/钽电容/氮化镓）没有
对应板块，板块匹配只能找相邻概念（丙烯酸/化肥），不精准。

法C流程：
1. AI 直接识别主题对应的A股公司（code+name+why）—— 利用 AI 知道"谁做PBO"
2. Baostock query_stock_basic 验证代码真实存在 + 用真实名称替换 AI 名称
   （抓 AI 编错代码/把不存在的公司塞进来）
3. AI 名称 vs Baostock 名称模糊匹配，不一致则丢弃（AI 可能给错代码）
4. 蹭概念过滤交给笨总业务纯度维度（低纯度=蹭概念，自然低分），不重复造轮子

返回：
    {
      "stocks": [{"code","name","term","why"}],  # 验证通过
      "invalid": [{"code","name","reason"}],      # AI报了但验证失败
      "terms": [...],
    }
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是A股主题定位助手。用户会给若干细分主题词（材料/技术/赛道），你要说出A股中真正主营或深度布局这些方向的上市公司。

要求：
1. 只返回你确信真实存在的A股公司，每家给出6位股票代码+名称+属于哪个主题词+一句话为什么
2. 代码必须是6位数字（如 002475），不要带市场前缀
3. 不确定或蹭概念的不报——宁可少报不报错的
4. 一个主题词可对应多家公司，一家公司可对应多个主题词

输出严格 JSON 数组，不要其他内容：
[{"code": "6位代码", "name": "公司名", "term": "对应主题词", "why": "为什么属这个主题"}]
"""


def _normalize_code(code: str) -> Optional[str]:
    """从 AI 输出里提 6 位数字代码。"""
    if not code:
        return None
    m = re.search(r"(\d{6})", str(code))
    return m.group(1) if m else None


def _baostock_prefix(code: str) -> str:
    """6位代码 → Baostock 市场前缀。"""
    if code.startswith(("sh.000", "9")):
        return "sh"
    if code.startswith("6") or code.startswith("5"):
        return "sh"
    return "sz"


def _verify_code(code: str, ai_name: str) -> Optional[dict]:
    """用 Baostock 验证代码存在 + 返回真实名称。

    返回 {code, name} 或 None（不存在 / 名称对不上）。
    名称对不上 = AI 可能给错了代码（张冠李戴），丢弃。
    """
    try:
        from src.data.akshare_client import _ensure_baostock_login
        import baostock as bs
        if not _ensure_baostock_login():
            return None
        prefix = _baostock_prefix(code)
        rs = bs.query_stock_basic(code=f"{prefix}.{code}")
        if rs.error_code != "0":
            return None
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return None
        f = rs.fields
        status = rows[0][f.index("status")] if "status" in f else "1"
        if status != "1":  # 已退市
            return None
        real_name = rows[0][f.index("code_name")]
        # AI 名称 vs 真实名称模糊匹配（AI 可能简称/全称差异，放宽：含核心字即可）
        if not _name_matches(ai_name, real_name):
            logger.info(f"theme_locator 丢弃 {code}: AI说'{ai_name}' 实际'{real_name}' 名称不符")
            return None
        return {"code": code, "name": real_name}
    except Exception as e:
        logger.debug(f"theme_locator 验证异常 {code}: {e}")
        return None


def _name_matches(ai_name: str, real_name: str) -> bool:
    """AI 名称与真实名称是否匹配（容错简称/全称）。

    规则：去除常见后缀后，一方包含另一方的核心字（≥2字）。
    AI 报错代码时通常名称完全对不上，此过滤能抓住。
    """
    if not ai_name or not real_name:
        return False
    a = re.sub(r"(股份|有限公司|集团|控股|科技|新材|材料|股份)$", "", ai_name.strip())
    r = real_name.strip()
    # 取较短名作为核心，看是否在另一方里
    core = a if len(a) <= len(r) else r
    host = r if len(a) <= len(r) else a
    if len(core) < 2:
        return core in host
    # 核心字（前2-3字）在另一方出现
    return core[:2] in host or core[:3] in host or core in host


def locate_theme_stocks(
    theme_terms: list[str],
    ai_client=None,
    ai_model: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict:
    """法C：AI 识别主题对应A股公司 + Baostock 验证。

    Args:
        theme_terms: 主题词列表（如 ["PBO树脂材料","氮化镓"]）
        ai_client / ai_model: 可注入；None 时从 settings 构造
        config: settings.yaml

    Returns:
        {stocks: [{code,name,term,why}], invalid: [{code,name,reason}], terms: [...]}
    """
    theme_terms = [t for t in theme_terms if t and t.strip()]
    if not theme_terms:
        return {"stocks": [], "invalid": [], "terms": []}

    if ai_client is None:
        from src.core.benzong.auto_scorer import _build_ai_client
        ai_client, ai_model = _build_ai_client(config)
    if ai_client is None:
        logger.warning("theme_locator: AI client 不可用，无法定位")
        return {"stocks": [], "invalid": [], "terms": theme_terms, "error": "AI不可用"}

    terms_str = "、".join(theme_terms)
    user_prompt = f"用户主题词: {terms_str}\n请说出A股中真正主营或深度布局这些方向的上市公司。"

    try:
        resp = ai_client.chat.completions.create(
            model=ai_model or "deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"theme_locator AI 调用失败: {e}")
        return {"stocks": [], "invalid": [], "terms": theme_terms, "error": f"AI调用失败: {e}"}

    # 解析 JSON 数组
    candidates = _parse_stock_list(text)
    logger.info(f"theme_locator AI 报 {len(candidates)} 个候选，开始验证")

    stocks = []
    invalid = []
    seen = set()
    for c in candidates:
        code = _normalize_code(c.get("code", ""))
        if not code or code in seen:
            continue
        ai_name = str(c.get("name", "")).strip()
        verified = _verify_code(code, ai_name)
        if verified:
            seen.add(code)
            stocks.append({
                "code": verified["code"],
                "name": verified["name"],
                "term": c.get("term", ""),
                "why": str(c.get("why", ""))[:100],
            })
        else:
            invalid.append({"code": code, "name": ai_name,
                            "reason": "代码不存在或名称不符（AI可能给错）"})

    logger.info(f"theme_locator 验证通过 {len(stocks)} / 丢弃 {len(invalid)}")
    return {"stocks": stocks, "invalid": invalid, "terms": theme_terms}


def _parse_stock_list(text: str) -> list[dict]:
    """从 AI 返回文本解析股票列表 JSON。容错 markdown 代码块。"""
    text = text.strip()
    # 去 markdown 代码块
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    # 找 JSON 数组
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return []
