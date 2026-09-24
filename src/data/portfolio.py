"""持仓记录管理器 - 方案B（自动+手动混合）

功能：
1. 读取 portfolio.yaml → 初始化 StrategyState
2. 分析后建议更新 → Y/N 交互确认
3. 支持手动添加/删除/列出持仓

设计原则：
- 持仓数据必须真实，系统只建议不强制
- YAML格式透明可编辑，用户随时可手动修改
- 系统自动维护strategy_state，用户一般不需碰
- 并发写保护（M5，v0.8.15）：保存前比对磁盘内容指纹（sha256），被外部/其他
  实例改过 → 拒绝写入 + 内存回滚磁盘版，绝不静默覆盖较新改动；mutator 返回
  bool 如实上报保存成败
"""

import hashlib
import math
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import NamedTuple, Optional

import yaml

from src.data.models import (
    StrategyState, TradeLifecycle, SignalType, TradePlan
)

logger = logging.getLogger(__name__)

# 默认持仓文件路径（项目根目录）
DEFAULT_PORTFOLIO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "portfolio.yaml"
)


class SaveResult(NamedTuple):
    """持仓保存结果（M5，plan/TECHNICAL_HANDOFF §6）。

    ok=True 已写入磁盘；ok=False 且 conflict=True = 磁盘被外部/其他实例修改，
    本次改动被拒绝且内存已回滚为磁盘版本（防旧快照覆盖较新改动）；
    ok=False 且 conflict=False = 损坏保护/IO 失败，内存不变。
    """
    ok: bool
    conflict: bool = False


def _file_fingerprint(path) -> Optional[str]:
    """文件内容指纹（sha256 hex）；文件不存在/不可读返回 None。"""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _validate_ratio(value: float, field: str = "仓位比例") -> float:
    """持仓比例合法性校验（ISS-078）：必须为有限数值且落在 [0, 1]。

    背景：add 路径此前零校验（-0.5/NaN 实测可落盘，总仓位可被污染成负数）；
    NaN/Inf 因 `nan < 0` 为 False 绕过 update 路径的简单比较。入口多为 AI
    供参的 manage_portfolio，数据层必须兜底。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field}需为有限数值，收到 {value!r}")
    if value < 0 or value > 1:
        raise ValueError(f"{field}需在 0-1 之间（0%-100%），收到 {value}")
    return float(value)


def _validate_price(value: float, field: str = "开仓价") -> float:
    """开仓价合法性校验（ISS-078）：必须为有限正数。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field}需为有限数值，收到 {value!r}")
    if value <= 0:
        raise ValueError(f"{field}需为正数，收到 {value}")
    return float(value)


class PositionRecord:
    """单只股票的持仓记录"""

    def __init__(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_date: Optional[str] = None,
        entry_price: Optional[float] = None,
        current_ratio: float = 0.0,
        last_action: str = "OPEN",
        last_action_semantic: Optional[str] = None,
        last_sell_path: Optional[str] = None,
        last_action_date: Optional[str] = None,
        high_since_entry: Optional[float] = None,
        lifecycle: str = "FLAT",
        strategy_state: Optional[dict] = None,
        trade_plan: Optional[TradePlan] = None,
        trade_plan_raw: Optional[dict] = None,
    ):
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.current_ratio = current_ratio
        self.last_action = last_action
        self.last_action_semantic = last_action_semantic
        self.last_sell_path = last_sell_path
        self.last_action_date = last_action_date
        self.high_since_entry = high_since_entry
        self.lifecycle = lifecycle
        self.strategy_state = strategy_state or {}
        self.trade_plan = trade_plan  # v0.8.5 TradePlan 子系统
        # ISS-078：加载时无法解析的 trade_plan 原始 dict 原样带回（保存时原样写回），
        # 防"加载失败→保存→静默抹除计划数据"的第二层丢失
        self.trade_plan_raw = trade_plan_raw

    def to_dict(self) -> dict:
        """转为YAML可序列化的字典"""
        d = {
            "stock_name": self.stock_name,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "current_ratio": self.current_ratio,
            "last_action": self.last_action,
            "last_action_semantic": self.last_action_semantic,
            "last_sell_path": self.last_sell_path,
            "last_action_date": self.last_action_date,
            "high_since_entry": self.high_since_entry,
            "lifecycle": self.lifecycle,
            "strategy_state": self.strategy_state,
        }
        if self.trade_plan is not None:
            # Pydantic v2: model_dump() 转 dict；YAML 兼容
            d["trade_plan"] = self.trade_plan.model_dump()
        elif self.trade_plan_raw is not None:
            d["trade_plan"] = self.trade_plan_raw
        return d

    @classmethod
    def from_dict(cls, stock_code: str, data: dict) -> "PositionRecord":
        """从YAML字典创建"""
        plan_data = data.get("trade_plan")
        trade_plan = None
        trade_plan_raw = None
        if plan_data:
            # ISS-078：残缺 trade_plan 不再让整个持仓加载崩溃（此前 TradePlan(**plan_data)
            # 的 ValidationError 会拖垮 get_position/list_positions 全体），原始数据保留
            try:
                trade_plan = TradePlan(**plan_data)
            except Exception as e:
                logger.error(
                    f"持仓 {stock_code} 的 trade_plan 无法解析（字段残缺或类型不对），"
                    f"原始数据已保留、分析时按无计划处理；修复字段后可恢复: {e}")
                trade_plan_raw = plan_data if isinstance(plan_data, dict) else None
        return cls(
            stock_code=stock_code,
            stock_name=data.get("stock_name", ""),
            entry_date=data.get("entry_date"),
            entry_price=data.get("entry_price"),
            current_ratio=data.get("current_ratio", 0.0),
            last_action=data.get("last_action", "OPEN"),
            last_action_semantic=data.get("last_action_semantic"),
            last_sell_path=data.get("last_sell_path"),
            last_action_date=data.get("last_action_date"),
            high_since_entry=data.get("high_since_entry"),
            lifecycle=data.get("lifecycle", "FLAT"),
            strategy_state=data.get("strategy_state", {}),
            trade_plan=trade_plan,
            trade_plan_raw=trade_plan_raw,
        )


class PortfolioManager:
    """持仓记录管理器

    用法：
        pm = PortfolioManager()
        # 读取持仓
        pos = pm.get_position("002192")
        # 转为StrategyState
        state = pm.to_strategy_state("002192")
        # 分析后更新
        # 手动操作
        pm.add_position("002192", stock_name="融捷股份", entry_price=35.20, ratio=0.20)
        pm.list_positions()
        pm.remove_position("002192")
    """

    def __init__(self, portfolio_path: Optional[str] = None):
        self.portfolio_path = portfolio_path or DEFAULT_PORTFOLIO_PATH
        self._data: dict = {}
        self._corrupted = False  # G01：加载失败置位，禁止 _save 覆盖真实持仓
        self._load()

    DEFAULT_MIN_HOLD_DAYS = 5

    def _load(self):
        """加载portfolio.yaml

        v0.8.7.8 裁决修复 G01：解析失败不再 fail-open 清空（原实现把损坏文件当
        "空仓"继续跑，下一次 _save 就把真实持仓静默抹掉——实测 1 只 → 0）。
        现置 _corrupted=True，_save 拒绝覆盖，损坏文件原样保留供人工修复。
        G04：键规范化——无引号数字键被 YAML 解析为 int，has_position("600519")
        查不到但总仓位照算（隐形持仓），统一转 str。
        """
        if not os.path.exists(self.portfolio_path):
            logger.info(f"持仓文件不存在，将创建: {self.portfolio_path}")
            self._data = {"positions": {}}
            self._loaded_fingerprint = None
            return

        try:
            with open(self.portfolio_path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {"positions": {}}
            positions = self._data.get("positions") or {}
            self._data["positions"] = {
                (str(k).strip().zfill(6) if str(k).strip().isdigit() else str(k).strip()): v
                for k, v in positions.items()
            }
            self._corrupted = False
            # M5（升级 ISS-078 的 mtime 基线）：记录加载时的内容指纹，
            # _save 据此检测会话外修改（内容级比对，touch 不误报）
            self._loaded_fingerprint = _file_fingerprint(self.portfolio_path)
            logger.info(f"持仓文件已加载: {self.portfolio_path}")
        except Exception as e:
            logger.error(
                f"持仓文件加载失败（已置损坏保护：后续保存将被拒绝，"
                f"请修复或删除 {self.portfolio_path} 后重试）: {e}")
            self._data = {"positions": {}}
            self._corrupted = True
            self._loaded_fingerprint = None

    def _save(self) -> SaveResult:
        """保存到portfolio.yaml

        v0.8.7.8 裁决修复 G01：
        - _corrupted 置位时拒绝写入（宁可不保存也不静默清掉真实持仓）
        - 原子写：先写 tmp 再 os.replace，进程被杀/磁盘满不再产生半截文件
        - 每次保存前保留 .bak 单份滚动备份；批量删仓（≥4→减半）额外留
          portfolio.yaml.before_<时间戳> 快照（合法操作放行，但数据可追溯）

        M5（v0.8.15）并发写保护：保存前比对磁盘内容指纹——磁盘文件在本实例
        加载后被外部/其他实例改过（内容变化）→ 拒绝写入并回滚内存为磁盘版，
        宁可让用户重做一次操作也不静默覆盖较新改动（原 ISS-078 实现只告警仍
        覆盖，形同虚设）。内容未变的 touch（编辑器保存/复制）不算冲突。
        """
        if getattr(self, "_corrupted", False):
            logger.error("G01 损坏保护生效：portfolio.yaml 此前加载失败，本次保存被拒绝。"
                         "请人工修复该文件（或删除后重试），期间持仓改动仅保留在内存")
            return SaveResult(False, False)

        # M5 外部修改检测：磁盘有文件且内容 ≠ 本实例加载时的内容 → 冲突拒绝。
        # 磁盘无文件 = 无外部内容可覆盖（含"外部删除后重建"工作流），照常写入。
        disk_fp = _file_fingerprint(self.portfolio_path)
        if disk_fp is not None and disk_fp != getattr(self, "_loaded_fingerprint", None):
            self._load()   # 内存回滚：与磁盘真值重新同步（先回滚再告警，措辞才属实）
            logger.warning(
                "持仓文件已被其他会话或编辑器修改，本次改动被拒绝（未覆盖外部改动），"
                "内存已恢复为磁盘最新版本——请核对后重新操作")
            return SaveResult(False, True)

        os.makedirs(os.path.dirname(self.portfolio_path) or ".", exist_ok=True)

        # 快照保护：已有 4 只以上持仓、本次要写掉一半以上时，先留快照（不阻断合法删仓）
        try:
            with open(self.portfolio_path, 'r', encoding='utf-8') as f:
                old_data = yaml.safe_load(f) or {}
            old_count = len(old_data.get("positions", {}) or {})
            new_count = len(self._data.get("positions", {}) or {})
            if old_count >= 4 and new_count < old_count / 2:
                snap = f"{self.portfolio_path}.before_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                import shutil
                shutil.copy2(self.portfolio_path, snap)
                logger.warning(
                    f"G01 快照保护：本次保存持仓数 {old_count}→{new_count} 减半以上，"
                    f"保存前快照已留 {snap}（合法批量删仓请忽略此告警）")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"G01 快照保护读取旧文件失败（不影响保存）: {e}")

        # 备份上一版（滚动单份）
        try:
            if os.path.exists(self.portfolio_path):
                import shutil
                shutil.copy2(self.portfolio_path, self.portfolio_path + ".bak")
        except OSError as e:
            logger.warning(f"G01 .bak 备份失败（继续保存）: {e}")

        tmp_path = self.portfolio_path + ".tmp"
        try:
            header = (
                "# ============================================================\n"
                "# 暮云思辨投资助手 - 持仓记录本\n"
                "# ============================================================\n"
                "#\n"
                "# 使用说明：\n"
                "# 1. 实时分析时系统会自动读取此文件，获取你的持仓状态\n"
                "# 2. 分析完成后，系统会建议更新持仓，输入Y确认/N跳过\n"
                "# 3. 你也可以手动编辑此文件（如在外部做了交易操作）\n"
                "#\n"
                "# 重要：系统决策依赖此数据的准确性！\n"
                "#   - 如果你在券商APP做了买卖，请及时手动更新此文件\n"
                "#   - lifecycle字段说明：FLAT=空仓 OPEN=新开仓 HOLD=持仓 EXIT=退出过程 COOLDOWN=冷却期\n"
                "#   - strategy_state由系统自动维护，一般不需要手动修改\n"
                "#\n"
                "# ============================================================\n\n"
            )
            body = yaml.dump(
                self._data,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            content = header + body
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            # G01 原子替换：tmp 完整写完后一次性替换，杜绝半截文件
            os.replace(tmp_path, self.portfolio_path)
            self._corrupted = False
            # 自己的写入不算"会话外修改"：指纹直接取刚写入的字节（不重读文件，
            # 消除重读被杀软/备份工具瞬时独占返回 None 造成的下轮误拒窗口）。
            # 文本模式写入时 '\n' 会被翻译成 os.linesep（Windows=CRLF），
            # 哈希必须对翻译后的实际字节算，否则下轮加载比对必假冲突
            self._loaded_fingerprint = hashlib.sha256(
                content.replace("\n", os.linesep).encode("utf-8")).hexdigest()
            logger.info(f"持仓文件已保存: {self.portfolio_path}")
            return SaveResult(True, False)
        except Exception as e:
            logger.error(f"持仓文件保存失败: {e}")
            return SaveResult(False, False)

    # ===== 读取操作 =====

    def get_position(self, stock_code: str) -> Optional[PositionRecord]:
        """获取指定股票的持仓记录"""
        positions = self._data.get("positions", {})
        pos_data = positions.get(stock_code)
        if pos_data is None:
            return None
        return PositionRecord.from_dict(stock_code, pos_data)

    def has_position(self, stock_code: str) -> bool:
        """是否持有指定股票"""
        return stock_code in self._data.get("positions", {})

    def list_positions(self) -> list[PositionRecord]:
        """列出所有持仓"""
        positions = self._data.get("positions", {})
        return [
            PositionRecord.from_dict(code, data)
            for code, data in positions.items()
        ]

    def get_total_position_ratio(self) -> float:
        """所有持仓 current_ratio 之和（0-1+，可能>1表示满仓+杠杆）。

        报告2.3 笨总教学十"永不满仓留底牌"：建议总仓位<=80%，预留20%底牌。
        """
        return sum(p.current_ratio for p in self.list_positions())

    def to_strategy_state(self, stock_code: str) -> StrategyState:
        """将持仓记录转为StrategyState（供策略层使用）

        如果没有持仓记录，返回默认的FLAT状态。
        """
        pos = self.get_position(stock_code)
        if pos is None:
            return StrategyState()  # 默认FLAT

        # 解析lifecycle
        try:
            lifecycle = TradeLifecycle(pos.lifecycle)
        except ValueError:
            lifecycle = TradeLifecycle.FLAT

        # 从strategy_state字典恢复
        ss = pos.strategy_state or {}

        # 解析last_decision
        last_decision = None
        if ss.get("last_decision"):
            try:
                last_decision = SignalType(ss["last_decision"])
            except ValueError:
                last_decision = None

        return StrategyState(
            lifecycle=lifecycle,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            current_position_ratio=pos.current_ratio,
            recent_signals=ss.get("recent_signals", []),
            inertia_counter=ss.get("inertia_counter", 0),
            last_decision=last_decision,
            cooldown_remaining=ss.get("cooldown_remaining", 0),
            cooldown_reason=ss.get("cooldown_reason"),
            reverse_count=ss.get("reverse_count", 0),
            total_commission_paid=ss.get("total_commission_paid", 0.0),
            signal_stability_score=ss.get("signal_stability_score", 1.0),
            reduce_protection_remaining=ss.get("reduce_protection_remaining", 0),
            min_hold_remaining=ss.get("min_hold_remaining", 0),
            add_protection_remaining=ss.get("add_protection_remaining", 0),
            last_tick_date=ss.get("last_tick_date"),
        )

    # ===== 写入操作 =====

    def add_position(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_price: Optional[float] = None,
        ratio: float = 0.20,
        lifecycle: str = "OPEN",
    ) -> bool:
        """手动添加持仓

        ISS-078：ratio/entry_price 走数据层校验——此前本路径零校验，
        负数/NaN/Inf 仓位实测可落盘并污染总仓位计算（AI 供参入口必须兜底）。

        Returns (M5): True 保存成功；False 保存失败（冲突被拒绝/IO 失败，
            详见告警——调用方不得报假成功）
        """
        ratio = _validate_ratio(ratio)
        if entry_price is not None:
            entry_price = _validate_price(entry_price)
        else:
            # ISS-091：无开仓价的持仓，止损/止盈分级判定（依赖未实现收益%）全部
            # 失效，SELL 退化为可被 PlanGuard 压制的 weak_sell——建仓时必须警示
            logger.warning(
                f"add_position {stock_code}: 未提供开仓价——该持仓的止损/止盈分级判定"
                f"将失效（仅剩 trade_plan.current_stop 兜底），强烈建议补录价格")
        today = datetime.now().strftime("%Y-%m-%d")
        record = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_date=today,
            entry_price=entry_price,
            current_ratio=ratio,
            last_action="OPEN",
            last_action_date=today,
            lifecycle=lifecycle,
            strategy_state={
                "cooldown_remaining": 0,
                "cooldown_reason": None,
                "reverse_count": 0,
                "inertia_counter": 1,
                "last_decision": "BUY",
                "recent_signals": ["BUY"],
                "signal_stability_score": 1.0,
                "reduce_protection_remaining": 0,
                "min_hold_remaining": self.DEFAULT_MIN_HOLD_DAYS if lifecycle == "OPEN" else 0,
                "add_protection_remaining": 0,
                "last_tick_date": None,
            }
        )

        if "positions" not in self._data:
            self._data["positions"] = {}
        self._data["positions"][stock_code] = record.to_dict()
        return self._save().ok

    def remove_position(self, stock_code: str) -> bool:
        """删除持仓记录。

        Returns (M5): True 删除并保存成功；False 无此记录或保存失败（详见告警）
        """
        positions = self._data.get("positions", {})
        if stock_code in positions:
            del positions[stock_code]
            return self._save().ok
        return False

    def update_position_fields(
        self,
        stock_code: str,
        current_ratio: Optional[float] = None,
        entry_price: Optional[float] = None,
        stock_name: Optional[str] = None,
    ) -> bool:
        """字段级修改已有持仓（v0.8.8 chat 持仓编辑）。

        只改用户可感知的三字段，strategy_state/lifecycle 等系统维护字段不动。
        走 _save()（原子写 + .bak 备份 + 损坏保护）。

        Returns:
            True 修改成功；False 持仓不存在 / 没有可改项 / 保存失败
            （M5：冲突被拒绝或 IO 失败，详见告警）
        """
        positions = self._data.get("positions", {})
        if stock_code not in positions:
            return False

        rec = positions[stock_code]
        # ISS-078 监督审查 P3：先全部校验再落内存——此前 ratio 校验通过已写入、
        # price 校验再抛异常会留下内存/磁盘短暂 desync
        new_ratio = _validate_ratio(current_ratio) if current_ratio is not None else None
        new_price = _validate_price(entry_price) if entry_price is not None else None
        changed = False
        if new_ratio is not None:
            rec["current_ratio"] = new_ratio
            changed = True
        if new_price is not None:
            rec["entry_price"] = new_price
            changed = True
        if stock_name is not None and stock_name.strip():
            rec["stock_name"] = stock_name.strip()
            changed = True

        if not changed:
            return False
        return self._save().ok

    def attach_plan(self, stock_code: str, plan: TradePlan) -> bool:
        """为已存在持仓附加 TradePlan（v0.8.5）。

        Args:
            stock_code: 股票代码
            plan: TradePlan 实例（建议由 TradePlanGenerator 生成 + 用户编辑后传入）

        Returns:
            True 表示附加成功；False 表示持仓不存在或保存失败（M5 起如实传播）
        """
        if "positions" not in self._data or stock_code not in self._data["positions"]:
            return False
        self._data["positions"][stock_code]["trade_plan"] = plan.model_dump()
        return self._save().ok

    def update_from_strategy_decision(
        self,
        stock_code: str,
        stock_name: str,
        strategy_decision,  # StrategyDecision
        stock_data,  # StockData
        price: Optional[float] = None,
    ) -> bool:
        """根据策略层决策结果更新持仓记录

        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            strategy_decision: 策略层决策结果（StrategyDecision）
            stock_data: 股票数据（StockData）
            price: 成交价格（用于更新entry_price），如果None则使用stock_data.price

        Returns (M5): True 保存成功；False 保存失败（冲突被拒绝/IO 失败）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        new_state = strategy_decision.new_state
        price = price or stock_data.price

        # 获取或创建持仓记录
        existing = self.get_position(stock_code)

        # 防御：stock_name 为空或等于代码（Baostock 行情不含名称时回填代码）时，
        # 优先保留 existing 已维护的名称，避免把正确名称覆盖成代码（旧版 bug）
        if not stock_name or stock_name == stock_code:
            stock_name = (existing.stock_name if existing and existing.stock_name else stock_name)

        # 仓位动作映射
        pos_action = strategy_decision.position_action.value

        # 如果是OPEN动作且之前没有记录，设置开仓信息
        entry_date = existing.entry_date if existing else today
        entry_price = existing.entry_price if existing else price

        # 如果清仓了，清除开仓信息
        if strategy_decision.position_action.value == "CLOSE_ALL":
            entry_date = None
            entry_price = None
            high_since_entry_new = None
        else:
            # 持仓期最高价(Chandelier追踪止损用)：取 已存值/本次entry_exit算的/当前价 三者大值
            # 审查修复H1关联：原PositionRecord构造未传high_since_entry -> to_dict覆盖重置None
            # (任何调用方手动改过 pos.high_since_entry 都会被本函数整体覆盖丢失，故取三者大值)
            _ee = getattr(strategy_decision, 'entry_exit', None) or {}
            _ee_highest = _ee.get('highest_since_entry') if isinstance(_ee, dict) else None
            _prev_high = existing.high_since_entry if existing else None
            _cands = [v for v in [_prev_high, _ee_highest, price] if v is not None and v > 0]
            high_since_entry_new = max(_cands) if _cands else None

        # 仓位比例：审查修复 M-E--HOLD_POSITION（含 PlanGuard 规则1压制 REDUCE->HOLD）时
        # 必须保持原仓位（existing.current_ratio），否则回退到 new_state.current_position_ratio
        # （strategy_layer 算的减仓目标 0.65×原值）会导致记录 desync：last_action=HOLD_POSITION
        # 无实际卖出，但 current_ratio 被记成减仓后比例，持仓记录与券商 desync。
        if pos_action == "HOLD_POSITION":
            actual_ratio = existing.current_ratio if existing else 0.0
        else:
            # 非 HOLD：优先用策略层 position_ratio，回退到 new_state（OPEN/ADD/REDUCE/CLOSE_ALL 路径）
            actual_ratio = strategy_decision.position_ratio if strategy_decision.position_ratio > 0 else new_state.current_position_ratio

        record = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_date=entry_date,
            entry_price=entry_price,
            current_ratio=actual_ratio,
            high_since_entry=high_since_entry_new,
            last_action=pos_action,
            last_action_semantic=strategy_decision.action_semantic,
            last_sell_path=strategy_decision.sell_path,
            last_action_date=today,
            lifecycle=new_state.lifecycle.value,
            # ISS-078（P0）：重建记录必须把既有 trade_plan 带上——此前漏传导致
            # chat analyze_stock 每回写一次就把该股交易计划从 portfolio.yaml 抹掉
            # （PlanGuard 压制/追踪止损/超配"只出手一次"标志全部随计划一起丢失），
            # HEAD 版持仓文件 7 处 trade_plan 实测被抹到 0 处。教训：重建记录
            # 必须从 existing 全量携带，不能只列本次会变的字段。
            trade_plan=existing.trade_plan if existing else None,
            trade_plan_raw=existing.trade_plan_raw if existing else None,
            strategy_state={
                "cooldown_remaining": new_state.cooldown_remaining,
                "cooldown_reason": new_state.cooldown_reason,
                "reverse_count": new_state.reverse_count,
                "inertia_counter": new_state.inertia_counter,
                "last_decision": new_state.last_decision.value if new_state.last_decision else None,
                "recent_signals": new_state.recent_signals,
                "signal_stability_score": new_state.signal_stability_score,
                "reduce_protection_remaining": new_state.reduce_protection_remaining,
                "min_hold_remaining": new_state.min_hold_remaining,
                "add_protection_remaining": new_state.add_protection_remaining,
                # getattr 防御：调用方可能传测试桩/简化对象（无该属性时落 None）
                "last_tick_date": getattr(new_state, "last_tick_date", None),
            }
        )

        if "positions" not in self._data:
            self._data["positions"] = {}

        # 如果仓位为0且lifecycle是FLAT或COOLDOWN，保留记录（冷却期需要）
        # 如果仓位为0且lifecycle是FLAT且冷却期结束，删除记录
        if (actual_ratio <= 0
                and new_state.lifecycle == TradeLifecycle.FLAT
                and new_state.cooldown_remaining <= 0):
            # 交易完全结束，删除记录
            del self._data["positions"][stock_code]
        else:
            self._data["positions"][stock_code] = record.to_dict()

        return self._save().ok

