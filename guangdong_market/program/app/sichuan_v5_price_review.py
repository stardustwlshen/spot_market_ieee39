"""Sichuan draft §9.6 RT price verification; opt-in research API only."""
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal

Kind = Literal["rt_initial", "rt_recomputed", "da_preclear"]
Source = Literal["rt_initial", "rt_recomputed", "da_proxy_rt", "withheld"]
_CAUSES = frozenset(("missing_boundary", "incorrect_boundary", "program_fault", "other_price_anomaly"))
_CENT = Decimal(".01")


@dataclass(frozen=True)
class Vector:
    stage: Kind
    quarter: int
    nodal_yuan_mwh: Mapping[int, Decimal]
    evidence_id: str


@dataclass(frozen=True)
class Incident:
    reason: str
    record_id: str
    # None means rerun has not been shown impossible. Never infer this from
    # an incomplete result, a price value, timing, or settlement failure.
    documented_rerun_blocker: str | None = None


@dataclass(frozen=True)
class PriceDecision:
    source: Source
    quarter: int
    nodal_yuan_mwh: Mapping[int, Decimal] | None
    evidence_id: str | None
    incident_id: str | None = None


def _valid_text(value):
    return isinstance(value, str) and bool(value.strip())


def _read(vector, stage, quarter, nodes):
    if not isinstance(vector, Vector) or vector.stage != stage or vector.quarter != quarter:
        raise ValueError("电价来源阶段或时段不匹配")
    if type(vector.quarter) is not int or not _valid_text(vector.evidence_id):
        raise ValueError("电价缺可核验记录")
    if set(vector.nodal_yuan_mwh) != nodes or any(type(k) is not int for k in vector.nodal_yuan_mwh):
        raise ValueError("节点集合不一致，不能混用部分电价")
    out = {}
    for node, quote in vector.nodal_yuan_mwh.items():
        if not isinstance(quote, Decimal) or not quote.is_finite():
            raise ValueError("存在不可核验的节点价格")
        try:
            out[node] = max(Decimal(0), min(Decimal(800), quote)).quantize(_CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("节点价格取值无效") from exc
    return out


def choose_rt_price(quarter, nodes, *, initial=None, incident=None, rerun=None, same_quarter_da=None):
    """Require explicit documented anomaly and unavailable rerun to use DA.

    Returned proxy is an RT price source only. It cannot create a DA billing
    leg, provide realized quantities, or attest that external evidence is
    genuine. The caller owns evidence validation, market authorization, and
    any subsequent corrections. This function never activates itself in a
    normal run.
    """
    nodes = tuple(nodes)
    if (type(quarter) is not int or not 0 <= quarter < 96 or not nodes
            or any(type(n) is not int for n in nodes) or len(set(nodes)) != len(nodes)):
        raise ValueError("时段或节点集合无效")
    node_set = set(nodes)
    if incident is None:
        if rerun is not None or same_quarter_da is not None:
            raise ValueError("没有价格异常记录，不能替换实时价格")
        if initial is None:
            return PriceDecision("withheld", quarter, None, None)
        return PriceDecision("rt_initial", quarter,
                             _read(initial, "rt_initial", quarter, node_set), initial.evidence_id)
    if (not isinstance(incident, Incident) or incident.reason not in _CAUSES
            or not _valid_text(incident.record_id)):
        raise ValueError("实时价格异常记录无效")
    if rerun is not None:
        prices = _read(rerun, "rt_recomputed", quarter, node_set)
        return PriceDecision("rt_recomputed", quarter, prices, rerun.evidence_id, incident.record_id)
    if not _valid_text(incident.documented_rerun_blocker) or same_quarter_da is None:
        return PriceDecision("withheld", quarter, None, None, incident.record_id)
    prices = _read(same_quarter_da, "da_preclear", quarter, node_set)
    return PriceDecision("da_proxy_rt", quarter, prices, same_quarter_da.evidence_id, incident.record_id)


@dataclass(frozen=True)
class BoundRerun:
    """Declared research price evidence, bound to one original result."""
    run_id: str
    rt_sha256: str
    vector: Vector


@dataclass(frozen=True)
class ReviewRequest:
    run_id: str
    rt_sha256: str
    quarter: int
    incident: Incident
    rerun: BoundRerun | None = None


def _clean_physical(row, nodes):
    if row.get("dispatch_verified") is False:
        return False
    try:
        shed, spill = row["shed"], row["spill"]
        if not all(isinstance(x, Mapping) and set(x)=={str(n) for n in nodes} for x in (shed,spill)):
            return False
        checks=(*shed.values(), *spill.values(), row["reliability_reserve_short_mw"])
        return all(type(v) in (int,float) and -1e-6<=v<=1e-6 for v in checks)
    except (KeyError, TypeError):
        return False


def resolve_run_review(run_id, rt_sha256, inputs, rt_rows, da_rows, request):
    """Select an RT research price; never repair or produce physical dispatch."""
    from dataclasses import asdict
    from math import isfinite
    from types import SimpleNamespace
    from sichuan_v5_demo import research_values

    if not isinstance(request, ReviewRequest) or (request.run_id,request.rt_sha256)!=(run_id,rt_sha256):
        raise ValueError("价格复核证据与原运行摘要不匹配")
    incident=request.incident
    if (not isinstance(incident,Incident) or incident.reason not in _CAUSES
            or not _valid_text(incident.record_id)
            or incident.documented_rerun_blocker is not None and not _valid_text(incident.documented_rerun_blocker)):
        raise ValueError("必须提交显式价格异常记录")
    q=request.quarter
    if type(q) is not int or not 0<=q<len(rt_rows):
        raise ValueError("只能复核已执行的实际时段")
    nodes=tuple(inputs["case"]["buses"])
    rt=rt_rows[q]
    if rt.get("quarter")!=q or not _clean_physical(rt,nodes):
        raise ValueError("原实时出清物理结果不可核验；禁止复核价格或生成金额")

    def vector(row, stage, evidence):
        raw=row.get("raw_nodal_prices")
        if (row.get("quarter")!=q or not isinstance(raw,Mapping)
                or set(raw)!={str(n) for n in nodes}
                or not all(type(x) in (float,int) and isfinite(x) for x in raw.values())):
            return None
        return Vector(stage,q,{n:Decimal(str(raw[str(n)])) for n in nodes},evidence)

    alt=None
    if request.rerun is not None:
        rr=request.rerun
        if not isinstance(rr,BoundRerun) or (rr.run_id,rr.rt_sha256)!=(run_id,rt_sha256):
            raise ValueError("复算证据与原运行摘要不匹配")
        alt=rr.vector
        if alt is None:
            raise ValueError("缺少复算节点价格")
    da=da_rows[q]
    proxy=(vector(da,"da_preclear",f"{run_id}:DA_PRECLEAR.json:{q}")
           if alt is None and incident.documented_rerun_blocker and _clean_physical(da,nodes) else None)
    decision=choose_rt_price(q,nodes,incident=incident,rerun=alt,same_quarter_da=proxy)
    selected=decision.nodal_yuan_mwh
    adjusted=dict(rt,raw_nodal_prices=selected if selected is not None else dict.fromkeys(nodes,0))
    for key in ("shed","spill","demand"):
        adjusted[key]={int(k):v for k,v in adjusted[key].items()}
    case=SimpleNamespace(buses=nodes,resources=[SimpleNamespace(**r) for r in inputs["case"]["resources"]])
    values=research_values(case,adjusted)
    if selected is None:
        from sichuan_v5_demo import _result
        from sichuan_v5_settlement import SettlementInput
        values={"quarter":q,"valuation_status":"withheld_price_review",
                "cleared_nodal_price":dict.fromkeys(nodes),"uniform_price":None,
                "rows":[_result(SettlementInput(q,r["resource_id"],r["role"],
                                               Decimal(r["grid_delivered_mwh"]),
                                               Decimal(r["grid_consumed_mwh"])))
                        for r in values["rows"]]}
    return {
        "schema":"ieee39.sichuan_v5.price_review.v1", "formal_settlement":False,
        "evidence_status":"declared_synthetic_research", "request":asdict(request),
        "source":decision.source, "quarter":q, "incident_id":incident.record_id,
        "evidence_id":decision.evidence_id, "original_raw_nodal_prices":rt.get("raw_nodal_prices"),
        "selected_nodal_prices":decision.nodal_yuan_mwh,"values":values,
    }


def parse_review_request(record):
    """Parse a complete, explicitly run-bound JSON request; never infer IDs."""
    try:
        if (not isinstance(record, dict)
                or not {"run_id","rt_sha256","quarter","incident"}<=set(record)
                or set(record)-{"run_id","rt_sha256","quarter","incident","rerun"}):
            raise ValueError("不完整的复核请求")
        incident=Incident(**record['incident'])
        alt=record.get('rerun')
        rr=None
        if alt is not None:
            if set(alt)!={"run_id","rt_sha256","vector"}:
                raise ValueError("不完整的复算证据")
            v=alt['vector']
            if (set(v)!={"stage","quarter","nodal_yuan_mwh","evidence_id"}
                    or any(str(int(k))!=k for k in v['nodal_yuan_mwh'])
                    or any(type(a) not in (str,int) for a in v['nodal_yuan_mwh'].values())):
                raise ValueError('节点编号或价格格式异常')
            rr=BoundRerun(alt['run_id'],alt['rt_sha256'],
                          Vector(v['stage'],v['quarter'],
                                 {int(k):Decimal(a) for k,a in v['nodal_yuan_mwh'].items()},
                                 v['evidence_id']))
        return ReviewRequest(record['run_id'],record['rt_sha256'],record['quarter'],incident,rr)
    except (KeyError,TypeError,ValueError,ArithmeticError,AttributeError) as exc:
        raise ValueError('价格复核档案证据格式有误') from exc
