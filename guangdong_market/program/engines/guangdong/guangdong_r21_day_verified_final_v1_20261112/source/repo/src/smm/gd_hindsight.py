"""Full-day actual-information provincial research reference, never settlement.

The existing SCUC/SCED chain is reused without changing its physical components.
Inputs are strictly classified actual or explicitly fixed scenario boundaries.
No forecast fallback or inherited market DA storage trajectory is permitted.
See rebuild docs/28_全天实际信息理想调度对比.md.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
from math import isfinite
from numbers import Real

import pyomo.environ as pyo

from .gd_horizon import EnergyHorizon
from .gd_piecewise_bids import ResourceEnergyBid
from .gd_reference_workflow import run_day_ahead_reference, _validate_common, WorkflowSolveError


class HindsightInputError(ValueError):
    """Incomplete actuals, hidden forecast fallback, or misleading comparison."""


FIXED_BOUNDARIES = frozenset({'thermal_availability', 'storage_availability',
    'network_limits', 'service_results', 'reserve_requirements', 'physical_initial_state'})
ACTUAL_KINDS = frozenset({'observed_actual', 'synthetic_actual'})
COST_COMPONENTS = ('thermal_bid_cost', 'renewable_bid_cost', 'startup_cost',
    'shutdown_cost', 'network_penalty_cost', 'balance_penalty_cost',
    'renewable_curtailment_cost', 'battery_bid_cost', 'pumped_bid_cost',
    'anchor_penalty_cost', 'reservoir_band_cost', 'reserve_penalty_cost')


@dataclass(frozen=True)
class HindsightActualInputs:
    # Exact bus/period grid for demand and tie; exact resource/period grid for RES.
    load_mw: dict
    renewable_available_mw: dict
    physical_tie_mw: dict
    # Keys: load, each renewable resource ID, physical_tie. kind/source mandatory.
    provenance: dict
    # Explicit kind=fixed_scenario_boundary/source for every FIXED_BOUNDARIES key.
    fixed_boundaries: dict


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise HindsightInputError(label + ': explicit source required')


def _finite(value, label, *, negative=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(float(value)):
        raise HindsightInputError(label + ': finite numeric value required')
    value = float(value)
    if not negative and value < 0:
        raise HindsightInputError(label + ': nonnegative value required')
    return value


def _keys(mapping, expected, label):
    if not isinstance(mapping, dict) or set(mapping) != set(expected):
        raise HindsightInputError(label + ': exact complete coverage required')


def _source(record, kinds, label):
    _keys(record, {'kind', 'source'}, label)
    if record['kind'] not in kinds:
        raise HindsightInputError(label + ': actual/fixed-boundary classification required')
    _text(record['source'], label)


def _grid(mapping, expected, label, *, negative=False):
    _keys(mapping, expected, label)
    for key, value in mapping.items():
        if not isinstance(key, tuple) or len(key) != 2 or type(key[1]) is not int:
            raise HindsightInputError(label + ': tuple(identifier, integer period) keys required')
        _finite(value, label, negative=negative)


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k,v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(x) for x in value]
    if hasattr(value, '__dataclass_fields__'):
        return _plain(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def prepare_hindsight_reference(*, workflow_inputs, actual):
    """Return a detached strict actual-input workflow and auditable contract.

    Template must be the unanchored full-day DA workflow, not RT execution input.
    Ancillary quantities remain explicit supplied boundaries, not a new auction.
    Initial physical states and final targets are preserved exactly.
    """
    if not isinstance(workflow_inputs, dict) or not isinstance(actual, HindsightActualInputs):
        raise HindsightInputError('workflow_inputs and typed HindsightActualInputs required')
    k = deepcopy(workflow_inputs)
    if k.get('pricing') is not None:
        raise HindsightInputError('hindsight pricing/settlement is outside this reference')
    allowed = {'core_inputs','reserve_by_stage','services','settings','storage_by_stage','piecewise_bids','pricing'}
    if set(k)-allowed:
        raise HindsightInputError('unexpected workflow keys, including callbacks or inherited commitments')
    core = k['core_inputs']; h = core['horizon']
    if not isinstance(h, EnergyHorizon) or h.mode != 'day_ahead_research_96':
        raise HindsightInputError('full-day research 96-interval horizon required')
    h.validate()
    if h.ids != tuple(range(96)) or h.settlement_ids != h.ids or any(h.duration_h[t] != .25 for t in h.ids):
        raise HindsightInputError('exact full-day 96 consecutive quarter-hours required')
    _validate_common(core, k['services'], k['settings'], None, 'DA_SCED')
    bids = k.get('piecewise_bids', {}).get('renewable_bids', ())
    if not isinstance(bids, tuple) or not bids or not all(isinstance(b,ResourceEnergyBid) and b.kind=='renewable' for b in bids):
        raise HindsightInputError('complete typed per-resource renewable bid inventory required')
    inventory = {b.resource_id:b for b in bids}
    if len(inventory) != len(bids) or {'load','physical_tie'} & set(inventory):
        raise HindsightInputError('unique renewable resource IDs distinct from provenance keys required')
    buses, ids = tuple(core['buses']), h.ids
    grid = {(b,t) for b in buses for t in ids}
    _grid(actual.load_mw, grid, 'load actual')
    _grid(actual.physical_tie_mw, grid, 'physical tie', negative=True)
    _keys(actual.renewable_available_mw, inventory, 'renewable actual inventory')
    _keys(actual.provenance, {'load','physical_tie',*inventory}, 'provenance')
    for name in ('load',*inventory):
        _source(actual.provenance[name], ACTUAL_KINDS, name)
    _source(actual.provenance['physical_tie'], ACTUAL_KINDS | {'fixed_external_schedule'}, 'physical_tie')
    if actual.provenance['physical_tie']['kind']=='fixed_external_schedule' and actual.physical_tie_mw != core['physical_tie_mw']:
        raise HindsightInputError('fixed external tie schedule must equal the preserved template boundary')
    _keys(actual.fixed_boundaries, FIXED_BOUNDARIES, 'fixed boundaries')
    for name, record in actual.fixed_boundaries.items():
        _source(record, {'fixed_scenario_boundary'}, name)
    renewed = []
    availability = {key:0. for key in grid}
    for uid, bid in inventory.items():
        _keys(actual.renewable_available_mw[uid], ids, uid+' actual')
        if any(type(t) is not int for t in actual.renewable_available_mw[uid]):
            raise HindsightInputError(uid+': integer periods required')
        if bid.target_id not in buses:
            raise HindsightInputError(uid+': unknown renewable bus')
        values = {t:_finite(actual.renewable_available_mw[uid][t],uid+' actual') for t in ids}
        if any(v>bid.curve.quantity_breakpoints_mw[-1] for v in values.values()):
            raise HindsightInputError(uid+': actual availability exceeds original curve capacity')
        for t,v in values.items():
            availability[bid.target_id,t] += v
        renewed.append(replace(bid,availability_mw=values,availability_source=actual.provenance[uid]['source']))
    # Reject rather than silently erase market commitments/anchors supplied by a caller.
    stores = k.get('storage_by_stage')
    if stores is not None:
        _keys(stores, {'DA_SCUC','DA_SCED'}, 'storage stages')
        for stage,s in stores.items():
            if s['anchor_periods'] or s.get('pumped_plan_boundary') is not None or s.get('rt_pumped_boundary_policy'):
                raise HindsightInputError('market DA anchors/pumped plan cannot bind own hindsight trajectory')
            if s['day_end_period'] != ids[-1]:
                raise HindsightInputError('preserved full-day terminal boundary required')
            for r in s['batteries'].values():
                if r.get('da_anchor_mwh') or r.get('da_direction'):
                    raise HindsightInputError('market DA battery anchors/directions are forbidden')
            for r in s['pumped_plants'].values():
                if any(r.get(key) for key in ('da_upper_m3','da_lower_m3','upper_band_m3','lower_band_m3','band_penalty_yuan_per_m3_step')):
                    raise HindsightInputError('market DA reservoir trajectory bands are forbidden')
    core['load_mw'] = deepcopy(actual.load_mw)
    core['renewable_mw'] = availability
    core['physical_tie_mw'] = deepcopy(actual.physical_tie_mw)
    source = 'Retrospective full-day actual inputs; no causal-availability or forecast claim. '
    core['source_notes']['exogenous'] = source + '; '.join(r['source'] for r in actual.provenance.values())
    k['piecewise_bids']['renewable_bids'] = tuple(renewed)
    k['services'] = replace(k['services'], forecast_source=source)
    audit = dict(schema_version=1, scope='Guangdong-provincial-full-day-hindsight-research',
        cross_regional_market=False, conventional_hydro=False,
        information_set='all_day_actual_exogenous_and_explicit_fixed_scenario_boundaries',
        actual_provenance=deepcopy(actual.provenance),fixed_boundary_provenance=deepcopy(actual.fixed_boundaries),
        synthetic_actual=any(r['kind']=='synthetic_actual' for r in actual.provenance.values()),
        authenticated_operational_actual=all(actual.provenance[n]['kind']=='observed_actual' for n in ('load',*inventory)),
        forecast_fallback_used=False, full_day_actual_used_in_SCUC_and_SCED=True,
        own_storage_trajectories=True, market_DA_storage_anchors_used=False,
        thermal_commitment_source='this_hindsight_SCUC_only',
        service_boundary='preserved_explicit_external_capacity_not_ancillary_auction',
        service_information_cutoff_interpretation='administrative_boundary_validation_only_not_actual_data_availability',
        original_network_resource_quote_initial_and_terminal_inputs_preserved=True,
        actual_input_sha256=_hash(actual), template_sha256=_hash(workflow_inputs),
        solver='highs',threads=1,requested_mip_rel_gap=.001,
        exact_global_optimality_claim=False,global_lower_bound_claim=False,
        physical_cost=dict(available=False,value_yuan=None,reason='No independent complete physical-cost dataset supplied; bids are not operating costs.'),
        settlement=dict(available=False,value_yuan=None,reason='No complete settlement ledger; payments are transfers, not system resource costs.'))
    return k,audit


def run_hindsight_reference(*, workflow_inputs, actual, stage_callback=None):
    """Solve own all-day SCUC/SCED; never reuse the market DA storage schedule."""
    kwargs,audit=prepare_hindsight_reference(workflow_inputs=workflow_inputs,actual=actual)
    result=run_day_ahead_reference(**kwargs,stage_callback=stage_callback)
    result['hindsight_audit']=audit
    try:
        result['comparison_summary']=summarize_hindsight(result,kwargs)
    except Exception as exc:
        raise WorkflowSolveError('HINDSIGHT_REPORT: '+str(exc),stages=result['stages'],events=result['events']) from exc
    return result


def _value(value):
    return float(pyo.value(value))


def _max(values):
    return max(values,default=0.)


def dispatch_rows(model, services):
    """One full-horizon selected dispatch row, with original MW and hour units."""
    rows=[]
    for t in model.T:
        sum_bus=lambda component:sum(_value(component[b,t]) for b in model.B)
        reserve=model.gd_reserve
        upmargin=_value(reserve.c_up[t].body) if hasattr(reserve,'c_up') else None
        downmargin=-_value(reserve.c_down[t].body) if hasattr(reserve,'c_down') else None
        primary=_value(reserve.primary_short[t])
        load=sum_bus(model.demand_mw); renew=sum_bus(model.renewable_power)
        tie=sum_bus(model.physical_tie); thermal=sum(_value(model.power[g,t]) for g in model.G)
        storage=sum_bus(model.other_injection)
        short=sum_bus(model.balance_shortfall); surplus=sum_bus(model.balance_surplus)
        rows.append(dict(global_period=int(t),start=services.intervals[t][0].isoformat(),end=services.intervals[t][1].isoformat(),
            duration_h=model._energy_horizon.duration_h[t],load_mw=load,renewable_mw=renew,
            renewable_available_mw=renew+sum_bus(model.renewable_curtailment),curtailment_mw=sum_bus(model.renewable_curtailment),
            tie_mw=tie,thermal_mw=thermal,storage_net_mw=storage,balance_shortfall_mw=short,balance_surplus_mw=surplus,
            balance_residual_mw=thermal+renew+tie+storage+short-surplus-load,
            max_line_slack_mw=_max(_value(model.line_over[l,t])+_value(model.line_under[l,t]) for l in model.L),
            max_section_slack_mw=_max(_value(model.section_over[s,t])+_value(model.section_under[s,t]) for s in model.S),
            primary_shortfall_mw=primary,operating_up_margin_mw=upmargin,operating_down_margin_mw=downmargin,
            operating_up_shortfall_mw=max(0.,-upmargin) if upmargin is not None else None,
            operating_down_shortfall_mw=max(0.,-downmargin) if downmargin is not None else None))
    return rows


def resource_rows(model):
    rows=[]
    for t in model.T:
        for g in model.G:
            rows.append(dict(global_period=int(t),kind='thermal',resource=g,power_mw=_value(model.power[g,t]),
                online=_value(model.online[g,t]),start=_value(model.start[g,t]),stop=_value(model.stop[g,t])))
        if hasattr(model,'gd_storage'):
            b=model.gd_storage
            for u in b.BAT:
                rows.append(dict(global_period=int(t),kind='battery',resource=u,power_mw=_value(b.battery_net_power[u,t]),
                    charge_mw=_value(b.charge[u,t]),discharge_mw=_value(b.discharge[u,t]),energy_mwh=_value(b.energy[u,t])))
            for u in b.UNIT:
                rows.append(dict(global_period=int(t),kind='pumped_unit',resource=u,power_mw=_value(b.unit_net_power[u,t]),
                    generation_mw=_value(b.generate[u,t]),pump_mw=_value(b.pump[u,t]),mode=next(mode for mode in b.MODES if _value(b.mode[u,t,mode])>.5)))
            for plant in b.PLANT:
                rows.append(dict(global_period=int(t),kind='pumped_plant',resource=plant,power_mw=_value(b.plant_net_power[plant,t]),
                    upper_volume_m3=_value(b.upper_volume[plant,t]),lower_volume_m3=_value(b.lower_volume[plant,t])))
    return rows


def summarize_hindsight(result, kwargs):
    selected=result['selected_dispatch']; model=selected.model
    costs={name:_value(getattr(model,name)) for name in COST_COMPONENTS if hasattr(model,name)}
    if hasattr(model,'gd_storage'):
        costs.update({name:_value(getattr(model.gd_storage,name)) for name in COST_COMPONENTS if hasattr(model.gd_storage,name)})
    costs['reserve_penalty_cost']=_value(model.gd_reserve.penalty_cost)
    objective=_value(model.objective); residual=objective-sum(costs.values())
    if abs(residual)>1e-5+1e-9*abs(objective):
        raise HindsightInputError('hindsight objective/cost component reconciliation failed')
    rows=dispatch_rows(model,kwargs['services'])
    max_residual=0.; violations=0
    for c in model.component_data_objects(pyo.Constraint,active=True):
        body=_value(c.body)
        violation=max(0.,0. if c.lower is None else _value(c.lower)-body,0. if c.upper is None else body-_value(c.upper))
        max_residual=max(max_residual,violation);violations+=violation>1e-6
    return dict(completed=True,scope='All-day own actual-information SCUC/SCED selected continuous polish',
        selected_stage=selected.metrics['stage'],quoted_objective_cost_yuan=objective,cost_components_yuan=costs,
        objective_reconciliation_residual_yuan=residual,physical=aggregate_dispatch(rows),
        max_active_constraint_residual=max_residual,constraint_violations_gt_1e_6=violations,
        terminal_state=deepcopy(result['executed_boundary']),solver_stages={n:deepcopy(s.metrics) for n,s in result['stages'].items()},
        physical_cost=deepcopy(result['hindsight_audit']['physical_cost']),settlement=deepcopy(result['hindsight_audit']['settlement']))


def aggregate_dispatch(rows):
    if not rows:
        raise HindsightInputError('nonempty chronological dispatch rows required')
    totals={}
    for key in ('load_mw','renewable_available_mw','renewable_mw','curtailment_mw','tie_mw','thermal_mw','storage_net_mw',
                'balance_shortfall_mw','balance_surplus_mw','primary_shortfall_mw','operating_up_shortfall_mw','operating_down_shortfall_mw','emergency_shortfall_mw'):
        totals[key.removesuffix('_mw')+'_mwh']=(sum(_finite(r[key],key,negative=True)*_finite(r['duration_h'],'duration_h') for r in rows)
            if all(r.get(key) is not None for r in rows) else None)
    for key in ('max_line_slack_mw','max_section_slack_mw','balance_residual_mw'):
        target=key if key.startswith('max_') else 'max_abs_'+key
        totals[target]=max(abs(_finite(r[key],key,negative=True)) for r in rows) if all(r.get(key) is not None for r in rows) else None
    totals.update(intervals=len(rows),first_start=rows[0]['start'],last_end=rows[-1]['end'])
    return totals


def compare_dispatch_reports(*, hindsight, market_rt, hindsight_rows, market_rows):
    """Transparent same-time-range arithmetic, never a lower-bound certificate.

    Different RT forecast/actual trajectories are quantified, not hidden. The
    caller supplies only 96 executed first intervals, never overlapping windows.
    """
    for label,rows in (('hindsight',hindsight_rows),('market RT',market_rows)):
        if len(rows)!=96 or [r['global_period'] for r in rows]!=list(range(96)):
            raise HindsightInputError(label+': exact 96 ordered executed intervals required')
        previous=None
        for r in rows:
            try:
                start,end=datetime.fromisoformat(r['start']),datetime.fromisoformat(r['end'])
            except (ValueError,TypeError) as exc:
                raise HindsightInputError(label+': explicit ISO interval timestamps required') from exc
            if start.utcoffset() is None or end.utcoffset() is None or (end-start).total_seconds()!=900 or (previous is not None and previous!=start):
                raise HindsightInputError(label+': contiguous timezone-aware 15-minute intervals required')
            if _finite(r['duration_h'],'duration_h') != .25:
                raise HindsightInputError(label+': duration must match interval')
            previous=end
    if [(r['start'],r['end']) for r in hindsight_rows]!=[(r['start'],r['end']) for r in market_rows]:
        raise HindsightInputError('comparison requires identical full-day time range')
    mismatches={}
    for key in ('load_mw','renewable_available_mw','tie_mw'):
        differences=[_finite(a[key],key,negative=key=='tie_mw')-_finite(b[key],key,negative=key=='tie_mw') for a,b in zip(hindsight_rows,market_rows)]
        mismatches[key]=dict(max_absolute_difference_mw=max(map(abs,differences)),
            signed_energy_difference_mwh=sum(v*.25 for v in differences),absolute_energy_difference_mwh=sum(abs(v)*.25 for v in differences))
    same=all(v['max_absolute_difference_mw']<=1e-8 for v in mismatches.values())
    idealcost=_finite(hindsight['quoted_objective_cost_yuan'],'hindsight cost',negative=True)
    marketcost=_finite(market_rt['quoted_objective_cost_yuan'],'market cost',negative=True)
    return dict(schema_version=1,hindsight=deepcopy(hindsight),market_rt=deepcopy(market_rt),comparison=dict(
        full_day_interval_alignment_verified=True,same_exogenous_trajectory=same,exogenous_differences=mismatches,
        same_feasible_set=False,global_lower_bound_claim=False,exact_optimum_claim=False,
        quoted_objective_difference_market_minus_hindsight_yuan=marketcost-idealcost,
        quoted_objective_difference_is_savings=False,pure_forecast_value_claim=False,
        physical_cost_comparable=False,settlement_cash_comparable=False,
        limitations=[
            'Hindsight reruns own all-day SCUC/SCED, storage paths and fixed service boundary; RT fixes market DA commitment, battery hour anchors and external pumped plans.',
            'RT first-interval plans use offline truth-conditioned *_rt profiles; they are not a replay balancing against *_actual.',
            'DA operating reserve and RT emergency reserve have different capacity formulas and penalties; zero DA hard-constraint shortage is not equivalent to zero RT emergency shortage.',
            'Current actual curves, AGC and other boundaries are synthetic research inputs, not authenticated Guangdong operations.',
            'Quoted costs include optimization penalties; independent physical costs and settlement cash are unavailable.']))
