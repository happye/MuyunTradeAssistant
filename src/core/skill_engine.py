"""技能引擎 - 加载和执行YAML定义的技能规则"""

import yaml
import logging
from pathlib import Path
from typing import Optional

from src.data.models import StockData, SkillSignal, SignalType

logger = logging.getLogger(__name__)


class Skill:
    """技能定义"""

    def __init__(self, name: str, alias: str, yaml_path: Path):
        self.name = name
        self.alias = alias
        self.yaml_path = yaml_path
        self.config: dict = {}

    def load(self):
        """加载YAML配置"""
        with open(self.yaml_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        logger.info(f"Loaded skill: {self.name}")

    def execute(self, data: StockData) -> SkillSignal:
        """执行技能，返回信号"""
        raise NotImplementedError


class SkillEngine:
    """技能引擎 - 管理所有技能"""

    def __init__(self, skills_dir: str = "./src/skills", skill_types: Optional[dict[str, str]] = None):
        self.skills_dir = Path(skills_dir)
        self.skills: dict[str, Skill] = {}
        self.skill_types = skill_types or {}  # {skill_name: "base"/"regulator"/"action"}

    def load_skills(self, skill_names: Optional[list[str]] = None):
        """加载指定技能或全部技能"""
        if not self.skills_dir.exists():
            logger.warning(f"Skills dir not found: {self.skills_dir}")
            return

        yaml_files = list(self.skills_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            skill_name = yaml_file.stem
            if skill_names and skill_name not in skill_names:
                continue

            skill = self._load_skill(skill_name, yaml_file)
            if skill:
                self.skills[skill_name] = skill

        logger.info(f"Loaded {len(self.skills)} skills: {list(self.skills.keys())}")

    def _load_skill(self, name: str, yaml_path: Path) -> Optional[Skill]:
        """加载单个技能"""
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            # 从配置或settings获取技能类型
            skill_type = self.skill_types.get(name, config.get("type", "base"))
            return YAMLBasedSkill(name, config, yaml_path, skill_type)
        except Exception as e:
            logger.error(f"Failed to load skill {name}: {e}")
            return None

    def execute_all(self, data: StockData) -> list[SkillSignal]:
        """执行所有技能"""
        signals = []
        for name, skill in self.skills.items():
            try:
                signal = skill.execute(data)
                signals.append(signal)
            except Exception as e:
                logger.error(f"Skill {name} execution failed: {e}")
        return signals

    def get_skill(self, name: str) -> Optional[Skill]:
        """获取指定技能"""
        return self.skills.get(name)


class YAMLBasedSkill(Skill):
    """基于YAML配置的技能"""

    def __init__(self, name: str, config: dict, yaml_path: Path, skill_type: str = "base"):
        """初始化YAML技能

        Args:
            name: 技能名称
            config: YAML解析后的配置字典
            yaml_path: YAML文件路径
            skill_type: 技能类型 (base/regulator/action)
        """
        super().__init__(name, name, yaml_path)  # alias = name
        self.config = config
        self.skill_type = skill_type  # base / regulator / action

    def execute(self, data: StockData) -> SkillSignal:
        """执行YAML定义的技能规则"""
        name = self.config.get("name", self.name)
        alias = self.config.get("alias", self.name)

        # 收集所有命中的规则
        matched_rules = []
        all_reasons = []

        for rule in self.config.get("rules", []):
            rule_signal = rule.get("signal", "HOLD")
            condition = rule.get("condition", {})
            condition_result = self._evaluate_condition(condition, data, require=rule.get("require", "majority"))
            if condition_result["met"]:
                matched_rules.append({
                    "signal": rule_signal,
                    "confidence": rule.get("confidence", 0.5),
                    "weight": rule.get("weight", 1.0),
                    "reasons": condition_result.get("reasons", [])
                })
                all_reasons.extend(condition_result.get("reasons", []))

        # 根据命中的规则数量计算信号
        if matched_rules:
            # 计算加权置信度
            total_weight = sum(r["weight"] for r in matched_rules)
            weighted_conf = sum(r["confidence"] * r["weight"] for r in matched_rules) / total_weight
            all_confidence = min(1.0, weighted_conf)

            # 确定信号：买入条件多且强则BUY
            buy_count = sum(1 for r in matched_rules if r["signal"] == "BUY")
            sell_count = sum(1 for r in matched_rules if r["signal"] == "SELL")

            if buy_count > sell_count and buy_count >= 1:
                all_signal = SignalType.BUY
            elif sell_count > buy_count and sell_count >= 1:
                all_signal = SignalType.SELL
            elif buy_count == sell_count and buy_count > 0:
                all_signal = SignalType.HOLD  # 冲突时保守
            else:
                all_signal = SignalType.HOLD
        else:
            all_signal = SignalType.WATCH
            all_confidence = 0.3

        return SkillSignal(
            skill_name=self.name,
            skill_alias=alias,
            signal=all_signal,
            confidence=round(all_confidence, 2),
            reason=all_reasons[:5],  # 最多5条理由
            metadata={},
            skill_type=self.skill_type
        )

    # ===== 条件注册表 =====
    # 每个条件由 (name, evaluator) 组成
    # evaluator 签名: (data: StockData, params: dict) -> bool
    # params 来自 YAML 中的条件参数，如 {min: -2.0, max: 0.0}
    # 无参数时 params 为空 dict，evaluator 应使用默认阈值

    CONDITION_REGISTRY: dict[str, callable] = {}

    @classmethod
    def _register_conditions(cls):
        """注册所有条件评估函数（只执行一次）"""
        if cls.CONDITION_REGISTRY:
            return  # 已注册

        reg = cls.CONDITION_REGISTRY

        # ===== 均线条件 =====
        def price_above_ma(d, p):
            ma_field = {"ma5": d.ma5, "ma20": d.ma20, "ma60": d.ma60, "ma120": d.ma120, "ma200": d.ma200}
            target = p.get("ma", "ma5")
            ma_val = ma_field.get(target)
            if ma_val is None:
                return False
            pct = p.get("pct")  # 可选：价格高于均线的百分比阈值
            if pct is not None:
                return d.price > ma_val * (1 + pct / 100)
            return d.price > ma_val

        def price_below_ma(d, p):
            ma_field = {"ma5": d.ma5, "ma20": d.ma20, "ma60": d.ma60, "ma120": d.ma120, "ma200": d.ma200}
            target = p.get("ma", "ma5")
            ma_val = ma_field.get(target)
            if ma_val is None:
                return False
            pct = p.get("pct")  # 可选：价格低于均线的百分比阈值
            if pct is not None:
                return d.price < ma_val * (1 - pct / 100)
            return d.price < ma_val

        def ma_cross(d, p):
            """均线交叉判断：fast 上穿/下穿 slow"""
            ma_field = {"ma5": d.ma5, "ma10": d.ma10, "ma20": d.ma20, "ma60": d.ma60}
            fast_name = p.get("fast", "ma5")
            slow_name = p.get("slow", "ma20")
            fast = ma_field.get(fast_name)
            slow = ma_field.get(slow_name)
            if fast is None or slow is None:
                return False
            direction = p.get("direction", "above")  # above=金叉, below=死叉
            if direction == "above":
                return fast > slow
            else:
                return fast < slow

        def ma_distance(d, p):
            """价格偏离均线的百分比"""
            ma_field = {"ma5": d.ma5, "ma20": d.ma20, "ma60": d.ma60}
            target = p.get("ma", "ma20")
            ma_val = ma_field.get(target)
            if ma_val is None or ma_val == 0:
                return False
            dist_pct = (d.price - ma_val) / ma_val * 100
            above = p.get("above")   # 偏离超过此值
            below = p.get("below")   # 偏离低于此值
            if above is not None and dist_pct > above:
                return True
            if below is not None and dist_pct < below:
                return True
            return False

        # ===== 涨跌幅条件 =====
        def change_positive(d, p):
            """涨幅为正，支持 min/max 阈值"""
            if d.change_pct is None:
                return False
            min_val = p.get("min")    # 最小涨幅阈值，如 0.5 表示涨幅至少0.5%
            max_val = p.get("max")    # 最大涨幅阈值，如 2.0 表示涨幅不超过2%（微涨/滞涨）
            if d.change_pct <= 0:
                return False
            if min_val is not None and d.change_pct < min_val:
                return False
            if max_val is not None and d.change_pct > max_val:
                return False
            return True

        def change_negative(d, p):
            """涨幅为负，支持 min/max 阈值"""
            if d.change_pct is None:
                return False
            min_val = p.get("min")    # 最小跌幅阈值（负数），如 -5.0 表示跌幅至少5%
            max_val = p.get("max")    # 最大跌幅阈值（负数），如 -0.5 表示跌幅不超过0.5%（微跌）
            if d.change_pct >= 0:
                return False
            if min_val is not None and d.change_pct < min_val:
                return False
            if max_val is not None and d.change_pct > max_val:
                return False
            return True

        def change_range(d, p):
            """涨跌幅范围判断（通用的精确条件）"""
            if d.change_pct is None:
                return False
            above = p.get("above")  # 涨幅高于此值
            below = p.get("below")  # 涨幅低于此值
            if above is not None and d.change_pct > above:
                return True
            if below is not None and d.change_pct < below:
                return True
            # 如果只给了 min/max 范围
            min_val = p.get("min")
            max_val = p.get("max")
            if min_val is not None and max_val is not None:
                return min_val <= d.change_pct <= max_val
            return False

        # ===== 成交量条件 =====
        def volume_above_avg(d, p):
            """成交量高于均量，支持倍数阈值"""
            if d.avg_volume_20 is None or d.avg_volume_20 == 0:
                return False
            ratio = d.volume / d.avg_volume_20
            min_ratio = p.get("ratio", 1.0)  # 最低量比，默认1.0（超过均量）
            return ratio >= min_ratio

        def volume_below_avg(d, p):
            """成交量低于均量，支持倍数阈值"""
            if d.avg_volume_20 is None or d.avg_volume_20 == 0:
                return False
            ratio = d.volume / d.avg_volume_20
            max_ratio = p.get("ratio", 1.0)  # 最高量比，默认1.0（低于均量）
            return ratio <= max_ratio

        def volume_ratio(d, p):
            """量比精确判断"""
            if d.avg_volume_20 is None or d.avg_volume_20 == 0:
                return False
            ratio = d.volume / d.avg_volume_20
            above = p.get("above")  # 量比高于此值
            below = p.get("below")  # 量比低于此值
            if above is not None and ratio > above:
                return True
            if below is not None and ratio < below:
                return True
            return False

        # ===== 多时间框架条件 =====
        def _get_timeframe_bucket(d, timeframe: str):
            return getattr(d, timeframe, None) or {}

        def timeframe_trend(d, p):
            timeframe = p.get("timeframe")
            expected = str(p.get("trend", "")).upper()
            bucket = _get_timeframe_bucket(d, timeframe)
            actual = str(bucket.get("trend", "")).upper()
            return bool(actual) and actual == expected

        def timeframe_price_above_ma(d, p):
            timeframe = p.get("timeframe")
            ma_name = p.get("ma", "ma20")
            bucket = _get_timeframe_bucket(d, timeframe)
            close = bucket.get("close")
            ma_val = bucket.get(ma_name)
            if close is None or ma_val is None:
                return False
            pct = p.get("pct")
            if pct is not None:
                return close > ma_val * (1 + pct / 100)
            return close > ma_val

        def timeframe_price_below_ma(d, p):
            timeframe = p.get("timeframe")
            ma_name = p.get("ma", "ma20")
            bucket = _get_timeframe_bucket(d, timeframe)
            close = bucket.get("close")
            ma_val = bucket.get(ma_name)
            if close is None or ma_val is None:
                return False
            pct = p.get("pct")
            if pct is not None:
                return close < ma_val * (1 - pct / 100)
            return close < ma_val

        # ===== 位置条件 =====
        def price_near_high(d, p):
            """价格接近N日高点，支持阈值"""
            threshold = p.get("pct", 5)  # 默认5%以内
            if d.high_60d and d.price >= d.high_60d * (1 - threshold / 100):
                return True
            return False

        def price_near_low(d, p):
            """价格接近N日低点，支持阈值"""
            threshold = p.get("pct", 5)  # 默认5%以内
            if d.low_60d and d.price <= d.low_60d * (1 + threshold / 100):
                return True
            return False

        # ===== MACD条件 =====
        def macd_dif_above_dea(d, p): return d.macd_dif is not None and d.macd_dea is not None and d.macd_dif > d.macd_dea
        def macd_dif_below_dea(d, p): return d.macd_dif is not None and d.macd_dea is not None and d.macd_dif < d.macd_dea
        def macd_dif_above_zero(d, p): return d.macd_dif is not None and d.macd_dif > 0
        def macd_dif_below_zero(d, p): return d.macd_dif is not None and d.macd_dif < 0
        def macd_dea_above_zero(d, p): return d.macd_dea is not None and d.macd_dea > 0
        def macd_dea_below_zero(d, p): return d.macd_dea is not None and d.macd_dea < 0
        def macd_hist_positive(d, p): return d.macd_hist is not None and d.macd_hist > 0
        def macd_hist_negative(d, p): return d.macd_hist is not None and d.macd_hist < 0

        # ===== RSI条件 =====
        def rsi_above(d, p):
            """RSI高于阈值，支持自定义周期和阈值"""
            period = p.get("period", 6)  # 默认RSI6
            threshold = p.get("threshold", 70)  # 默认70
            rsi_val = {6: d.rsi_6, 12: d.rsi_12, 24: d.rsi_24}.get(period)
            return rsi_val is not None and rsi_val > threshold

        def rsi_below(d, p):
            """RSI低于阈值，支持自定义周期和阈值"""
            period = p.get("period", 6)
            threshold = p.get("threshold", 30)
            rsi_val = {6: d.rsi_6, 12: d.rsi_12, 24: d.rsi_24}.get(period)
            return rsi_val is not None and rsi_val < threshold

        # ===== 布林带条件 =====
        def price_above_boll_upper(d, p): return d.boll_upper is not None and d.price > d.boll_upper
        def price_below_boll_lower(d, p): return d.boll_lower is not None and d.price < d.boll_lower
        def price_near_boll_upper(d, p):
            pct = p.get("pct", 5)
            return d.boll_upper is not None and d.price > d.boll_upper * (1 - pct / 100)
        def price_near_boll_lower(d, p):
            pct = p.get("pct", 5)
            return d.boll_lower is not None and d.price < d.boll_lower * (1 + pct / 100)
        def boll_width_narrow(d, p):
            threshold = p.get("threshold", 0.1)
            return (d.boll_upper is not None and d.boll_lower is not None
                    and (d.boll_upper - d.boll_lower) / d.boll_mid < threshold)
        def boll_width_expand(d, p):
            threshold = p.get("threshold", 0.15)
            return (d.boll_upper is not None and d.boll_lower is not None
                    and (d.boll_upper - d.boll_lower) / d.boll_mid > threshold)

        # ===== KDJ条件 =====
        def kdj_k_above_d(d, p): return d.kdj_k is not None and d.kdj_d is not None and d.kdj_k > d.kdj_d
        def kdj_k_below_d(d, p): return d.kdj_k is not None and d.kdj_d is not None and d.kdj_k < d.kdj_d
        def kdj_k_above(d, p): return d.kdj_k is not None and d.kdj_k > p.get("threshold", 80)
        def kdj_k_below(d, p): return d.kdj_k is not None and d.kdj_k < p.get("threshold", 20)
        def kdj_j_above(d, p): return d.kdj_j is not None and d.kdj_j > p.get("threshold", 80)
        def kdj_j_below(d, p): return d.kdj_j is not None and d.kdj_j < p.get("threshold", 20)

        # ===== 注册所有条件 =====
        # 旧格式兼容名 → 新函数
        reg["price_above_ma5"] = lambda d, p: price_above_ma(d, {**p, "ma": "ma5"})
        reg["price_below_ma5"] = lambda d, p: price_below_ma(d, {**p, "ma": "ma5"})
        reg["price_above_ma20"] = lambda d, p: price_above_ma(d, {**p, "ma": "ma20"})
        reg["price_below_ma20"] = lambda d, p: price_below_ma(d, {**p, "ma": "ma20"})
        reg["price_above_ma60"] = lambda d, p: price_above_ma(d, {**p, "ma": "ma60"})
        reg["price_below_ma60"] = lambda d, p: price_below_ma(d, {**p, "ma": "ma60"})
        reg["ma5_above_ma20"] = lambda d, p: ma_cross(d, {**p, "fast": "ma5", "slow": "ma20", "direction": "above"})
        reg["ma5_below_ma20"] = lambda d, p: ma_cross(d, {**p, "fast": "ma5", "slow": "ma20", "direction": "below"})
        reg["ma20_above_ma60"] = lambda d, p: ma_cross(d, {**p, "fast": "ma20", "slow": "ma60", "direction": "above"})
        reg["monthly_trend_bullish"] = lambda d, p: timeframe_trend(d, {**p, "timeframe": "monthly", "trend": "BULLISH"})
        reg["monthly_trend_bearish"] = lambda d, p: timeframe_trend(d, {**p, "timeframe": "monthly", "trend": "BEARISH"})
        reg["weekly_trend_bullish"] = lambda d, p: timeframe_trend(d, {**p, "timeframe": "weekly", "trend": "BULLISH"})
        reg["weekly_trend_bearish"] = lambda d, p: timeframe_trend(d, {**p, "timeframe": "weekly", "trend": "BEARISH"})
        reg["weekly_price_above_ma20"] = lambda d, p: timeframe_price_above_ma(d, {**p, "timeframe": "weekly", "ma": "ma20"})
        reg["weekly_price_below_ma20"] = lambda d, p: timeframe_price_below_ma(d, {**p, "timeframe": "weekly", "ma": "ma20"})
        reg["monthly_price_above_ma20"] = lambda d, p: timeframe_price_above_ma(d, {**p, "timeframe": "monthly", "ma": "ma20"})
        reg["monthly_price_below_ma20"] = lambda d, p: timeframe_price_below_ma(d, {**p, "timeframe": "monthly", "ma": "ma20"})

        # 新通用条件名
        reg["price_above_ma"] = price_above_ma
        reg["price_below_ma"] = price_below_ma
        reg["ma_cross"] = ma_cross
        reg["ma_distance"] = ma_distance
        reg["timeframe_trend"] = timeframe_trend
        reg["timeframe_price_above_ma"] = timeframe_price_above_ma
        reg["timeframe_price_below_ma"] = timeframe_price_below_ma

        # 涨跌幅
        reg["change_positive"] = change_positive
        reg["change_negative"] = change_negative
        reg["change_range"] = change_range

        # 成交量
        reg["volume_above_avg"] = volume_above_avg
        reg["volume_below_avg"] = volume_below_avg
        reg["volume_ratio"] = volume_ratio

        # 位置
        reg["price_near_high"] = price_near_high
        reg["price_near_low"] = price_near_low

        # MACD
        reg["macd_dif_above_dea"] = macd_dif_above_dea
        reg["macd_dif_below_dea"] = macd_dif_below_dea
        reg["macd_dif_above_zero"] = macd_dif_above_zero
        reg["macd_dif_below_zero"] = macd_dif_below_zero
        reg["macd_dea_above_zero"] = macd_dea_above_zero
        reg["macd_dea_below_zero"] = macd_dea_below_zero
        reg["macd_hist_positive"] = macd_hist_positive
        reg["macd_hist_negative"] = macd_hist_negative

        # RSI（旧格式兼容 + 新通用）
        reg["rsi_6_above_70"] = lambda d, p: rsi_above(d, {**p, "period": 6, "threshold": 70})
        reg["rsi_6_below_30"] = lambda d, p: rsi_below(d, {**p, "period": 6, "threshold": 30})
        reg["rsi_6_above_50"] = lambda d, p: rsi_above(d, {**p, "period": 6, "threshold": 50})
        reg["rsi_6_below_50"] = lambda d, p: rsi_below(d, {**p, "period": 6, "threshold": 50})
        reg["rsi_12_above_70"] = lambda d, p: rsi_above(d, {**p, "period": 12, "threshold": 70})
        reg["rsi_12_below_30"] = lambda d, p: rsi_below(d, {**p, "period": 12, "threshold": 30})
        reg["rsi_24_above_70"] = lambda d, p: rsi_above(d, {**p, "period": 24, "threshold": 70})
        reg["rsi_24_below_30"] = lambda d, p: rsi_below(d, {**p, "period": 24, "threshold": 30})
        reg["rsi_above"] = rsi_above
        reg["rsi_below"] = rsi_below

        # 布林带
        reg["price_above_boll_upper"] = price_above_boll_upper
        reg["price_below_boll_lower"] = price_below_boll_lower
        reg["price_near_boll_upper"] = price_near_boll_upper
        reg["price_near_boll_lower"] = price_near_boll_lower
        reg["boll_width_narrow"] = boll_width_narrow
        reg["boll_width_expand"] = boll_width_expand

        # KDJ
        reg["kdj_k_above_d"] = kdj_k_above_d
        reg["kdj_k_below_d"] = kdj_k_below_d
        reg["kdj_k_above_80"] = lambda d, p: kdj_k_above(d, {**p, "threshold": 80})
        reg["kdj_k_below_20"] = lambda d, p: kdj_k_below(d, {**p, "threshold": 20})
        reg["kdj_j_above_80"] = lambda d, p: kdj_j_above(d, {**p, "threshold": 80})
        reg["kdj_j_below_20"] = lambda d, p: kdj_j_below(d, {**p, "threshold": 20})
        reg["kdj_k_above"] = kdj_k_above
        reg["kdj_k_below"] = kdj_k_below
        reg["kdj_j_above"] = kdj_j_above
        reg["kdj_j_below"] = kdj_j_below

        # ===== 大盘环境条件 =====
        def index_bullish(d, p):
            """大盘处于牛市趋势"""
            return d.index_trend == "BULLISH"

        def index_bearish(d, p):
            """大盘处于熊市趋势"""
            return d.index_trend == "BEARISH"

        def index_neutral(d, p):
            """大盘趋势中性"""
            return d.index_trend == "NEUTRAL" or d.index_trend is None

        reg["index_bullish"] = index_bullish
        reg["index_bearish"] = index_bearish
        reg["index_neutral"] = index_neutral

        # ===== 超跌到底确认条件（oversold_confirm skill 用）=====
        def change_n_below(d, p, n_days):
            """近N日跌幅阈值（threshold为负数，如-8.0表示跌幅>=8%）"""
            field_map = {5: d.change_5d, 20: d.change_20d, 120: d.change_120d}
            val = field_map.get(n_days)
            if val is None:
                return False
            threshold = p.get("threshold")
            if threshold is None:
                return False
            return val <= threshold

        def change_5d_below(d, p):
            return change_n_below(d, p, 5)

        def change_20d_below(d, p):
            return change_n_below(d, p, 20)

        def change_120d_below(d, p):
            return change_n_below(d, p, 120)

        def relative_drop_to_index(d, p):
            """个股20日跌幅 - 大盘20日跌幅 = 超额跌幅（适配极端市场，只筛跌幅远超大盘的错杀）"""
            if d.change_20d is None or d.index_change_20d is None:
                return False
            excess = d.change_20d - d.index_change_20d
            threshold = p.get("threshold", -15.0)
            return excess <= threshold

        def gain_from_60d_high_below(d, p):
            """距60日高点跌幅（回撤深度，threshold负数如-20.0）"""
            if d.high_60d is None or d.high_60d <= 0 or d.price is None:
                return False
            drop = (d.price - d.high_60d) / d.high_60d * 100
            threshold = p.get("threshold", -20.0)
            return drop <= threshold

        def rsi_bottom_divergence(d, p):
            """RSI底背离：近20日价格创新低但RSI未创新低（RSI低点比前低高>=5）"""
            if not d.close_series or len(d.close_series) < 10 or not d.rsi_6_series or len(d.rsi_6_series) < 10:
                return False
            mid = len(d.close_series) // 2
            prev_low = min(d.close_series[:mid]) if d.close_series[:mid] else None
            curr_low = min(d.close_series[mid:]) if d.close_series[mid:] else None
            if prev_low is None or curr_low is None or curr_low >= prev_low:
                return False  # 未创新低
            rsi_mid = len(d.rsi_6_series) // 2
            prev_rsi = min(d.rsi_6_series[:rsi_mid]) if d.rsi_6_series[:rsi_mid] else 100
            curr_rsi = min(d.rsi_6_series[rsi_mid:]) if d.rsi_6_series[rsi_mid:] else 100
            return curr_rsi >= prev_rsi + 5

        def volume_shrink_stop(d, p):
            """缩量止跌：近3日>=2日缩量(<0.8均量)且当日跌幅收窄到-2%以内"""
            if not d.volume_series or len(d.volume_series) < 3 or d.avg_volume_20 is None or d.avg_volume_20 == 0:
                return False
            recent_3 = d.volume_series[-3:]
            shrink_days = sum(1 for v in recent_3 if v < d.avg_volume_20 * 0.8)
            if shrink_days < 2:
                return False
            if d.change_pct is None or d.change_pct <= -2.0:
                return False
            return True

        def long_lower_shadow(d, p):
            """长下影线：下影线占振幅>ratio(默认0.5)"""
            if d.open is None or d.high is None or d.low is None or d.price is None:
                return False
            amplitude = d.high - d.low
            if amplitude <= 0:
                return False
            lower_shadow = min(d.open, d.price) - d.low
            ratio = p.get("ratio", 0.5)
            return (lower_shadow / amplitude) > ratio

        def not_monthly_weekly_bearish(d, p):
            """非月周共振下跌（防追跌：月周都BEARISH=下跌中继不抄）"""
            monthly = d.monthly or {}
            weekly = d.weekly or {}
            m_trend = monthly.get("trend")
            w_trend = weekly.get("trend")
            return not (m_trend == "BEARISH" and w_trend == "BEARISH")

        def not_st(d, p):
            """非ST股"""
            return not (d.stock_name or "").startswith("ST")

        def no_loss_forecast(d, p):
            """无业绩预亏/预减/减持公告（硬否决基本面恶化）"""
            if not d.recent_announcements:
                return True  # 无公告数据，不否决（降级）
            keywords = ["预亏", "预减", "亏损", "业绩下降", "减持", "立案", "警示"]
            for ann in d.recent_announcements:
                title = ann.get("title", "") if isinstance(ann, dict) else str(ann)
                if any(kw in title for kw in keywords):
                    return False
            return True

        def price_back_above_ma5(d, p):
            """站回MA5（短期止跌确认）"""
            return d.ma5 is not None and d.price is not None and d.price > d.ma5

        def stabilization_signals(d, p):
            """企稳信号复合条件：>=min_match项子条件满足才通过（区分下跌中继vs真到底）"""
            min_match = p.get("min_match", 2)
            met = 0
            if rsi_bottom_divergence(d, {}):
                met += 1
            if volume_shrink_stop(d, {}):
                met += 1
            if long_lower_shadow(d, {}):
                met += 1
            if price_back_above_ma5(d, {}):
                met += 1
            if boll_width_narrow(d, {"threshold": 0.05}):
                met += 1
            return met >= min_match

        reg["change_5d_below"] = change_5d_below
        reg["change_20d_below"] = change_20d_below
        reg["change_120d_below"] = change_120d_below
        reg["relative_drop_to_index"] = relative_drop_to_index
        reg["gain_from_60d_high_below"] = gain_from_60d_high_below
        reg["rsi_bottom_divergence"] = rsi_bottom_divergence
        reg["volume_shrink_stop"] = volume_shrink_stop
        reg["long_lower_shadow"] = long_lower_shadow
        reg["not_monthly_weekly_bearish"] = not_monthly_weekly_bearish
        reg["not_st"] = not_st
        reg["no_loss_forecast"] = no_loss_forecast
        reg["price_back_above_ma5"] = price_back_above_ma5
        reg["stabilization_signals"] = stabilization_signals

    def _evaluate_condition(self, condition: dict, data: StockData, require: str = "majority") -> dict:
        """评估单个条件块（支持参数化条件）"""
        # 确保条件注册表已初始化
        self._register_conditions()

        result = {
            "met": False,
            "signal": "HOLD",
            "confidence": 0.5,
            "weight": 1.0,
            "reasons": []
        }

        conditions_met = 0
        total_conditions = 0

        for cond_name, cond_value in condition.items():
            # 跳过非条件字段（condition块内的元数据字段）
            if cond_name in ("signal", "weight", "confidence", "require"):
                continue

            # 价格相对位置（特殊条件，保持原有逻辑）
            if cond_name == "price_position":
                total_conditions += 1
                pos = cond_value
                if data.high_60d and data.low_60d:
                    price_range = data.high_60d - data.low_60d
                    if price_range > 0:
                        position = (data.price - data.low_60d) / price_range
                        pos_reason = pos.get("reason", f"价格处于60日区间的{int(position*100)}%位置")
                        if pos.get("above") and position > pos["above"] / 100:
                            conditions_met += 1
                            result["reasons"].append(pos_reason)
                        elif pos.get("below") and position < pos["below"] / 100:
                            conditions_met += 1
                            result["reasons"].append(pos_reason)
                continue

            # 在注册表中查找条件评估函数
            evaluator = self.CONDITION_REGISTRY.get(cond_name)
            if evaluator is None:
                logger.debug(f"未知条件: {cond_name}，跳过")
                continue

            total_conditions += 1

            # 解析条件参数
            params = {}
            if isinstance(cond_value, dict):
                params = {k: v for k, v in cond_value.items() if k != "reason"}
            # 如果cond_value不是dict（向后兼容旧的字符串格式），params为空dict

            try:
                if evaluator(data, params):
                    conditions_met += 1
                    # 收集原因
                    if isinstance(cond_value, dict):
                        reason = cond_value.get("reason", cond_name)
                    else:
                        reason = cond_name
                    result["reasons"].append(reason)
            except Exception as e:
                logger.debug(f"条件 {cond_name} 评估异常: {e}")

        # 信号和权重
        result["signal"] = condition.get("signal", "HOLD")
        result["weight"] = condition.get("weight", 1.0)
        result["confidence"] = condition.get("confidence", 0.5)

        # 判断条件是否满足（require: all全满足/any任一/majority多数，默认majority向后兼容）
        if require == "all":
            result["met"] = total_conditions > 0 and conditions_met >= total_conditions
        elif require == "any":
            result["met"] = conditions_met >= 1
        else:
            result["met"] = conditions_met >= max(1, total_conditions // 2)

        return result
