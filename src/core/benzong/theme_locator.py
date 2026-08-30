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
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 当日文件缓存（v0.8.7.9）：同主题词当天复用 AI 报股+Baostock 验证结果。
# 动机：同一天反复 bz scan 同一主题时，法C 每次都重调 DeepSeek（费用+延迟）并对
# 每个候选打一次 Baostock query_stock_basic（第三方接口高频触发风险）。
# 语义对齐笨总六维评分的当日缓存：跨天自动失效，文件只存当天，不膨胀。
_CACHE_DIR = Path.home() / ".muyun" / "theme_locator_cache"


def _cache_path(theme_terms: list[str]) -> Path:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", "_".join(sorted(theme_terms)))
    return _CACHE_DIR / f"{safe}.json"


def _load_today_cache(theme_terms: list[str]) -> Optional[dict]:
    """命中当日缓存返回 locate 结果（带 cached=True 标记），否则 None。绝不抛异常。"""
    try:
        p = _cache_path(theme_terms)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return None
        if data.get("terms") != theme_terms:
            return None
        result = data.get("result")
        if isinstance(result, dict) and result.get("stocks"):
            result["cached"] = True
            return result
    except Exception as e:
        logger.debug(f"theme_locator 缓存读取失败: {e}")
    return None


def _save_today_cache(theme_terms: list[str], result: dict) -> bool:
    """落当日缓存。IO 失败只 debug，不阻塞主流程。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(theme_terms).write_text(json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "terms": theme_terms,
            "result": result,
        }, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        logger.debug(f"theme_locator 缓存写入失败: {e}")
        return False


SYSTEM_PROMPT = """你是A股主题定位助手。用户会给若干细分主题词（材料/技术/赛道），你要说出A股中真正主营或深度布局这些方向的上市公司。

重要：不要只报行业龙头/白马。笨总「超景气价值投机」体系恰恰要在龙头之外找弹性更大的标的——
覆盖三类：①细分龙头 ②有真实业务布局的二线公司 ③主营高度聚焦该方向的中小盘（市值小但纯度高，往往比龙头弹性大）。
龙头通常已被充分定价，中小纯正标的才是超额收益来源。

要求：
1. 只返回你确信真实存在、且真有该方向**实际业务/营收**的A股公司（不是只蹭概念）
2. 每家给6位股票代码+名称+属于哪个主题词+一句话为什么（含主营聚焦度）
3. 代码必须是6位数字（如 002475），不要带市场前缀
4. 龙头与中小盘都要报，尽量每个主题词报 8-15 家（覆盖面优先，宁全勿缺龙头垄断）
5. 编不出真实代码的不要硬凑——验证层会丢弃错误代码

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
    use_cache: bool = True,
) -> dict:
    """法C：AI 识别主题对应A股公司 + Baostock 验证。

    Args:
        theme_terms: 主题词列表（如 ["PBO树脂材料","氮化镓"]）
        ai_client / ai_model: 可注入；None 时从 settings 构造
        config: settings.yaml
        use_cache: True 时同主题词当日命中直接复用（v0.8.7.9），跳过 AI+Baostock；
            调用方传 use_cache=not force_refresh 即可接上 bz scan --refresh

    Returns:
        {stocks: [{code,name,term,why}], invalid: [{code,name,reason}], terms: [...]}
        命中缓存时额外带 cached=True
    """
    theme_terms = [t.strip() for t in theme_terms if t and t.strip()]
    if not theme_terms:
        return {"stocks": [], "invalid": [], "terms": []}

    if use_cache:
        cached = _load_today_cache(theme_terms)
        if cached is not None:
            logger.info(f"theme_locator 命中当日缓存({len(cached.get('stocks', []))}只)")
            return cached

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
            model=ai_model or "deepseek-v4-flash",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=2400,
            **({"extra_body": {"thinking": {"type": "disabled"}}} if str(ai_model or "deepseek-v4-flash").startswith("deepseek-v4") else {}),
        )
        text = resp.choices[0].message.content or ""
        # v0.8.7.6 审计修复 B13：截断时重试一次要求精简（对齐 dimensions 的截断纪律）——
        # 原 finish_reason==length 无检测，45家候选2781字符截断到2500 → 解析0只 → 法C静默失效
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            logger.warning("theme_locator: AI 输出被截断(finish_reason=length)，重试要求精简 why 字段")
            resp = ai_client.chat.completions.create(
                model=ai_model or "deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt + "\n（注意：上次输出超长被截断。why 每条不超过20字，先保证 JSON 完整闭合）"},
                ],
                temperature=0.2,
                max_tokens=2400,
                **({"extra_body": {"thinking": {"type": "disabled"}}} if str(ai_model or "deepseek-v4-flash").startswith("deepseek-v4") else {}),
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
    result = {"stocks": stocks, "invalid": invalid, "terms": theme_terms}
    # 只缓存验证通过非空的结果——空结果可能是 Baostock 登录失败等临时故障，
    # 缓存一整天会放大故障
    if stocks:
        _save_today_cache(theme_terms, result)
    return result


def _parse_stock_list(text: str) -> list[dict]:
    """从 AI 返回文本解析股票列表 JSON。容错 markdown 代码块与截断。

    v0.8.7.6 审计修复 B13：原贪婪 `\\[.*\\]` 匹配到最后一个 `]`，截断文本解析必败；
    现改为"首个 [ 起到文末 + 截断修复"——先按最后一个完整对象闭合重试。
    """
    text = text.strip()
    # 去 markdown 代码块
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    # 找 JSON 数组起点（贪婪语义：到文本末尾，等价原 r"\[.*\]" 的起点行为）
    start = text.find("[")
    if start < 0:
        return []
    raw = text[start:]
    end = raw.rfind("]")
    if end >= 0:
        raw = raw[:end + 1]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    # 截断容错：丢掉最后一个不完整对象，按最后一个 } 闭合数组重试
    last_brace = raw.rfind("}")
    if last_brace > 0:
        try:
            data = json.loads(raw[:last_brace + 1] + "]")
            if isinstance(data, list):
                logger.warning(f"theme_locator: AI 输出疑似截断，修复解析出 {len(data)} 个完整候选")
                return data
        except json.JSONDecodeError:
            pass
    return []
