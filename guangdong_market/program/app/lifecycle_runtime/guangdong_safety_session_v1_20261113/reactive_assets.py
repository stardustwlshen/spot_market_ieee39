"""Source-bound static reactive devices. Pure compilation; no solver or authority grant."""
import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'finite numeric value required')
    return value


def stamp(value):
    t = datetime.fromisoformat(value)
    require(t.utcoffset() == timedelta(hours=8), 'UTC+8 timestamp required')
    return t


def evidence(ref, root):
    require(set(ref) == {'path', 'sha256'}, 'source reference schema')
    root = Path(root).resolve()
    p = (root / ref['path']).resolve()
    require(p.is_relative_to(root) and p.is_file(), 'missing or out-of-root source')
    raw = p.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'source hash changed')
    return json.loads(raw)


def compile_reactive_assets(ac_input, assets, *, source_root, start, end, information_cutoff):
    """Caller must separately validate AC network, SCED acceptance, post-AC safety and gates.

    Nonideal losses must ALREADY be in input demand and an externally accepted SCED
    ledger. This compiler verifies binding, not external acceptance or meter truth.
    No new device ever gains energy/reserve/AGC service credit.
    """
    require(type(assets) is list, 'assets must be a list')
    digest(ac_input); digest(assets)
    ac = copy.deepcopy(ac_input)
    output = dict(ac_input=ac, bindings=[], ledger=[], gates=dict(
        block_normal_price=False, block_execution=False, safety_granted=False,
        execution_granted=False, formal_release=False, requires_independent_AC=bool(assets)))
    if not assets:
        return output
    begin, finish, cutoff = map(stamp, (start, end, information_cutoff))
    require(cutoff <= begin < finish, 'invalid interval')
    buses = {b['id']: b for b in ac['buses']}
    require(len(buses) == len(ac['buses']), 'duplicate network bus')
    occupied = {g['bus'] for g in ac['generators']}
    ids = {g['id'] for g in ac['generators']}
    for entry in assets:
        require(set(entry) == {'spec', 'source'}, 'asset entry schema')
        s = entry['spec']
        require(evidence(entry['source'], source_root) == s, 'source parameters differ')
        require(set(s) == {'schema','id','bus','external_bus','kind','model','state','available_at','valid_from','valid_to','zero_p_enabled','capability','control','p_loss_mw','loss_accounting','market_credit','provenance'}, 'asset spec schema')
        prov = s['provenance']
        require(type(prov) is dict and set(prov) == {'kind','statement','reference'}, 'provenance schema required')
        require(prov['kind'] in ('research_assumption','device_document') and all(isinstance(prov[k], str) and prov[k].strip() for k in ('statement','reference')), 'explicit provenance required')
        require(s['model'] != 'ideal_lossless_research' or prov['kind'] == 'research_assumption', 'ideal model requires research provenance')
        require(s['schema'] == 'gd.reactive_asset.v1' and s['kind'] == 'static_q', 'unsupported device kind')
        require(isinstance(s['id'], str) and s['id'] and s['id'] not in ids, 'duplicate/invalid device id')
        require(s['bus'] in buses and s['bus'] not in occupied, 'unknown or unaggregated occupied bus')
        require(type(s['external_bus']) is int and str(s['external_bus']-1) == s['bus'], 'IEEE39 external/internal bus mismatch')
        require(all(type(v) is bool for v in s['state'].values()) and s['state'] == {'energized': True, 'connected': True, 'maintenance': False}, 'offline/unknown device state')
        require(any(l['status'] == 1 and s['bus'] in (l['from_bus'],l['to_bus']) for l in ac['branches']), 'bus not energized by network')
        require(stamp(s['available_at']) <= cutoff and stamp(s['valid_from']) <= begin and stamp(s['valid_to']) >= finish, 'noncausal or unavailable device')
        require(s['zero_p_enabled'] is True, 'zero-P reactive operation not supported')
        require(all(type(v) is bool for v in s['market_credit'].values()) and s['market_credit'] == {'energy': False, 'reserve': False, 'AGC': False}, 'reactive capability grants no market credit')
        cap = s['capability']; ctl = s['control']; b = buses[s['bus']]
        require(set(cap) <= {'qmin_mvar','qmax_mvar','s_mva','imax_ka','valid_voltage_range_pu'} and {'qmin_mvar','qmax_mvar','valid_voltage_range_pu'} <= set(cap), 'unsupported capability/tap control')
        vr = cap['valid_voltage_range_pu']
        require(type(vr) is list and len(vr) == 2, 'capability voltage validity required')
        vlo, vhi = number(vr[0]), number(vr[1])
        require(0 < vlo <= b['vmin_pu'] <= b['vmax_pu'] <= vhi, 'capability voltage domain does not cover bus limits')
        qlo, qhi = number(cap['qmin_mvar']), number(cap['qmax_mvar'])
        require(qlo <= qhi and 0 < b['vmin_pu'] <= b['vmax_pu'], 'invalid Q/V bounds')
        loss = number(s['p_loss_mw']); require(loss >= 0, 'negative loss')
        envelope = math.hypot(loss, max(abs(qlo),abs(qhi)))
        if 's_mva' in cap:
            require(number(cap['s_mva']) > 0 and envelope <= cap['s_mva'] + 1e-9, 'Q/S capability violation')
        if 'imax_ka' in cap:
            require(number(cap['imax_ka']) > 0 and envelope <= math.sqrt(3)*b['base_kv']*b['vmin_pu']*cap['imax_ka']+1e-9, 'current capability violation')
        require(s['model'] in ('ideal_lossless_research','constant_loss_static'), 'unknown physical model')
        if s['model'] == 'ideal_lossless_research':
            require(loss == 0 and s['loss_accounting'] is None, 'ideal research loss must be explicitly zero')
            output['gates'].update(block_normal_price=True, block_execution=True)
            accounting = 'explicit_ideal_research_zero_not_physical_claim'
        else:
            require(s['loss_accounting'] is not None, 'SCED loss evidence required')
            record = evidence(s['loss_accounting'], source_root)
            expected = dict(schema='gd.reactive_loss_ledger.v1', asset_id=s['id'], bus=s['bus'], p_loss_mw=loss,
                start=start, end=end, ac_input_sha256=digest(ac_input), accounting='included_once_in_bus_pd_and_SCED',
                available_at=s['available_at'])
            require(record == expected, 'loss accounting binding mismatch')
            require(b['pd_mw'] >= loss, 'loss not representable in bound bus demand')
            # This source-bound statement is not itself proof of accepted market accounting.
            output['gates'].update(block_normal_price=True, block_execution=True)
            accounting = 'already_in_bound_demand_no_second_P_debit_requires_external_SCED_acceptance'
        if ctl.get('mode') == 'Q':
            require(set(ctl) == {'mode','q_mvar'}, 'Q control schema/tap action unsupported')
            q = number(ctl['q_mvar']); require(qlo <= q <= qhi, 'Q target outside capability')
            require(b['type'] == 'PQ', 'fixed-Q bus must be PQ')
            b['qd_mvar'] -= q
        elif ctl.get('mode') == 'PV':
            require(set(ctl) == {'mode','vg_pu'}, 'PV control schema/tap action unsupported')
            v = number(ctl['vg_pu']); require(b['vmin_pu'] <= v <= b['vmax_pu'], 'V target outside limits')
            require(b['type'] == 'PQ', 'PV conversion requires vacant PQ bus')
            b['type'] = 'PV'
            ac['generators'].append(dict(id=s['id'],bus=s['bus'],pg_mw=0.,qg_mvar=min(qhi,max(qlo,0.)),pmin_mw=0.,pmax_mw=0.,qmin_mvar=qlo,qmax_mvar=qhi,vg_pu=v))
        else:
            raise ValueError('unsupported Q/V/tap control mode')
        occupied.add(s['bus']); ids.add(s['id'])
        output['bindings'].append(dict(asset_id=s['id'],source=copy.deepcopy(entry['source']),spec_sha256=digest(s)))
        output['ledger'].append(dict(asset_id=s['id'],bus=s['bus'],p_loss_mw=loss,additional_P_debit_mw=0.,accounting=accounting,energy_credit=0.,reserve_credit=0.,AGC_credit=0.))
    ac['provenance'] = str(ac.get('provenance','')) + '; explicit source-bound static reactive diagnostic, not normal market/operational authorization'
    return output
