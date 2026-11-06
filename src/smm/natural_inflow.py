"""One strict natural-inflow lookup for every physical/diagnostic consumer.

Legacy remains explicitly constant. New inputs never fall back to season.qin.
The values are local natural m3/s, excluding controlled upstream arrivals.
"""
from __future__ import annotations
import numpy as np
from .causal_types import MODEL, SLOT_SECONDS, utc_seconds, utc_iso, SelectedInputs, ResourceSeries


def is_causal(cfg):
    return (cfg.base.get('forecast') or {}).get('model')==MODEL


def natural_values(cfg,station,periods,*,selected_inputs=None,day_start_utc=None):
    periods=list(periods)
    if not periods: return np.empty(0,dtype=float)
    if periods!=list(range(periods[0],periods[-1]+1)) or periods[0]<0 or periods[-1]>=cfg.T:
        raise ValueError('natural inflow request must use contiguous absolute day periods')
    if selected_inputs is None:
        if is_causal(cfg): raise ValueError('causal natural inflow source is missing; no seasonal fallback')
        spec=next((h for h in cfg.units['hydro'] if h['id']==station),None)
        if spec is None: raise ValueError('unknown natural inflow station')
        q=float(spec['seasons'][cfg.base['season']]['qin'])
        if not np.isfinite(q) or q<0: raise ValueError('invalid legacy natural inflow')
        return np.full(len(periods),q)
    if not is_causal(cfg): raise ValueError('v3 natural inputs require explicit causal model selection')
    if cfg.dt!=.25 or cfg.T!=96 or day_start_utc is None:
        raise ValueError('v3 natural flow requires explicit day clock and 15min grid')
    expected={h['id'] for h in cfg.units['hydro']}
    given={s.resource_id for s in selected_inputs.series if s.kind=='natural_inflow'}
    if expected!=given: raise ValueError('natural inflow stations do not match enabled hydro')
    day=utc_seconds(day_start_utc)
    return selected_inputs.values(station,utc_iso(day+periods[0]*SLOT_SECONDS),
        utc_iso(day+(periods[-1]+1)*SLOT_SECONDS),kind='natural_inflow')


def case_natural_values(case,station,periods):
    return natural_values(case.cfg,station,periods,
        selected_inputs=getattr(case,'selected_inputs',None),day_start_utc=getattr(case,'day_start_utc',None))


def model_natural(md,station,relative_period):
    d=md.hydro[station]
    if 'qin_series' in d:
        if not 0<=relative_period<len(d['qin_series']): raise ValueError('model inflow coverage is incomplete')
        return float(d['qin_series'][relative_period])
    # Historical models/fixtures remain supported, with no fabricated v3 provenance.
    return float(d['qin'])


def selected_from_evidence(evidence):
    if not isinstance(evidence,dict) or evidence.get('model_version')!=MODEL:
        raise ValueError('missing causal input evidence')
    obj=SelectedInputs(evidence['start_utc'],evidence['end_utc'],evidence['issue_time_utc'],
        tuple(ResourceSeries(**r) for r in evidence['rows']),evidence['forecast_id'],
        evidence['information_set_id'],tuple(evidence['sources']))
    if obj.content_hash!=evidence['selected_inputs_hash']:
        raise ValueError('selected input evidence hash mismatch')
    return obj


def evidence_natural_values(cfg,snapshot,station,periods,*,source_registry=None):
    """Read the selected-source record, not the water diagnostic's claimed qin.

    If a live/frozen registry is supplied, additionally require exact source match.
    Exported audits must carry this independent registry to claim source verification.
    """
    ev=snapshot.get('input_provenance')
    if ev is None:
        return natural_values(cfg,station,periods)
    selected=selected_from_evidence(ev)
    if source_registry is None and is_causal(cfg):
        raise ValueError('causal inflow audit requires an independently frozen input registry')
    if source_registry is not None:
        if hasattr(source_registry,'selection'):
            authoritative=source_registry.selection(selected.content_hash)
            if authoritative!=selected: raise ValueError('selected evidence differs from source registry')
        else:
            stored=source_registry.get('selections',{}).get(selected.content_hash)
            if stored is None or selected_from_evidence(stored)!=selected:
                raise ValueError('frozen input registry does not contain the selected input')
            from .causal_types import ForecastFrame,PredictionRequest
            raw=source_registry.get('forecasts',{}).get(selected.forecast_id)
            if raw is None: raise ValueError('frozen issued forecast is missing')
            frame=ForecastFrame(PredictionRequest(**raw['request']),tuple(ResourceSeries(**r) for r in raw['series']),
                raw['information_set_id'],raw['observation_prefix_hash'],raw['public_information_hash'],raw['policy_hash'],
                raw['model_version'],raw['preset_version'])
            if frame.content_hash!=selected.forecast_id or frame.information_set_id!=selected.information_set_id:
                raise ValueError('issued forecast source hash mismatch')
            issued=frame.select(selected.start_utc,selected.end_utc)
            offset=1 if selected.sources[0]=='current_interval_perfect_measurement' else 0
            if {r.resource_id for r in issued.series}!={r.resource_id for r in selected.series}:
                raise ValueError('issued/selected resource identity mismatch')
            for r in selected.series:
                if not np.array_equal(r.values[offset:],issued.values(r.resource_id)[offset:]):
                    raise ValueError('selected future inputs differ from issued forecast')
    return natural_values(cfg,station,periods,selected_inputs=selected,day_start_utc=ev['day_start_utc'])


def case_input_evidence(case):
    selected=getattr(case,'selected_inputs',None)
    if selected is None:
        if is_causal(case.cfg): raise ValueError('missing selected causal inputs')
        return None
    ev=selected.evidence(); ev['day_start_utc']=case.day_start_utc
    return ev


def validate_bid_availability(cfg,bids,selected):
    """Reject automatic future-truth availability and unidentified new bid overrides."""
    if selected is None: return
    for r in cfg.units['renewable']:
        row=bids['units'][r['id']]
        values=row.get('avail_pmax')
        if values is None or len(values)==0: continue
        source=row.get('availability_provenance',{})
        if source.get('kind') not in ('issued_forecast','public_bound','user_declared'):
            raise ValueError(f"{r['id']}: explicit availability_provenance required; future actual is not a bid template source")
        if source['kind']=='issued_forecast':
            if source.get('forecast_id')!=selected.forecast_id or source.get('issue_time_utc')!=selected.issue_time_utc:
                raise ValueError('bid availability references a different issued forecast')
            if not np.array_equal(np.asarray(values,float),selected.values(r['id'])):
                raise ValueError('bid availability differs from its claimed issued forecast')
