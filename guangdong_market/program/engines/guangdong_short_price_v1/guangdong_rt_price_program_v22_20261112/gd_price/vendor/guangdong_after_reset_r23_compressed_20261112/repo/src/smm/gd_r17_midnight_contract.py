"""PURE source-bound 309-cell/103-quarter research contract. Never solves.

R15 is retained verbatim. This module does NOT widen its native/session guards.
Template states are archived DA mathematical examples, never simulated receipts.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from weakref import WeakKeyDictionary
import math

from . import gd_r15_da_rt_bridge as old
from .gd_r15_da_rt_bridge import require, close, digest, stamp, finite, DT
from .gd_r15_two_window import _forecast, project_issued_prefix

SCHEMA = 'gd.research.r17.midnight_contract.v1'
TAIL_KIND = 'archived_R14_physical_SCED_3cell_mean_not_published_DA_award'
POLICY = dict(schema=SCHEMA+'.policy', tail_reference=TAIL_KIND,
    midnight='preserve_original_D96_research_target_not_official_rule',
    rolling_endpoint='original_physical_SCED_energy_at_two_hour_end',
    cycle='Shanghai_cell_start_date_internal_throughput_with_inherited_use',
    physical_cells=309, reference_quarters=103, potential_D_windows=96,
    rolling_minutes=15, physical_minutes=5, price_minutes=15,
    forecasts='frozen_exogenous_prior_linear_interpolation_no_future_actuals',
    formal_price_publication='official_verified_Dplus1_or_necessary_Dplus2_not_synthetic_cutoff',
    native_release_available=False, native_calls_authorized=0,
    execution_authority=False, official_market=False, formal_bill=None,
    actual_telemetry=False, AGC_qualification_inferred=False,
    resource_formal_qualification=None, storage_15minute_settlement_qualification=None)
TARGETS = {'ES1':100., 'ES2':200., 'ES3':100., 'ES4':300., 'ES5':300.}
_SERVICES = ('positive','negative','primary','accident')
_CONTRACTS = WeakKeyDictionary()
_INPUTS = WeakKeyDictionary()
class ContractPermit:
    __slots__ = ('__weakref__',)
class PureInputPermit:
    __slots__ = ('__weakref__',)


def verify_upstream_modes(data, values):
    """Index0 is global hour24; bind all24 hours to original named MIP."""
    result = {}
    for r,modes in data['native_upstream_modes'].items():
        require(len(modes)==24, '24 D+1 upstream modes required')
        result[r]=[]
        for k,mode in enumerate(modes):
            h=k+24
            require(mode in ('charge','discharge','idle'), 'invalid upstream mode')
            onehot={m:finite(values[f'mode[{r},{h},{m}]']) for m in ('charge','discharge','idle')}
            for m,v in onehot.items(): close(v,int(m==mode),'original upstream one-hot mismatch')
            rows=data['physical_lift']['resources'][r]['rows'][12*h:12*h+12]
            require(len(rows)==12 and all(z['mode']==mode for z in rows), 'D+1 physical/upstream mode mismatch')
            result[r].append(dict(global_hour=h,Dplus1_hour=k,mode=mode,one_hot=onehot))
    return result


def quarter_eligibility(rows, kind, permission=None):
    """Modes/phases determine research eligibility; actual power never enters."""
    lo,hi=(math.fsum(z[k] for z in rows)/3 for k in ('min_mw','max_mw'))
    require(len(rows)==3, 'three physical rows required')
    if kind=='battery':
        modes={z['mode'] for z in rows}
        require(modes<= {'charge','idle','discharge'} and not {'charge','discharge'}<=modes, 'opposite battery modes in quarter')
        mode=next(iter(modes-{'idle'}),'idle')
        require(permission in (-1,0,1) and (mode=='idle' or permission=={'charge':-1,'discharge':1}[mode]), 'upstream direction permission mismatch')
        for z in rows:
            require(not z['reserve_eligible'] and all(z[s+'_cap_mw']==0 for s in _SERVICES), 'unqualified battery reserve forbidden')
            require((z['mode']=='idle' and z['min_mw']==z['max_mw']==0) or (z['mode']=='charge' and z['max_mw']<=0) or (z['mode']=='discharge' and z['min_mw']>=0), 'source mode bounds mismatch')
        detail=dict(mode=mode,active_mask=[z['mode']!='idle' for z in rows],price_eligible=mode!='idle',
            eligibility_reason='sourced same-direction active/idle mask and hourly permission; research only',
            selected_hour_direction_permission=permission,formal_15minute_settlement_qualification=None)
    else:
        phases={z['phase'] for z in rows}
        require(len(phases)==1 and phases<= {'stable','off','startup','shutdown'}, 'mixed or unsupported thermal quarter phases')
        phase=next(iter(phases))
        detail=dict(phase=phase,price_eligible=phase=='stable',active_mask=[True]*3,
            eligibility_reason='three sourced stable phases eligible; off/transition fixed; research only')
    return dict(min_mw=lo,max_mw=hi,formal_energy_price_qualification=None,**detail)


def _extend(base, data, values, upstream):
    cells=deepcopy(base['physical_cells']); state=deepcopy(base['terminal_state'])
    lift=data['physical_lift']; day=stamp(base['day_start'])
    for j in range(288,309):
        raw=lift['cells'][j]
        require(stamp(raw['start'])==day+timedelta(minutes=5*j) and stamp(raw['end'])==day+timedelta(minutes=5*(j+1)), 'tail physical chronology')
        c=dict(start=raw['start'],end=raw['end'],thermal={},batteries={},reserve_requirement_mw=deepcopy(lift['reserve'][j]))
        require(c['reserve_requirement_mw']==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.), 'original400/400 source reserves')
        for r,u in data['resources'].items():
            z=lift['resources'][r]['rows'][j]; p=values[f'physical_power[{r},{j}]']
            if u['kind']=='battery':
                h=j//12; mode=upstream[r][h-24]['mode']
                require(z['mode']==mode, 'tail permission must use global_hour minus24')
                c['batteries'][r]=dict(mode=mode,power_mw=p,charge_mw=-p if mode=='charge' else 0.,discharge_mw=p if mode=='discharge' else 0.,
                    selected_hour_direction_permission={'charge':-1,'idle':0,'discharge':1}[mode],
                    energy_begin_mwh=values[f'physical_energy[{r},{j}]'],energy_end_mwh=values[f'physical_energy[{r},{j+1}]'])
                close(state['batteries'][r]['energy_mwh'],c['batteries'][r]['energy_begin_mwh'],'tail SOC discontinuity')
            else:
                # The exact pinned first21 tail rows are stable/off. Fail rather
                # than invent an event/notice for any later transition.
                require(z['phase'] in ('stable','off') and state['thermal'][r]['phase']==z['phase'], 'unsupported tail transition needs sourced event contract')
                c['thermal'][r]=dict(phase=z['phase'],mean_mw=p,begin_mw=state['thermal'][r]['power_mw'],end_mw=p,curve_step=None,curve_length=None,
                    stable=int(z['phase']=='stable'),connected=int(z['phase']!='off'),age_begin_h=state['thermal'][r]['state_age_h'])
        state=old.integrate_cell(state,c,base['technical'])
        for r,z in c['thermal'].items():z.update(age_end_h=state['thermal'][r]['state_age_h'],residence_remaining_h=state['thermal'][r]['residence_remaining_h'])
        for r,z in c['batteries'].items():
            close(state['batteries'][r]['energy_mwh'],z['energy_end_mwh'],'tail SOC integration')
            z.update(mode_age_end_h=state['batteries'][r]['mode_age_h'],hour_direction=state['batteries'][r]['hour_direction'],daily_used_internal_mwh=deepcopy(state['batteries'][r]['daily_used_internal_mwh']))
        cells.append(c)
    return cells,state


def _references(base,data,values,cells):
    result=[];lift=data['physical_lift']
    require(lift['market_to_physical'][96:]==[[363,364,365],[498,499,500]],'representatives must remain distinct from chronological tail')
    require(all(len(v)==96 for v in base['publication96']['power_mw'].values()),'publication96 must remain96')
    for q in range(103):
        ids=list(range(3*q,3*q+3)); rr={}
        for r,u in data['resources'].items():
            rows=[lift['resources'][r]['rows'][j] for j in ids]
            permission=cells[ids[0]]['batteries'][r]['selected_hour_direction_permission'] if u['kind']=='battery' else None
            e=quarter_eligibility(rows,u['kind'],permission)
            ref=math.fsum(values[f'physical_power[{r},{j}]'] for j in ids)/3
            if q<96:
                close(ref,base['publication96']['power_mw'][r][q],'publication/physical mean mismatch')
                close(ref,values[f'power[{r},{q}]'],'market/physical mean mismatch')
                market=u['rows'][q]
                require(market['price_eligible']==e['price_eligible'],'original eligibility mismatch')
                for key in ('min_mw','max_mw'): close(market[key],e[key],'original quarter capacity mismatch')
            rr[r]=dict(**e,reference_mw=ref,node=u['node'],physical_rows_sha256=digest(rows),
                source_market_index=q if q<96 else None,
                published_DA_award=base['publication96']['power_mw'][r][q] if q<96 else None,
                reference_kind='original_publication96_research_DA_award' if q<96 else TAIL_KIND)
        result.append(dict(chronological_q=q,start=cells[ids[0]]['start'],end=cells[ids[-1]]['end'],physical_indices=ids,resources=rr))
    return result


def load_contract(core_root, *, preparation_root, policy):
    require(policy==POLICY,'explicit exact physical-derived research policy required; official strict mode has no tail awards')
    base,oldpermit=old.load_accepted_r14(core_root,preparation_root=preparation_root)
    original,data=old.bridge_sources(base,oldpermit);root=Path(core_root)
    def named(folder):
        names=old.read_json(root/folder/'names.json')['variables']; primal=old.read_json(root/folder/'response.json')['primal']
        require(len(names)==len(primal)==len(set(names)),'source named primal coverage')
        return dict(zip(names,primal))
    values=named('ieee39_native_v4b/reports/sced');upstream=verify_upstream_modes(data,named('bess_native_v4b/reports/native'))
    cells,end=_extend(base,data,values,upstream)
    for r,target in TARGETS.items():
        u=data['resources'][r]
        require(u['terminal_interpretation']=='D96_only_research' and u['terminal_targets']==[dict(index=95,energy_mwh=target)],'original D96 research target changed')
        close(cells[287]['batteries'][r]['energy_end_mwh'],target,'original D96 source boundary')
    c=dict(schema=SCHEMA,policy=deepcopy(POLICY),source_bindings=deepcopy(base['source_bindings']),preparation_source_bindings=deepcopy(base['preparation_source_bindings']),
        day_start=base['day_start'],publication_at=base['publication_at'],source_available_at=base['source_available_at'],publication_clock_is_simulated=True,
        availability_is_simulated=True,future_RT_used=False,publication96=deepcopy(base['publication96']),
        representatives=[dict(market_index=q,physical_indices=deepcopy(data['physical_lift']['market_to_physical'][q]),grid=deepcopy(data['grid'][q])) for q in (96,97)],
        physical_cells=cells,references=_references(base,data,values,cells),upstream_mode_evidence=upstream,
        midnight_targets=deepcopy(TARGETS),initial_preparation=deepcopy(base['initial_preparation']),technical=deepcopy(base['technical']),
        archived_DA_terminal_state=end,coverage_end=cells[-1]['end'],events=deepcopy(base['events']),
        scopes=deepcopy(old.SCOPES),new_native_calls=0,native_release_available=False,
        meaning='pure inputs and matrices only; archived DA states are templates, not accepted RT/PRICE or future receipts')
    permit=ContractPermit();_CONTRACTS[permit]=dict(sha=digest(c),base=base,oldpermit=oldpermit,source=original,data=data,contract=deepcopy(c))
    validate_contract(c,permit)
    return c,permit


def validate_contract(c,permit,*,official_strict=False,recheck_files=False):
    require(type(permit) is ContractPermit and permit in _CONTRACTS,'R17 pure data permit required')
    e=_CONTRACTS[permit]
    require(digest(c)==e['sha'],'source-bound contract mutated')
    old.validate_bridge(e['base'],e['oldpermit'],recheck_files=recheck_files)
    require(not official_strict,'official strict mode: seven chronological tail DA awards and formal qualifications missing')
    require(len(c['physical_cells'])==309 and len(c['references'])==103,'complete309/103 coverage required')
    return True


def window_spec(c,index):
    require(type(index) is int and 0<=index<96,'only D-day96 pure window templates')
    day=stamp(c['day_start']);start=day+timedelta(minutes=15*index)
    return dict(index=index,start=start.isoformat(),cutoff=(start-timedelta(minutes=15)).isoformat(),delivery_end=(start+timedelta(minutes=15)).isoformat(),
        horizon_end=(start+timedelta(hours=2)).isoformat(),physical_cells=24,issued_cells=3,
        midnight_local_energy_index=288-3*index if 3*index<288<=3*index+24 else None)


def project_forward_initial(receipt,commands,*,cutoff,start,technical):
    """Causal arithmetic check only. Does not authenticate native receipts."""
    require(receipt.get('origin')=='accepted_research_RT_execution_receipt' and receipt.get('native_lineage_sha256'), 'DA/PRICE/template cannot be an execution receipt')
    require(all(c.get('origin')=='accepted_research_RT_issued_command' and c.get('native_lineage_sha256') for c in commands),'only previously issued RT commands may project')
    p=project_issued_prefix(receipt,commands,cutoff=cutoff,start=start,technical=technical)
    p.update(native_acceptance=False,execution_authority=False,forward_evidence_still_requires_independent_native_authentication=True)
    return p


def template_window(c,permit,index):
    """Models possible shapes, NOT later RT states or model feasibility."""
    validate_contract(c,permit);e=_CONTRACTS[permit];data=e['data'];spec=window_spec(c,index);first=3*index
    state=deepcopy(c['initial_preparation']['midnight_state'])
    for cell in c['physical_cells'][:first]:state=old.integrate_cell(state,cell,c['technical'])
    if index==0:
        prep=c['initial_preparation'];seed=prep['seed_state']
        receipt=dict(at=seed['at'],available_at=seed['at'],state=seed,is_measurement=False)
        commands=[dict(id='PREPARATION_TEMPLATE_'+str(j),issued_at=seed['at'],available_at=seed['at'],cell=x) for j,x in enumerate(prep['cells'])]
        projection=project_issued_prefix(receipt,commands,cutoff=spec['cutoff'],start=spec['start'],technical=c['technical'])
    else:
        projection=dict(state=state,start=spec['start'],cutoff=spec['cutoff'],native_acceptance=False,
            origin='archived_DA_mathematical_template_not_receipt',future_actual_used=False,is_measurement=False)
    families,vintages=_forecast(e['source'],spec)
    for v in vintages.values():v.update(coverage_start=spec['start'],coverage_end=spec['horizon_end'])
    cells=deepcopy(c['physical_cells'][first:first+24]);refs=deepcopy(c['references'][index:index+8])
    require(len(cells)==24 and len(refs)==8,'missing chronological physical/reference tail')
    resources={}
    for r,u in data['resources'].items():
        resources[r]={k:deepcopy(v) for k,v in u.items() if k!='rows'}
        resources[r]['rows']=deepcopy(data['physical_lift']['resources'][r]['rows'][first:first+24])
        if u['kind']=='battery':
            resources[r].update(terminal_energy_mwh=cells[-1]['batteries'][r]['energy_end_mwh'],cycle_budget=deepcopy(data['physical_lift']['resources'][r]['cycle_budget']))
    inp=dict(schema=SCHEMA+'.window',contract_sha256=digest(c),window=spec,day_start=c['day_start'],publication_at=c['publication_at'],
        policy=deepcopy(POLICY),scopes=deepcopy(old.SCOPES),projection=projection,da_cells=cells,references=refs,
        technical=deepcopy(c['technical']),vintages=vintages,nodes=deepcopy(data['nodes']),resources=resources,
        reserve=deepcopy(data['physical_lift']['reserve'][first:first+24]),load_mw=families['load_mw'],
        fixed_injection_mw={n:[sum(families[f][n][j] for f in ('wind_mw','solar_mw','boundary_tie_mw')) for j in range(24)] for n in data['nodes']},
        network={l:dict(ptdf=deepcopy(z['ptdf']),lower_mw=deepcopy(data['physical_lift']['network'][l]['lower_mw'][first:first+24]),upper_mw=deepcopy(data['physical_lift']['network'][l]['upper_mw'][first:first+24])) for l,z in data['network'].items()},
        midnight_targets=deepcopy(c['midnight_targets']),construction_only=True,feasibility_established=False,
        future_RT_states_known=False,native_release_available=False)
    p=PureInputPermit();_INPUTS[p]=dict(sha=digest(inp),contract=c,permit=permit)
    validate_window(inp,p)
    return inp,p


def validate_window(inp,permit):
    require(type(permit) is PureInputPermit and permit in _INPUTS,'R17 pure input permit required')
    entry=_INPUTS[permit];validate_contract(entry['contract'],entry['permit'])
    require(digest(inp)==entry['sha'],'pure input mutated')
    require(inp['construction_only'] is True and inp['feasibility_established'] is False,'template is not feasibility or execution')
    require(inp['window']==window_spec(entry['contract'],inp['window']['index']),'window clock changed')
    for v in inp['vintages'].values():
        require(stamp(v['source_available_at'])<=stamp(v['issued_at'])<=stamp(inp['window']['cutoff']) and stamp(v['information_end'])<=stamp(inp['window']['cutoff']) and not v['uses_future_actual'],'forecast future availability')
        require(v['coverage_end']==inp['window']['horizon_end'],'forecast tail coverage')
    for r,u in inp['resources'].items():
        require(len(u['rows'])==24,'physical row shape')
        if u['kind']=='battery':
            state=inp['projection']['state']['batteries'][r];dates={c['start'][:10] for c in inp['da_cells']}
            require(all(d in u['cycle_budget'] for d in dates),'missing dated budget')
            require(all(finite(z)>=0 for z in state['daily_used_internal_mwh'].values()),'negative inherited usage')
            close(sum(state['daily_used_internal_mwh'].values()),state['cumulative_internal_mwh'],'date usage cannot be reset or overlap counted')
            for day in dates:
                require(state['daily_used_internal_mwh'].get(day,0)<=u['cycle_budget'][day]['limit_internal_mwh']+1e-6,'inherited usage exceeds source dated budget')
    return True


def derive_price_quarters(inp):
    result={}
    for r,u in inp['resources'].items():
        result[r]=[]
        for q,ref in enumerate(inp['references']):
            source=ref['resources'][r];rows=u['rows'][3*q:3*q+3];permission=source.get('selected_hour_direction_permission')
            eligibility=quarter_eligibility(rows,u['kind'],permission)
            require(source['physical_rows_sha256']==digest(rows),'reference physical rows changed')
            for k,v in eligibility.items(): require(source[k]==v,'quarter source eligibility mismatch')
            value=finite(source['reference_mw']);lo,hi=eligibility['min_mw'],eligibility['max_mw']
            lower,upper=(max(lo,value-.1*abs(value)),min(hi,value+.1*abs(value))) if source['price_eligible'] else (value,value)
            require(lower<=upper+1e-9,'empty DA reference band; never widen')
            result[r].append(dict(da_reference_mw=value,lower_mw=lower,upper_mw=upper,physical_min_mw=lo,physical_max_mw=hi,
                price_eligible=source['price_eligible'],mode=source.get('mode'),active_mask=source['active_mask'],
                reserve_caps_mw={s:math.fsum(z[s+'_cap_mw'] for z in rows)/3 for s in _SERVICES}))
    return result
