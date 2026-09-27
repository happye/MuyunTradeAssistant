# 第三轮复验证据

2026-09-27｜HEAD `427e110`，当前工作树｜纯内存探针，不联网、不调用AI、不改真实账本。main.py及四份iteration2文档已有外部未提交改动，未触碰；核心探针文件相对HEAD无本轮代码修改。

## 1. 已执行检查

```text
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
  tests/core/test_horizon_policy.py
  tests/core/test_claim_verification_corpus.py
  tests/core/test_financial_data.py
  tests/core/test_research_snapshot.py
  tests/core/test_account_snapshot.py
  tests/core/test_research_service.py

实际结果：109 passed in 6.55s
```

上面为排版拆行，运行时是单行命令。仓库测试使用既有隔离机制。此计数不是全量1222的重跑确认，不证明新反例已被覆盖。

## 2. 第一组实测

| 输出键 | 实际值 |
|---|---|
| P1 | UNESTABLISHED |
| P2_cv2 | REJECTED |
| P3 | citation_present/no_future_date/unit_allowed/statement_present；没有citation_resolves |
| P4 | kept=0；latest_only_unverifiable包含netProfit |
| P5 | continuous=0.1；state=CONDITIONAL；quantity=0 |
| N1_unrelated_quote | FACT_CHECKED |
| N2_counter_and_shadow | research=UNESTABLISHED；real_exposure=TRUE、change_to_profit=TRUE、window_and_refutation=UNKNOWN；shadow=VALID |
| N3_fake_ref | VALID |
| N4_mapping | once=0.7294；twice=72.94；restored_supplier=72.94 |
| raw_sample_audit | files=768；600519=256、000002=512；liability_records=24；period_count=16 |

P2按新cv2入口、带真实摘录复验；旧verify_claim仍属结构检查，不要求它冒充新语义核验器。N1不是数值错误，而是原文和主张完全无关仍被升级。N2执行真实ResearchService和shadow辅助函数；不表示已对真实账户产生该错误动作。N4是重复映射/供应商恢复的合成输入，不是已证明线上重复调用。

复现：在仓库根将下面内容用PowerShell单引号here-string管道输入 `.\.venv\Scripts\python.exe -B -`。原始留样目录不存在时统计将为0，不能假称重现768条。

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
from types import SimpleNamespace
from src.core.research import ThesisRecord, assess_thesis
from src.core.claim_extraction import ClaimRecord, SourceDocument, verify_claim, verify_claim_tiered
from src.data.research_snapshot import financial_record, EvidenceSnapshot
from src.core.portfolio_policy import AddProposal, BudgetConstraints, solve_budget
from src.data.account_snapshot import AccountSnapshot, TradeRulesAdapter, LotRules, allocate_tradeable_budget
from src.core.research_service import ResearchService
from src.core.shadow_diff import _thesis_status_for
from src.data.financial_data import apply_unit_drift_mapping, FINANCIAL_SOURCE_VERSION
asof=datetime(2026,9,27,tzinfo=timezone.utc)
pub=datetime(2026,9,1,tzinfo=timezone.utc)
out={}
out["P1"]=assess_thesis(ThesisRecord(thesis_id="p",horizon="LONG",beneficiary_business="",profit_mechanism="",facts_observed=["   "])).value
def make(body, statement, **kw):
    doc=SourceDocument(canonical_uri="probe://doc",content_hash=SourceDocument.body_hash(body),security_ids=["600000"],published_at=pub,body=body)
    c=ClaimRecord(security_id="600000",event_type="order",statement=statement,citation_uri=doc.canonical_uri,citation_hash=doc.content_hash,quote_text=body,published_at=pub,**kw)
    return c,doc
c,d=make("Company reports no new order.","Company won a new order.")
out["P2_cv2"]=verify_claim_tiered(c,[d],as_of=asof).level.value
out["P3"]=verify_claim(c,as_of=asof).checks
r=financial_record("600000",dict(metric="netProfit",value=999,unit="CNY",period_kind="cumulative",period_end="2023-12-31",published_at="2024-04-01",source_uri="probe://latest",source_version="latest-only"))
sn=EvidenceSnapshot.build("600000",datetime(2024,5,1,tzinfo=timezone.utc),[r],strict=True)
out["P4"]={"kept":len(sn.records),"reasons":sn.drop_reasons}
class Rules(TradeRulesAdapter):
    def lot_rules(self,security_id,as_of):
        return LotRules(security_id=security_id,board="main",min_order_qty=100,lot_step=100,effective_from="2020-01-01",source="synthetic fixture")
b=solve_budget([AddProposal(stock_code="600000",target_weight=.1,pressure_loss_rate=.2)],[],BudgetConstraints(per_stock_max=.2,cash_nav=None))
a=allocate_tradeable_budget(b.adds,AccountSnapshot(as_of=asof,cash_available=None,nav=100000),Rules(),as_of="2026-09-27",price_provider=lambda code:(10,asof))
out["P5"]={"continuous":b.adds[0].add_weight,"state":a[0].allocation_state.value,"quantity":a[0].quantity}
c2,d2=make("Company held its annual meeting.","Company has an unassailable competitive advantage.")
out["N1_unrelated_quote"]=verify_claim_tiered(c2,[d2],as_of=asof).level.value
c3,d3=make("Company reports no new order.","Company reports no new order.",negation_flag=True,relation="REFUTES")
bundle=ResearchService().run("600000",as_of=asof,evidence_records=[],documents=[{"claim":c3,"documents":[d3]}])
mid=bundle.assessments["MID"]
plan=SimpleNamespace(**bundle.plan_drafts[0])
out["N2_counter_and_shadow"]={"research_status":mid["status"],"assertions":{x["proposition_type"]:x["evaluation"] for x in mid["required_assertions"]},"shadow_status":_thesis_status_for(plan,"MID","600000").value}
out["N3_fake_ref"]=assess_thesis(ThesisRecord(thesis_id="p",horizon="MID",beneficiary_business="",profit_mechanism="",facts_observed=["unsupported"],fact_evidence_refs={"unsupported":["does-not-exist"]})).value
x=dict(metric="liabilityToAsset",source_version=FINANCIAL_SOURCE_VERSION,period_end="2024-06-30",value=.007294,unit="ratio")
once=apply_unit_drift_mapping(x); twice=apply_unit_drift_mapping(once)
restored=apply_unit_drift_mapping({**x,"value":.7294,"period_end":"2026-06-30"})
out["N4_mapping"]={"once":once["value"],"twice":twice["value"],"restored_supplier":restored["value"]}
raw=Path("tests/artifacts/probe_fin_semantics_raw/raw")
counts=Counter(); metrics=Counter(); periods=Counter()
for f in raw.glob("*.json"):
    obj=json.loads(f.read_text(encoding="utf-8-sig"))
    meta=obj.get("metadata",{}); content=obj.get("content",{})
    counts[str(meta.get("stock_code"))]+=1
    metrics[str(content.get("metric"))]+=1
    periods[str(content.get("period_end"))]+=1
out["raw_sample_audit"]={"files":sum(counts.values()),"securities":dict(counts),"liability_records":metrics.get("liabilityToAsset"),"period_count":len(periods)}
print(json.dumps(out,ensure_ascii=True,indent=2))
```


## 3. 账户事件探针

真实replay函数，只有_load替换为内存事件源；不创建/打开/写入账户文件。期初现金5000，无持仓：ghost SELL后现金6000、持仓空；缺成本BUY后现金4000、持仓空。程序同时记录了隔离告警，说明份额隔离没有覆盖现金。

```python
import json
from pathlib import Path
from src.data.account_snapshot import AccountEventLog, AccountEvent
class MemoryLog(AccountEventLog):
    def __init__(self, events):
        self.events=events
    def _load(self):
        return list(self.events)
ghost=AccountEvent(event_id="ghost",event_type="SELL",security_id="600000",trade_date="2026-09-27",quantity=100,price=10,cash_delta=1000)
bad=AccountEvent(event_id="bad",event_type="BUY",security_id="600000",trade_date="2026-09-27",quantity=100,price=None,cash_delta=-1000)
out={}
for label,event in [("ghost_sell",ghost),("invalid_buy",bad)]:
    sn=MemoryLog([event]).replay(opening_cash=5000)
    out[label]={"cash":sn.cash_available,"holdings":[x.model_dump(mode="json") for x in sn.holdings]}
print(json.dumps(out,ensure_ascii=True,indent=2))
```


## 4. 已读与未读的边界

- 原交付账本、R9独立审查、ISS114摘录、冻结语料元数据、关键源码与目标测试已核对。
- 核心定位：claim_extraction.verify_claim_tiered；research.assess_thesis/evaluate_assertion；research_service.run/_claim_supports；shadow_diff._thesis_status_for；financial_data.apply_unit_drift_mapping；account_snapshot.AccountEventLog.replay。
- 当前CLI归档读/公告正文/草稿保存缺口来自research_command静态检查；没有将未实际点击的用户流程写成实测。
- 原件PDF全文读取超时，本轮未核定发行人财务真值；未跑E1/E5付费AI、全量收益回测或新真实用户走查。
- 本轮缺陷仅确认和设计承接，尚未修改源码修复。代码/测试hash与复查范围见REVIEW_INDEX.json。

