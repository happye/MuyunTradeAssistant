"""候选池与来源谱系（plan/fusion F4，DESIGN §2.1/RESEARCH §2.1）：三路并集召回+去重+配额。

病根（RESEARCH §2.1）：候选截断影响后续 bz 排名上限——先丢掉的标的，后续再强的 AI
也找不回来；量比缺失被跳过后语义模糊（"缩量"没有证据支撑）；技术/产业/质量三路
没有统一的谱系记录。

语义（TASKS F4 + F4 审查裁决）：
- 技术/产业/质量候选取**并集去重**：同股多路只产生一个候选，contributions 记录全部
  来源（含同路多规则命中——"只计算一次"不是"只记录一次"）
- 保留 source、rule_version、applied/missing filters、rank_completeness、**来源截断
  原因**与配额截断记录——谱系完整可追溯
- **配额按 rank_in_source 优先截断**（来源自己的排序权威，缺失排最后）——不按代码序
  （代码字典序=板块序，按代码截断会系统性牺牲科创板/主板，F4 审查 P1 裁决）
- 快照缺量比的候选标 missing_filter（待验证），不宣称缩量
- **长期候选不受当天回调必要条件误杀**：LONG_QUALITY 路独立准入，不施加技术路条件
- 输入排列变化不改变结果（(rank, rule, code, filters排序) 确定序；G09/排列不变性）
- 不做市值/龙头一刀切过滤：候选池层只有配额，没有规模歧视
- 空/非法代码**不静默丢弃**：计入 skipped_invalid（形态不匹配可发现，F4 审查 P0 教训）

本模块纯函数+纯数据（零网络）；与 scan/bz 的接线是窄适配构造函数——bz 法C 真实
输出形态是 {"code","name","term","why"}（theme_locator.py），from_theme_results
双键兼容 stock_code/code；旧 scan/bz 命令零改动（回滚=关新路线）。
"""

import hashlib
import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

CANDIDATE_POOL_VERSION = "f4.v1"


class CandidateRoute(str, Enum):
    """召回路线（DESIGN §5 架构图：候选召回 = 技术 / 产业 / 长期质量）。"""

    TECHNICAL = "TECHNICAL"        # scan 规则（回调/趋势/超跌等技术形态）
    INDUSTRY = "INDUSTRY"          # bz 主题/产业链（法C 主题定位等）
    LONG_QUALITY = "LONG_QUALITY"  # 长期质量研究队列（独立准入，不施加技术条件）


class RouteContribution(BaseModel):
    """一路来源对候选的贡献记录（谱系最小单元）。"""

    model_config = ConfigDict(extra="allow")

    route: CandidateRoute
    rule_name: str = Field(default="", description="来源规则/主题名（如 healthy_pullback / 氮化镓）")
    rule_version: str = Field(default="", description="规则版本（scan_rules 版本/bz prompt 版本）")
    rank_in_source: Optional[int] = Field(default=None, description="来源内排名（None=来源未排名）")
    rank_complete: bool = Field(default=True, description="该来源排名是否完整（截断=False）")
    source_truncation_reason: str = Field(default="", description="来源侧截断原因（如 scan max_candidates=30）")
    applied_filters: list[str] = Field(default_factory=list, description="该股实际通过的过滤条件（构造时排序，保证 fingerprint 排列无关）")
    missing_filters: list[str] = Field(default_factory=list,
                                       description="缺数据跳过的条件（如 volume_ratio_missing）——标待验证，不宣称语义")


class Candidate(BaseModel):
    """一个候选（并集去重后：同股一个 Candidate，多路/多规则来源进 contributions）。"""

    model_config = ConfigDict(extra="allow")

    stock_code: str
    stock_name: str = ""
    contributions: list[RouteContribution] = Field(default_factory=list)

    @property
    def routes(self) -> list["CandidateRoute"]:
        seen = []
        for c in self.contributions:
            if c.route not in seen:
                seen.append(c.route)
        return seen

    @property
    def missing_filters(self) -> list[str]:
        out = []
        for c in self.contributions:
            for m in c.missing_filters:
                if m not in out:
                    out.append(m)
        return out


class CandidateSet(BaseModel):
    """候选集合（并集去重+配额截断后的结果，谱系完整）。"""

    model_config = ConfigDict(extra="allow")

    pool_version: str = CANDIDATE_POOL_VERSION
    as_of: str = Field(default="", description="截止时点（ISO）")
    candidates: list[Candidate] = Field(default_factory=list)
    excluded_by_quota: list[dict] = Field(
        default_factory=list, description="配额截断记录 {stock_code, route, rule_name, reason}——被截断的可追溯")
    dedup_count: int = Field(default=0, description="跨路去重合并次数（同股多路算一次）")
    skipped_invalid: int = Field(default=0, description="空/非法代码被拒的输入条数（不静默丢弃，F4 审查 P0）")

    def by_route(self, route: CandidateRoute) -> list[Candidate]:
        return [c for c in self.candidates if route in c.routes]

    def get(self, stock_code: str) -> Optional[Candidate]:
        for c in self.candidates:
            if c.stock_code == stock_code:
                return c
        return None

    def fingerprint(self) -> str:
        """集合内容指纹（排列无关）：候选按 code 排序后 hash——G09 排列不变性的可验证键。"""
        canon = sorted(
            (c.model_dump(mode="json") for c in self.candidates),
            key=lambda d: d["stock_code"])
        payload = json.dumps({"excluded": sorted(
            json.dumps(e, ensure_ascii=False, sort_keys=True) for e in self.excluded_by_quota),
            "candidates": canon}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _norm_code(code) -> str:
    c = str(code or "").strip()
    return c.zfill(6) if c.isdigit() and len(c) <= 6 else ("" if not c else c)


def build_candidate_set(
    route_inputs: dict,
    quotas: Optional[dict] = None,
    *,
    as_of: str = "",
) -> CandidateSet:
    """三路并集去重 + 配额截断（输入排列无关）。

    Args:
        route_inputs: {route: [contrib_dict]}；contrib_dict 至少含 stock_code（或 code），
            可选 stock_name/name/rule_name/rule_version/rank_in_source/rank_complete/
            applied_filters/missing_filters/source_truncation_reason
        quotas: 每路保留上限；**配额按 rank_in_source 优先**（来源排序权威；缺失排最后），
            同 rank 内按 (rule_name, code) 确定序——排列变化不改变结果
        as_of: 截止时点 ISO 串（诊断用）

    去重语义（F4 审查 P2 裁决）：跨路同股=并入既有候选（dedup_count+1）；
    同路多规则命中=追加贡献（不占配额、不计 dedup——"只计算一次"不是"只记录一次"）。
    """
    excluded: list[dict] = []
    dedup_count = 0
    skipped_invalid = 0
    kept_contribs: dict[str, list[RouteContribution]] = {}
    names: dict[str, str] = {}

    for route in CandidateRoute:  # 固定枚举序，不依赖 dict 传入序
        items = route_inputs.get(route) or []

        # 路内确定序：rank 优先（来源排序权威；None=未排名排最后），同 rank 按
        # (rule_name, code, 排序后的 filters) 内容定序——输入排列只影响同键同内容项
        def _sort_key(d):
            rank = d.get("rank_in_source")
            return (rank is None, int(rank) if rank is not None else 0,
                    str(d.get("rule_name") or ""),
                    _norm_code(d.get("stock_code") or d.get("code")),
                    json.dumps(sorted(str(x) for x in (d.get("applied_filters") or [])),
                               ensure_ascii=False),
                    json.dumps(sorted(str(x) for x in (d.get("missing_filters") or [])),
                               ensure_ascii=False))
        items = sorted(items, key=_sort_key)

        quota = (quotas or {}).get(route)
        quota_taken = 0
        seen_codes_in_route = set()
        for d in items:
            code = _norm_code(d.get("stock_code") or d.get("code"))
            if not code:
                skipped_invalid += 1  # 形态不匹配/空代码计数留痕，不静默丢弃
                continue
            contrib = RouteContribution(
                route=route,
                rule_name=str(d.get("rule_name") or ""),
                rule_version=str(d.get("rule_version") or ""),
                rank_in_source=d.get("rank_in_source"),
                rank_complete=bool(d.get("rank_complete", True)),
                source_truncation_reason=str(d.get("source_truncation_reason") or ""),
                applied_filters=sorted(str(x) for x in (d.get("applied_filters") or [])),
                missing_filters=sorted(str(x) for x in (d.get("missing_filters") or [])),
            )
            if code in seen_codes_in_route:
                # 同股同路多规则命中：追加谱系（不占配额、不计 dedup）
                if code in kept_contribs:
                    kept_contribs[code].append(contrib)
                else:
                    excluded.append({"stock_code": code, "route": route.value,
                                     "rule_name": contrib.rule_name,
                                     "reason": "同路重复命中且已配额截断"})
                continue
            seen_codes_in_route.add(code)
            if quota is not None and quota_taken >= quota:
                excluded.append({"stock_code": code, "route": route.value,
                                 "rule_name": contrib.rule_name,
                                 "reason": f"{route.value} 路配额已满（{quota}）"})
                continue
            quota_taken += 1
            if code in kept_contribs:
                dedup_count += 1  # 跨路重复：并入既有候选（只算一次）
                kept_contribs[code].append(contrib)
            else:
                kept_contribs[code] = [contrib]
            # 名称回填：任何一路给出非空名即可（首路空名不阻塞，F4 审查 P2）
            name = str(d.get("stock_name") or d.get("name") or "")
            if name and not names.get(code):
                names[code] = name

    candidates = [Candidate(stock_code=c, stock_name=names.get(c, ""),
                            contributions=contribs)
                  for c, contribs in sorted(kept_contribs.items())]
    return CandidateSet(as_of=as_of, candidates=candidates,
                        excluded_by_quota=excluded, dedup_count=dedup_count,
                        skipped_invalid=skipped_invalid)


# ──────────────── 窄适配：现有 scan/bz 输出 → route input ────────────────

def from_scan_candidates(items: list, rule_name: str, rule_version: str,
                         rank_complete: bool = True, truncation_reason: str = "") -> list[dict]:
    """ScanCandidate/扫描 dict → TECHNICAL 路输入。

    量比等字段缺失（None）→ missing_filter 记 `volume_ratio_missing`——候选标待验证，
    不宣称缩量（RESEARCH §2.1：新浪源无量比，scanner_filter 跳过整列缺失条件）。
    """
    out = []
    for i, it in enumerate(items):
        get = it.get if isinstance(it, dict) else (lambda k, _g=it: getattr(_g, k, None))
        missing = []
        if get("volume_ratio") is None:
            missing.append("volume_ratio_missing")
        if get("amount") is None:
            missing.append("amount_missing")
        out.append({
            "stock_code": get("stock_code"),
            "stock_name": get("stock_name") or "",
            "rule_name": rule_name,
            "rule_version": rule_version,
            "rank_in_source": i + 1,
            "rank_complete": rank_complete,
            "applied_filters": [rule_name],
            "missing_filters": missing,
            "source_truncation_reason": truncation_reason,
        })
    return out


def from_theme_results(codes: list, rule_name: str, rule_version: str) -> list[dict]:
    """bz 主题/产业链结果 → INDUSTRY 路输入。

    兼容两种真实形态（F4 审查 P0）：bz 法C 的 {"code","name","term","why"} dict
    （theme_locator.py）与纯代码字符串。
    """
    out = []
    for i, code in enumerate(codes):
        if isinstance(code, dict):
            stock_code = code.get("stock_code") or code.get("code")
            stock_name = code.get("stock_name") or code.get("name") or ""
        else:
            stock_code = str(code)
            stock_name = ""
        out.append({
            "stock_code": stock_code,
            "stock_name": stock_name,
            "rule_name": rule_name,
            "rule_version": rule_version,
            "rank_in_source": i + 1,
            "rank_complete": True,
            "applied_filters": [f"theme:{rule_name}"],
            "missing_filters": [],
        })
    return out
