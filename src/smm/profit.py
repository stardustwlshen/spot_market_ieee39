"""Synthetic cash operating profits, independent of offers and operator penalties."""
from __future__ import annotations
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

REQUIRED = {
    'coal': ('scalar_marginal_cost_yuan_per_mwh','fixed_online_cost_yuan_per_h','fixed_startup_cost_yuan_per_start'),
    'gas_ccgt': ('scalar_marginal_cost_yuan_per_mwh','fixed_online_cost_yuan_per_h','fixed_startup_cost_yuan_per_start'),
    'gas_gt': ('scalar_marginal_cost_yuan_per_mwh','fixed_online_cost_yuan_per_h','fixed_startup_cost_yuan_per_start'),
    'wind': ('scalar_marginal_cost_yuan_per_mwh',), 'pv': ('scalar_marginal_cost_yuan_per_mwh',),
    'hydro': ('scalar_marginal_cost_yuan_per_mwh',),
    'storage': ('discharge_degradation_yuan_per_mwh',),
    'pumped': ('generation_vom_yuan_per_mwh','gen_start_yuan_per_unit_start','pump_start_yuan_per_unit_start'),
}
COST_COLUMNS=('variable_cost_yuan','fixed_online_cost_yuan','startup_cost_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan')
REVENUE_COLUMNS=('da_cash_revenue_yuan','rt_deviation_cash_revenue_yuan','cash_revenue_yuan','charging_settlement_yuan')


def load_profile(root):
    path=Path(root)/'config'/'fixed_costs.yaml'
    if not path.exists():
        profile=dict(available=False,missing_reason='explicit fixed_costs.yaml absent; no bid/worksheet fallback',units={},sha256=None)
    else:
        content=path.read_bytes();profile=yaml.safe_load(content)
        if not isinstance(profile,dict) or not isinstance(profile.get('units'),dict):
            raise ValueError('fixed_costs.yaml must contain explicit units mapping')
        profile=dict(profile,available=True,sha256=hashlib.sha256(content).hexdigest(),input_file='config/fixed_costs.yaml')
    from .research_cost_setup import merge_fixed_profile
    return merge_fixed_profile(root,profile)


def _parameters(profile,unit,typ):
    d=profile.get('units',{}).get(unit)
    if not profile.get('available',False) or not isinstance(d,dict):return None,'explicit unit cost profile missing'
    if d.get('type')!=typ:return None,'cost profile resource type mismatch'
    for key in REQUIRED[typ]:
        value=d.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value) or value<0:
            return None,f'missing/invalid fixed cost parameter: {key}'
    return d,''


def validate_profile(profile, cfg=None):
    """Validate explicitly present records; omitted units remain unavailable, never zero."""
    if not isinstance(profile,dict) or not isinstance(profile.get('units'),dict):
        raise ValueError('fixed costs must contain units mapping')
    if not isinstance(profile.get('source'),str) or not profile['source'].strip():
        raise ValueError('fixed costs require explicit source/assumption statement')
    known = None if cfg is None else {u['id'] for family in ('thermal','renewable','hydro','storage','pumped') for u in cfg.units.get(family,[])}
    for uid,d in profile['units'].items():
        if known is not None and uid not in known:raise ValueError(f'{uid}: unknown/disabled unit')
        typ=d.get('type') if isinstance(d,dict) else None
        if typ not in REQUIRED:raise ValueError(f'{uid}: unsupported fixed cost type')
        params,reason=_parameters(dict(profile,available=True),uid,typ)
        if params is None:raise ValueError(f'{uid}: {reason}')
    return profile


def load_fixed_cost_profile(cfg):
    """Shared read-only validated profile for offers/UI; absence is explicit, never fallback."""
    profile=load_profile(cfg.root)
    if profile['available']:validate_profile(profile)
    return profile


def analyze(res, *, policy=None):
    """Only formal executed first-period quantities and signed two-settlement cash."""
    da,rr=res['da'],res['rr'];cfg=da.case.cfg;dt=cfg.dt
    from .settlement import validate_execution_order
    validate_execution_order(rr.rt, expected_periods=getattr(cfg, 'T', None), states=rr.states,
                             allow_legacy=False)
    from . import spot_costs
    policy=spot_costs.BASIS if policy is None else policy
    if policy not in (spot_costs.BASIS,spot_costs.LEGACY):raise ValueError('Unknown profit basis')
    spot=policy==spot_costs.BASIS
    profile=spot_costs.load_profile(cfg) if spot else load_fixed_cost_profile(cfg)
    detail=res['settle']['detail'];rows=[]
    specs=[]
    for family in ('thermal','renewable','hydro','storage','pumped'):
        for spec in cfg.units.get(family,[]):
            specs.append((spec['id'],spec['type'] if family in ('thermal','renewable') else family))
    periods=[int(r['t']) for r in rr.rt]
    if len(set(periods))!=len(periods) or len(rr.states)<len(rr.rt):
        raise ValueError('profit accounting requires distinct executed periods and all initial states')
    required_columns={'entity','period','da_amount','rt_amount','total'}
    if not required_columns.issubset(detail.columns):raise ValueError('settlement cash evidence columns missing')
    if detail.duplicated(['entity','period']).any():raise ValueError('duplicate settlement entity/period')
    for key in ('period','da_amount','rt_amount','total'):
        values=pd.to_numeric(detail[key],errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(values).all():raise ValueError(f'non-finite settlement {key}')
        if key=='period' and not np.equal(values,np.floor(values)).all():raise ValueError('noninteger settlement period')
    if not set(detail.period).issubset(set(periods)):raise ValueError('settlement outside executed period scope')
    names_by_unit={uid:([uid] if typ not in ('storage','pumped') else
        ([uid+'_放电',uid+'_充电'] if typ=='storage' else [uid+'_发电',uid+'_抽水'])) for uid,typ in specs}
    resource_entities={name for names in names_by_unit.values() for name in names}
    expected={(entity,t) for entity in resource_entities for t in periods}
    actual=set(zip(detail.loc[detail.entity.isin(resource_entities),'entity'],detail.loc[detail.entity.isin(resource_entities),'period']))
    if actual!=expected:raise ValueError('incomplete resource settlement entity/period grid')
    other_entities={'平衡松弛(过剩)'} | {f'L{n}' for n in getattr(da.case,'load_bus',{})} | set(getattr(da.case,'tie',{}))
    if not set(detail.entity).issubset(resource_entities | other_entities):raise ValueError('unknown settlement entity')
    if not np.allclose(detail.total.to_numpy(dtype=float),detail.da_amount.to_numpy(dtype=float)+detail.rt_amount.to_numpy(dtype=float),atol=1e-6,rtol=1e-10):
        raise ValueError('settlement total disagrees with DA plus RT cash')
    economic_columns = {'q_da', 'q_rt', 'lmp_da', 'lmp_rt'}
    has_economic_evidence = bool(economic_columns & set(detail.columns))
    if has_economic_evidence:
        if not economic_columns <= set(detail.columns):
            raise ValueError('incomplete settlement quantity/price evidence')
        for name in economic_columns:
            if not np.isfinite(pd.to_numeric(detail[name], errors='coerce').to_numpy(dtype=float)).all():
                raise ValueError('non-finite settlement quantity/price evidence')
        expected_da = detail.q_da * detail.lmp_da * dt
        expected_rt = (detail.q_rt-detail.q_da) * detail.lmp_rt * dt
        if (not np.allclose(detail.da_amount, expected_da, atol=1e-6, rtol=1e-10)
                or not np.allclose(detail.rt_amount, expected_rt, atol=1e-6, rtol=1e-10)):
            raise ValueError('settlement cash disagrees with quantity × price × dt')
    for uid,typ in specs:
        params,reason=(spot_costs.parameters(profile,uid,typ) if spot else _parameters(profile,uid,typ))
        names=[uid] if typ not in ('storage','pumped') else ([uid+'_放电',uid+'_充电'] if typ=='storage' else [uid+'_发电',uid+'_抽水'])
        charge=uid+('_充电' if typ=='storage' else '_抽水')
        for k,r in enumerate(rr.rt):
            t=int(r['t']);sn=r['snap'];state=rr.states[k]
            cash=detail[(detail.entity.isin(names)) & (detail.period==t)]
            if cash.empty:raise ValueError(f'missing formal settlement evidence for {uid} period {t}')
            if has_economic_evidence:
                if typ == 'storage':
                    powers = {uid+'_放电': sn['Pd'][uid][0], uid+'_充电': -sn['Pc'][uid][0]}
                elif typ == 'pumped':
                    powers = {uid+'_发电': sn['Pg'][uid][0], uid+'_抽水': -sn['Pp'][uid][0]}
                else:
                    key = 'P' if typ in ('coal','gas_ccgt','gas_gt') else 'Pw' if typ in ('wind','pv') else 'Ph'
                    powers = {uid: sn[key][uid][0]}
                recorded = cash.set_index('entity').q_rt
                if any(not np.isfinite(float(power)) or not np.isclose(recorded[entity], power, atol=1e-6, rtol=1e-10)
                       for entity, power in powers.items()):
                    raise ValueError(f'{uid}: settlement quantity disagrees with executed RT first period')
            p=0.;on=0.;starts=0.;pumpstarts=0.;stops=0.;pumpstops=0.;start=end=None;stockunit=None
            if typ in ('coal','gas_ccgt','gas_gt'):
                p=float(sn['P'][uid][0]);on=float(sn['u'][uid][0])
                prior_online=state.thermal[uid]['online']
                if not np.isfinite(float(prior_online)):
                    raise ValueError(f'non-finite actual initial online state for {uid} period {t}')
                starts=int(on>0 and not prior_online)
                stops=int(on==0 and prior_online)
            elif typ in ('wind','pv'):p=float(sn['Pw'][uid][0])
            elif typ=='hydro':
                p=float(sn['Ph'][uid][0]);start=float(state.hydro[uid]);end=float(sn['Vh'][uid][0]);stockunit='1e4m3'
            elif typ=='storage':
                p=float(sn['Pd'][uid][0]);start=float(state.storage[uid]);end=float(sn['E'][uid][0]);stockunit='MWh'
            else:
                p=float(sn['Pg'][uid][0]);start=float(state.pumped[uid]['V']);end=float(sn['Vu'][uid][0]);stockunit='1e4m3'
                counts=[sn['ngen'][uid][0],sn['npump'][uid][0],state.pumped[uid]['ng'],state.pumped[uid]['np']]
                if not np.isfinite(np.asarray(counts,dtype=float)).all():
                    raise ValueError(f'non-finite actual pumped mode count for {uid} period {t}')
                starts=max(float(sn['ngen'][uid][0])-state.pumped[uid]['ng'],0.)
                pumpstarts=max(float(sn['npump'][uid][0])-state.pumped[uid]['np'],0.)
                stops=max(state.pumped[uid]['ng']-float(sn['ngen'][uid][0]),0.)
                pumpstops=max(state.pumped[uid]['np']-float(sn['npump'][uid][0]),0.)
            actual=[p,on,starts,pumpstarts]+([start,end] if stockunit is not None else [])
            if not np.isfinite(np.asarray(actual,dtype=float)).all():
                raise ValueError(f'non-finite actual power/state/inventory for {uid} period {t}')
            revenue=float(cash.total.sum())
            row=dict(unit=uid,type=typ,period=t,energy_generated_or_discharged_mwh=p*dt,
                profit_basis=policy,cost_basis_version=spot_costs.VERSION if spot else 'legacy-fixed-costs-v1',
                cost_source_kind=(params.get('source_kind','explicit_assumption_status_not_supplied') if spot and params is not None else profile.get('unit_sources',{}).get(uid,{}).get('source_kind',profile.get('source_kind','explicit_assumption_status_not_supplied'))),
                da_cash_revenue_yuan=float(cash.da_amount.sum()),rt_deviation_cash_revenue_yuan=float(cash.rt_amount.sum()),
                cash_revenue_yuan=revenue,charging_settlement_yuan=float(cash.loc[cash.entity==charge,'total'].sum()) if typ in ('storage','pumped') else 0.,
                cost_available=params is not None,cost_missing_reason=reason,
                startup_count=starts,pump_startup_count=pumpstarts,shutdown_count=stops,pump_shutdown_count=pumpstops,
                inventory_start=start,inventory_end=end,inventory_change=None if start is None else end-start,inventory_unit=stockunit)
            if params is None:
                row.update({key:None for key in (*COST_COLUMNS,'shutdown_cost_yuan')});row['shutdown_cost_yuan']=None
            else:
                rate=0. if spot else params.get('scalar_marginal_cost_yuan_per_mwh',params.get('discharge_degradation_yuan_per_mwh',params.get('generation_vom_yuan_per_mwh')))
                variable=rate*p*dt
                online=params.get('fixed_online_cost_yuan_per_h',0.)*on*dt
                startup=params.get('fixed_startup_cost_yuan_per_start',params.get('gen_start_yuan_per_unit_start',0.))*starts+params.get('pump_start_yuan_per_unit_start',0.)*pumpstarts
                shutdown=0.
                if spot:
                    variable,online,startup,shutdown=spot_costs.period_cost(params,p*dt,on,starts,pumpstarts,stops,pumpstops,dt)
                row['shutdown_cost_yuan']=shutdown
                total=variable+online+startup+shutdown
                row.update(variable_cost_yuan=variable,fixed_online_cost_yuan=online,startup_cost_yuan=startup,cash_operating_cost_yuan=total,simulated_operating_profit_yuan=revenue-total)
            rows.append(row)
    period=pd.DataFrame(rows);units=[]
    for (uid,typ),group in period.groupby(['unit','type'],sort=False):
        valid=bool(group.cost_available.all());g=group.sort_values('period')
        row=dict(unit=uid,type=typ,cost_available=valid,cost_missing_reason='; '.join(sorted(set(group.cost_missing_reason)-{''})))
        kinds=set(group.cost_source_kind)
        row['cost_source_kind']=next(iter(kinds)) if len(kinds)==1 else 'mixed_explicit_and_synthetic_costs'
        row.update({key:float(group[key].sum()) for key in (*REVENUE_COLUMNS,'energy_generated_or_discharged_mwh','startup_count','pump_startup_count','shutdown_count','pump_shutdown_count')})
        row.update({key:float(group[key].sum()) if valid else None for key in (*COST_COLUMNS,'shutdown_cost_yuan')})
        start,end=g.iloc[0].inventory_start,g.iloc[-1].inventory_end
        row.update(inventory_start=start,inventory_end=end,inventory_change=None if pd.isna(start) else end-start,inventory_unit=g.iloc[0].inventory_unit)
        units.append(row)
    by_unit=pd.DataFrame(units);types=[]
    for typ,g in by_unit.groupby('type',sort=False):
        valid=bool(g.cost_available.all())
        types.append(dict(type=typ,unit_count=len(g),cost_available=valid,
            cost_source_kind=g.cost_source_kind.iloc[0] if g.cost_source_kind.nunique()==1 else 'mixed_explicit_and_synthetic_costs',
            **{key:float(g[key].sum()) for key in REVENUE_COLUMNS},
            **{key:float(g[key].sum()) if valid else None for key in (*COST_COLUMNS,'shutdown_cost_yuan')}))
    complete=bool(len(by_unit) and by_unit.cost_available.all())
    summary=dict(profit_basis=policy,cost_basis_version=spot_costs.VERSION if spot else 'legacy-fixed-costs-v1',label='simulated_cash_operating_profit_not_total_project_profit',cost_profile_sha256=profile['sha256'],
        cost_profile_available=profile['available'],all_unit_costs_available=complete,unit_count=len(by_unit),
        cash_revenue_yuan=float(by_unit.cash_revenue_yuan.sum()),
        cash_operating_cost_yuan=float(by_unit.cash_operating_cost_yuan.sum()) if complete else None,
        simulated_operating_profit_yuan=float(by_unit.simulated_operating_profit_yuan.sum()) if complete else None,
        revenue_scope='DA + RT deviation signed net energy settlement; charging already included',
        exclusions=spot_costs.EXCLUSIONS if spot else 'CAPEX,financing,tax,capacity,AGC,compensation,operator penalties,water opportunity cost,inventory valuation',
        scope='actual executed RT periods only; see completed_scope for full-day status; ending inventories separately disclosed')
    by_type=pd.DataFrame(types)
    for frame in (period,by_unit,by_type):
        frame['cost_profile_sha256']=profile['sha256']
        frame['profit_basis']=policy
        frame['cost_basis_version']=summary['cost_basis_version']
    return dict(profile=profile,summary=summary,by_period=period,by_unit=by_unit,by_type=by_type)
