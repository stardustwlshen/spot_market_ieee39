"""Explicit DA98 semantic contract; no solver imports or implicit tail chronology.

Current public rule evidence does not resolve representative timing/weights or
storage terminal mapping. Strict mode therefore fails closed. Research mode
requires authored mapping and never issues execution or official-price authority.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math

SCHEMA = 'gd.r14.da98.contract.v1'
OFFICIAL_GAPS = (
    'representative_absolute_time_and_order',
    'representative_objective_and_energy_weights',
    'representative_ramp_and_state_connections',
    'storage_terminal_rule_version_conflict',
)


class DA98ContractError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise DA98ContractError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def number(value, label, *, minimum=0., positive=False):
    require(type(value) in (int, float) and math.isfinite(value), label + ': finite number required')
    require(value >= minimum and (not positive or value > 0), label + ': invalid sign')
    return float(value)


def text(value, label):
    require(type(value) is str and bool(value.strip()), label + ': explicit nonempty source/value required')
    return value


def stamp(value, label):
    text(value, label)
    try:
        d = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DA98ContractError(label + ': invalid timestamp') from exc
    require(d.utcoffset() is not None, label + ': timezone required')
    return d


@dataclass(frozen=True)
class DA98Contract:
    canonical_bytes: bytes
    sha256: str

    def __post_init__(self):
        require(type(self.canonical_bytes) is bytes, 'canonical bytes required')
        require(sha256(self.canonical_bytes).hexdigest() == self.sha256, 'contract digest mismatch')
        d = json.loads(self.canonical_bytes)
        require(canonical(d) == self.canonical_bytes, 'contract not canonical')
        _validate(d)

    def payload(self):
        return json.loads(self.canonical_bytes)


def _validate(data):
    require(data.get('schema') == SCHEMA, 'DA98 schema')
    require(data.get('mode') in ('strict', 'research'), 'strict or research mode required')
    # These are unresolved in the currently verified rule set, not flags a
    # caller can remove to acquire a claim of official equivalence.
    if data['mode'] == 'strict':
        raise DA98ContractError('strict mode blocked by unresolved official evidence: ' + ', '.join(OFFICIAL_GAPS))
    text(data.get('scenario_id'), 'scenario_id')
    require(data.get('timezone') == 'Asia/Shanghai', 'Shanghai market timezone required')
    day = stamp(data.get('day_start'), 'day_start')
    require(day.utcoffset() == timedelta(hours=8) and not any((day.hour, day.minute, day.second, day.microsecond)), 'Shanghai midnight required')
    cutoff = stamp(data.get('information_cutoff'), 'information_cutoff')
    require(cutoff <= day, 'DA cutoff after delivery start')
    stamp(data.get('generated_at'), 'generated_at')
    require(type(data.get('research_assumptions')) is dict and bool(data['research_assumptions']), 'explicit research assumptions required')
    for k, v in data['research_assumptions'].items():
        text(k, 'assumption id'); text(v, 'assumption source and description')
    require(data.get('official_equivalence_verified') is False, 'official equivalence cannot be claimed')
    require(data.get('execution_authority') is False, 'contract cannot issue execution authority')
    require(data.get('missing_official_evidence') == list(OFFICIAL_GAPS), 'retain unresolved official evidence')
    grid = data.get('grid')
    require(type(grid) is list and len(grid) == 98, 'exactly 98 market points required')
    ids = [text(p.get('id'), 'point id') for p in grid]
    require(len(set(ids)) == 98, 'duplicate point IDs')
    require([p.get('role') for p in grid[:96]] == ['delivery'] * 96, 'first 96 points must be delivery')
    require({p.get('role') for p in grid[96:]} == {'representative_peak', 'representative_valley'}, 'two distinct peak/valley representative points required')
    for i, p in enumerate(grid):
        require(p.get('power_semantics') in ('instantaneous_sample', 'interval_mean'), 'explicit power semantics required')
        text(p.get('mapping_source'), 'point mapping source')
        number(p.get('objective_weight_h'), 'objective weight', positive=True)
        number(p.get('energy_weight_h'), 'energy weight')
        require(type(p.get('publishable')) is bool and type(p.get('settlement_eligible')) is bool, 'explicit publication/settlement flags required')
        if i < 96:
            a, b = day + timedelta(minutes=15*i), day + timedelta(minutes=15*(i+1))
            require(stamp(p.get('delivery_start'), 'delivery_start') == a and stamp(p.get('delivery_end'), 'delivery_end') == b, 'exact D-day quarter coverage required')
            require(p['publishable'] and p['settlement_eligible'], 'D-day point output domain')
            require(abs(p['energy_weight_h'] - .25) < 1e-12, 'delivery energy weight must be quarter hour')
            require(abs(p['objective_weight_h'] - .25) < 1e-12, 'delivery objective weight must be quarter hour')
            if p['power_semantics'] == 'instantaneous_sample':
                at = stamp(p.get('sample_at'), 'sample_at')
                require(a <= at <= b, 'sample timestamp outside associated delivery interval')
            else:
                require(p.get('sample_at') is None, 'interval mean cannot also be instantaneous sample')
        else:
            require(p.get('delivery_start') is None and p.get('delivery_end') is None, 'representative points cannot masquerade as delivery intervals')
            require(not p['publishable'] and not p['settlement_eligible'], 'representative point cannot publish/settle D-day')
            if p['power_semantics'] == 'instantaneous_sample':
                require(p.get('projection_window') is None, 'instantaneous sample cannot be an interval projection')
                at = stamp(p.get('sample_at'), 'representative sample_at')
                require(day + timedelta(days=1) <= at < day + timedelta(days=2), 'representative sample must lie in D+1')
            else:
                require(p.get('sample_at') is None, 'interval mean cannot also be instantaneous sample')
                w = p.get('projection_window')
                require(type(w) is dict, 'representative mean needs explicit projection_window')
                a, at = stamp(w.get('start'), 'projection start'), stamp(w.get('end'), 'projection end')
                text(w.get('source'), 'projection source')
                require(day + timedelta(days=1) <= a < at <= day + timedelta(days=2) and at-a == timedelta(minutes=15), 'representative projection must cover one explicit D+1 quarter')
                require(abs(p['energy_weight_h']-.25) < 1e-12, 'representative mean energy weight must equal projection interval')
    require(data.get('daily_energy_point_ids') == ids[:96], 'daily energy domain is exactly T96, excludes representatives')
    require(data.get('day_end_state_id') == ids[95], 'D-day end must not move to representative tail')
    initial = text(data.get('day_start_state_id'), 'day_start_state_id')
    require(initial not in ids, 'initial state cannot alias a market point')
    terminal = data.get('storage_terminal_state_id')
    require(terminal in ids, 'storage terminal state must be explicit grid state')
    text(data.get('storage_terminal_rule_version'), 'storage terminal rule version/interpretation')
    text(data.get('storage_terminal_source'), 'storage terminal source')
    edges = data.get('state_edges')
    require(type(edges) is list and len(edges) == 98, 'one explicit incoming state edge per point required')
    require({e.get('to_state') for e in edges} == set(ids), 'state edges must cover each point once')
    require(len({text(e.get('id'), 'edge id') for e in edges}) == 98, 'duplicate edge IDs')
    rank = {initial: -1, **{k: i for i, k in enumerate(ids)}}
    for e in edges:
        to, previous = e['to_state'], e.get('from_state')
        require(previous in rank and rank[previous] < rank[to], 'state edge must be acyclic and point to an earlier declared state')
        number(e.get('elapsed_h'), 'edge elapsed_h', positive=True)
        number(e.get('energy_weight_h'), 'edge energy_weight_h')
        text(e.get('source'), 'edge source')
        require(type(e.get('carry_thermal_residence')) is bool, 'explicit thermal residence carry required')
        require(e['carry_thermal_residence'], 'thermal residence reset forbidden')
        if rank[to] < 96:
            expected = initial if rank[to] == 0 else ids[rank[to]-1]
            require(previous == expected and abs(e['elapsed_h']-.25) < 1e-12 and abs(e['energy_weight_h']-.25) < 1e-12, 'delivery state chain must retain quarter chronology')
        else:
            # A representative has no automatic clock or inventory reset.
            end = day if previous == initial else (stamp(grid[rank[previous]]['delivery_end'], 'edge previous end') if rank[previous] < 96 else _representative_end(grid[rank[previous]]))
            at = _representative_end(grid[rank[to]])
            require(abs((at-end).total_seconds()/3600-e['elapsed_h']) < 1e-9, 'representative elapsed must match explicit sample timestamps')
        require(abs(e['energy_weight_h']-grid[rank[to]]['energy_weight_h']) < 1e-12, 'point/edge inventory energy weights disagree')
    return data



def _representative_end(point):
    if point['power_semantics'] == 'instantaneous_sample':
        return stamp(point['sample_at'], 'edge sample')
    return stamp(point['projection_window']['end'], 'edge projection end')


def validate_physical_projection(contract, physical_lift):
    """Audit actual timestamp/semantics binding before any model is built.

    The supported physical bridge is a contiguous five-minute *interval mean*
    trajectory. It cannot certify instantaneous values by relabeling averages.
    Representative point energy weights describe the projected quarter only;
    state integration through intervening cells remains the builder's duty.
    """
    c = contract if isinstance(contract, DA98Contract) else validate_contract(contract)
    d = c.payload(); x = deepcopy(physical_lift)
    cells, mapping = x.get('cells'), x.get('market_to_physical')
    require(type(cells) is list and bool(cells), 'physical cells required')
    require(type(mapping) is list and len(mapping) == 98, '98 physical mappings required')
    day = stamp(d['day_start'], 'day start')
    for j, cell in enumerate(cells):
        require(stamp(cell.get('start'), 'cell start') == day+timedelta(minutes=5*j) and stamp(cell.get('end'), 'cell end') == day+timedelta(minutes=5*(j+1)), 'physical clock must be contiguous five-minute intervals')
    used=set()
    for point, ids in zip(d['grid'], mapping):
        require(point['power_semantics'] == 'interval_mean', 'three-cell interval mean cannot certify instantaneous sample')
        require(type(ids) is list and len(ids)==3 and all(type(j) is int for j in ids), 'three physical cell indices required')
        require(ids==list(range(ids[0],ids[0]+3)) and 0<=ids[0] and ids[-1]<len(cells), 'physical mapping indices must be contiguous and in domain')
        require(not used.intersection(ids), 'market projection cells cannot overlap')
        used.update(ids)
        if point['role']=='delivery': a,b=point['delivery_start'],point['delivery_end']
        else: a,b=point['projection_window']['start'],point['projection_window']['end']
        require(stamp(cells[ids[0]]['start'], 'mapped start')==stamp(a, 'declared start') and stamp(cells[ids[-1]]['end'], 'mapped end')==stamp(b, 'declared end'), 'physical mapping does not match declared representative/delivery window')
        require(abs(point['energy_weight_h']-.25)<1e-12,'projected mean energy weight mismatch')
    return dict(contract_sha256=c.sha256,physical_clock_sha256=sha256(canonical(cells)).hexdigest(),market_mapping_sha256=sha256(canonical(mapping)).hexdigest(),projection_kind='three_five_minute_interval_means',instantaneous_price_certificate=False,official_equivalence_verified=False,physical_cells=len(cells),unmapped_intervening_cells=len(cells)-len(used))


def contract_from_lift(data, *, scenario_id, information_cutoff, generated_at,
                       research_assumptions, storage_terminal_state_id,
                       storage_terminal_rule_version, storage_terminal_source):
    """Explicit adapter for the SCED/LMP local grid DTO, not a solver permit.

    local actual_time is a window-end *label*, never an instantaneous value.
    It must equal the interval end and the mapped physical cell end exactly.
    """
    grid=data['grid']; require(len(grid)==98,'98 local grid points required')
    reps=[]; edges=[]; ids=[f'D{i+1:02d}' for i in range(96)]+[f'R{i+1}' for i in range(2)]
    for i,g in enumerate(grid):
        require(g.get('power_semantics')=='interval_mean','lift grid must explicitly declare interval_mean; instantaneous conversion prohibited')
        require(g.get('actual_time')==g.get('end'),'actual_time must label the declared mean-window end')
        if i<96: continue
        require(g.get('kind') in ('peak','valley'),'representative kind')
        prev=g.get('predecessor'); require(type(prev) is int and 0<=prev<i,'representative predecessor cannot reset')
        text(g.get('constraint_scope'),'projection mapping source')
        reps.append(dict(id=ids[i],role='representative_'+g['kind'],delivery_start=None,delivery_end=None,sample_at=None,power_semantics='interval_mean',projection_window=dict(start=g['start'],end=g['end'],source=g['constraint_scope']),objective_weight_h=g['objective_weight_h'],energy_weight_h=g['physical_duration_h'],publishable=False,settlement_eligible=False,mapping_source=g['constraint_scope']))
        edges.append(dict(id='edge:'+ids[i],from_state=ids[prev],to_state=ids[i],elapsed_h=g['ramp_elapsed_h'],energy_weight_h=g['physical_duration_h'],carry_thermal_residence=True,source=g['constraint_scope']))
    result=make_research_contract(scenario_id=scenario_id,day_start=grid[0]['start'],information_cutoff=information_cutoff,generated_at=generated_at,delivery_power_semantics='interval_mean',delivery_sample_position=None,representatives=reps,representative_edges=edges,initial_state_id='initial',storage_terminal_state_id=storage_terminal_state_id,storage_terminal_rule_version=storage_terminal_rule_version,storage_terminal_source=storage_terminal_source,research_assumptions=research_assumptions)
    for i,g in enumerate(grid[:96]):
        p=result.payload()['grid'][i]
        require(g['start']==p['delivery_start'] and g['end']==p['delivery_end'],'local D96 timestamps conflict with contract')
        require(g['objective_weight_h']==g['physical_duration_h']==g['ramp_elapsed_h']==.25 and g['predecessor']==(i-1 if i else None),'local D96 time/weights conflict with contract')
    validate_physical_projection(result,data['physical_lift'])
    return result


def validate_contract(data):
    """Return immutable validated copy; caller edits cannot change its identity."""
    copied = deepcopy(data)
    _validate(copied)
    blob = canonical(copied)
    return DA98Contract(blob, sha256(blob).hexdigest())


def make_research_contract(*, scenario_id, day_start, information_cutoff,
                          generated_at, delivery_power_semantics, delivery_sample_position,
                          representatives, representative_edges, initial_state_id,
                          storage_terminal_state_id, storage_terminal_rule_version,
                          storage_terminal_source, research_assumptions):
    """No defaults for representative weights, connections or sample semantics.

    representatives and representative_edges are authored records with all
    fields validated above. Delivery sample labels are an explicit convention,
    never a claim that the rules mandated 00:15 as the first label.
    """
    day = stamp(day_start, 'day_start')
    require(delivery_power_semantics in ('instantaneous_sample', 'interval_mean'), 'delivery semantics')
    require(delivery_sample_position in ('start', 'end', None), 'sample position')
    require((delivery_power_semantics == 'interval_mean') == (delivery_sample_position is None), 'sample position must agree with power semantics')
    grid=[]; edges=[]
    for i in range(96):
        uid=f'D{i+1:02d}'; a=day+timedelta(minutes=15*i); b=a+timedelta(minutes=15)
        grid.append(dict(id=uid, role='delivery', delivery_start=a.isoformat(), delivery_end=b.isoformat(), sample_at=(a if delivery_sample_position=='start' else b).isoformat() if delivery_sample_position else None, power_semantics=delivery_power_semantics, objective_weight_h=.25, energy_weight_h=.25, publishable=True, settlement_eligible=True, mapping_source='D-day 96 quarter intervals; sample label is explicit research convention'))
        edges.append(dict(id=f'edge:{uid}',from_state=initial_state_id if i==0 else f'D{i:02d}',to_state=uid,elapsed_h=.25,energy_weight_h=.25,carry_thermal_residence=True,source='D-day contiguous quarter-hour physical chain'))
    grid.extend(deepcopy(representatives)); edges.extend(deepcopy(representative_edges))
    return validate_contract(dict(schema=SCHEMA,mode='research',scenario_id=scenario_id,timezone='Asia/Shanghai',day_start=day_start,information_cutoff=information_cutoff,generated_at=generated_at,research_assumptions=research_assumptions,official_equivalence_verified=False,execution_authority=False,missing_official_evidence=list(OFFICIAL_GAPS),grid=grid,state_edges=edges,day_start_state_id=initial_state_id,day_end_state_id='D96',daily_energy_point_ids=[f'D{i+1:02d}' for i in range(96)],storage_terminal_state_id=storage_terminal_state_id,storage_terminal_rule_version=storage_terminal_rule_version,storage_terminal_source=storage_terminal_source))


def validate_lift_contract_binding(data):
    """Validate bound contract AND rebuild local-grid interpretation at use time.

    Checking the saved hash alone would miss a subsequently relabeled local
    grid. Use this gate before SCED/LMP model construction and serialization.
    """
    saved=validate_contract(data.get('contract',{})); d=saved.payload()
    require(data.get('contract_sha256')==saved.sha256,'bound contract hash mismatch')
    rebuilt=contract_from_lift(data,scenario_id=d['scenario_id'],information_cutoff=d['information_cutoff'],generated_at=d['generated_at'],research_assumptions=d['research_assumptions'],storage_terminal_state_id=d['storage_terminal_state_id'],storage_terminal_rule_version=d['storage_terminal_rule_version'],storage_terminal_source=d['storage_terminal_source'])
    require(rebuilt.sha256==saved.sha256,'local grid semantics differ from bound contract')
    return validate_physical_projection(saved,data['physical_lift'])
