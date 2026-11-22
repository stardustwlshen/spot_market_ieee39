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
