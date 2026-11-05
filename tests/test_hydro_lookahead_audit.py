"""Detached reference forecasts cannot certify themselves or become actual cost."""
from copy import deepcopy
from types import SimpleNamespace as NS
import json
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest
import yaml
from smm import full, rolling, scuc, handoff, value_outputs
from test_handoff_integrity import context
from test_soft_terminal_reporting import soft_archive
from test_validate_runs_evidence import validator, archive


def diagnostic(cfg,plan,metadata,t,state_value,hist=None):
    end=min(t+int(round(cfg.mode['realtime']['lookahead_h']*60/cfg.mode['realtime']['interval_min'])),cfg.T)
    inputs=rolling.hydro_terminal_lookahead_terms(cfg,plan,NS(hydro_hist=hist or {}),t,end,metadata)
    if inputs is None:return []
    m=pyo.ConcreteModel();m._case=NS(stage='RT',cfg=cfg,t0=t,terminal={'hydro_terminal_lookahead':inputs})
    m._md=NS(T=end-t,hydro={u['id']:u for u in cfg.units['hydro']})
    m._state_band_terminal_policy=cfg.mode['realtime']['value_method']['terminal_policy']
    m.V=pyo.Var(list(m._md.hydro),range(end-t),initialize=state_value)
    scuc._attach_hydro_terminal_lookahead(m)
    for h,row in inputs.items():m.hydro_terminal_lookahead.slack[h].value=max(row['minimum_window_end_volume']-state_value,0.)
    return scuc.hydro_terminal_lookahead_diagnostics(m)


@pytest.fixture
def evidence(context):
    cfg,da=context;cfg.base['periods']=12
    cfg.mode['realtime']['value_method']['hydro_lookahead']='reference_continuation'
    cfg.mode['intraday']['enabled']=False
    for group,arrays in da.items():
        if group=='state_values':
            for units in arrays.values():
                for values in units.values():
                    for key,value in list(values.items()):
                        if isinstance(value,np.ndarray):values[key]=value[:12]
        else:
            for key,value in arrays.items():arrays[key]=value[:12]
    h=cfg.units['hydro'][0]['id'];da['Qout'][h][:]=600.
    meta=handoff.selected_reference(cfg,da,None,False)[1]
    rt=[]
    for t in range(cfg.T):
        n=min(8,cfg.T-t)
        rt.append(dict(t=t,reference_metadata=deepcopy(meta),snap={'Vh':{h:np.full(n,17990.)},
            'Qout':{h:np.full(n,600.)},'hydro_terminal_lookahead':diagnostic(cfg,da,meta,t,17990.)}))
    return cfg,NS(rt=rt,id_rounds=[],id_failures=[]),da


def test_positive_forecast_cost_is_valid_but_not_actual_cost(evidence):
    cfg,rr,da=evidence;a=full.hydro_terminal_lookahead_checks(cfg,rr,da)
    assert a['hydro_terminal_lookahead_evidence_complete'] and a['hydro_terminal_lookahead_checked_records']==4
    assert a['hydro_terminal_lookahead_max_forecast_shortfall_1e4m3']>0
    assert a['hydro_terminal_lookahead_max_forecast_cost_yuan']>0
    assert not any('executed' in k or 'sum' in k or 'physical_feasible' in k for k in a)
    assert all(not r['snap']['hydro_terminal_lookahead'] for r in rr.rt[-8:])


@pytest.mark.parametrize('damage',['missing','duplicate','last_window_extra','state','dt','R','L','target','flow','periods',
 'projected','shortfall','factor','cost_dt','source','round','forecast','active','upstream_source','upstream_flow'])
def test_forecast_evidence_fails_closed(evidence,damage):
    cfg,rr,da=evidence;w=rr.rt[0]['snap']['hydro_terminal_lookahead'][0]
    if damage=='missing':rr.rt[0]['snap'].pop('hydro_terminal_lookahead')
    elif damage=='duplicate':rr.rt[0]['snap']['hydro_terminal_lookahead'].append(deepcopy(w))
    elif damage=='last_window_extra':rr.rt[-1]['snap']['hydro_terminal_lookahead']=[deepcopy(w)]
    elif damage=='state':w['state']+=1
    elif damage=='dt':w['dt_h']=1.
    elif damage=='R':w['reference_recovery_volume']+=1
    elif damage=='L':w['minimum_window_end_volume']+=1
    elif damage=='target':w['terminal_target']+=1
    elif damage=='flow':w['reference_outflows_m3s'][0]+=1
    elif damage=='periods':w['future_periods'][0]+=1
    elif damage=='projected':w['projected_terminal_volume']+=1
    elif damage=='shortfall':w['projected_terminal_shortfall']=0.
    elif damage=='factor':w['factor']*=.25
    elif damage=='cost_dt':w['penalty_cost']*=.25
    elif damage=='source':w['reference_source']='intraday'
    elif damage=='round':w['reference_round']=0
    elif damage=='forecast':w['forecast_only']=False
    elif damage=='active':w['active']=False
    elif damage=='upstream_source':w['upstream_arrival_sources'][0]='selected_reference'
    else:w['upstream_arrivals_m3s'][0]+=1
    assert not full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']


def test_id_flow_source_and_failed_round_da_reversion(evidence):
    cfg,rr,da=evidence;cfg.mode['intraday']['enabled']=True
    ref=deepcopy(da);ref['Qout']['H1'][:]=610.
    ref['reference_source']='intraday@0';ref['reference_metadata']=deepcopy(rr.rt[0]['reference_metadata'])
    ref['reference_metadata']['V']['H1'].update(trajectory_source='intraday@0',value_source='intraday@0',round=0)
    rr.id_rounds=[dict(tau=0,snap=ref)]
    for r in rr.rt:
        r['reference_metadata']=deepcopy(ref['reference_metadata'])
        r['snap']['hydro_terminal_lookahead']=diagnostic(cfg,ref,r['reference_metadata'],r['t'],17990.)
    assert full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']
    rr.id_failures=[dict(tau=1)]
    assert not full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']
    ref=rolling.failed_intraday_reference(da,1,'infeasible')
    for r in rr.rt[1:]:
        r['reference_metadata']=deepcopy(ref['reference_metadata'])
        r['snap']['hydro_terminal_lookahead']=diagnostic(cfg,ref,r['reference_metadata'],r['t'],17990.)
    assert full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']


def write_archive(path,cfg,rr,da):
    rows=value_outputs.hydro_terminal_lookahead_rows(rr)
    (path/'hydro_terminal_lookahead_evidence.json').write_text(json.dumps({'scope':
       'reference_continuation_forecast_not_executed_cost_or_feasibility_proof','rows':rows}))
    scalar=[{k:v for k,v in row.items() if not isinstance(v,(dict,list))} for row in rows]
    pd.DataFrame(scalar).to_csv(path/'rt_hydro_terminal_lookahead.csv',index=False,float_format='%.4f')
    def serial(x):return x.tolist() if hasattr(x,'tolist') else float(x)
    (path/'hydro_window_evidence.json').write_text(json.dumps({'da':{k:da[k] for k in ('Qout','Vh')},'id_rounds':[],
        'rt_windows':[dict(t=r['t'],snapshot=r['snap'],reference_metadata=r['reference_metadata']) for r in rr.rt]},default=serial))


def test_archive_crosschecks_scalar_csv_and_full_precision(tmp_path,evidence):
    cfg,rr,da=evidence;write_archive(tmp_path,cfg,rr,da)
    assert validator._hydro_lookahead_audit(tmp_path,cfg)['hydro_terminal_lookahead_evidence_complete']
    p=tmp_path/'rt_hydro_terminal_lookahead.csv';a=pd.read_csv(p);a['penalty_cost']*=.25;a.to_csv(p,index=False)
    with pytest.raises(ValueError,match='CSV'):validator._hydro_lookahead_audit(tmp_path,cfg)


@pytest.mark.parametrize('filename',['hydro_terminal_lookahead_evidence.json','rt_hydro_terminal_lookahead.csv','hydro_window_evidence.json'])
def test_archive_missing_sources_are_not_zero(tmp_path,evidence,filename):
    cfg,rr,da=evidence;write_archive(tmp_path,cfg,rr,da);(tmp_path/filename).unlink()
    with pytest.raises((OSError,ValueError)):validator._hydro_lookahead_audit(tmp_path,cfg)


def test_existing_validator_requires_new_evidence_for_nonterminal_soft_window(soft_archive):
    p=soft_archive/'scenario_snapshot/config/modes.yaml';m=yaml.safe_load(p.read_text())
    m['MKT']['realtime']['lookahead_h']=.25
    m['MKT']['realtime']['value_method']['hydro_lookahead']='reference_continuation';p.write_text(yaml.safe_dump(m))
    # The old fixture has no future-reference artifact; all legacy checks being
    # green cannot certify the newly required lookahead term.
    result=validator.checks(soft_archive)
    assert not result['验收通过'] and not result['水电参考续程证据合规']


@pytest.mark.parametrize('setting',[None,'off'])
def test_legacy_disabled_policy_does_not_require_or_silently_accept_forecasts(evidence,setting):
    cfg,rr,da=evidence
    if setting is None:cfg.mode['realtime']['value_method'].pop('hydro_lookahead')
    else:cfg.mode['realtime']['value_method']['hydro_lookahead']=setting
    assert not full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']
    for r in rr.rt:r['snap'].pop('hydro_terminal_lookahead')
    result=full.hydro_terminal_lookahead_checks(cfg,rr,da)
    assert result['hydro_terminal_lookahead_evidence_complete'] and not result['hydro_terminal_lookahead_checks_active']


def test_cascade_checks_known_actual_history_and_selected_future(evidence):
    cfg,rr,da=evidence
    child=deepcopy(cfg.units['hydro'][0]);child.update(id='H2',upstream='H1',lag_periods=10)
    cfg.units['hydro'].append(child);da['Qout']['H2']=np.full(cfg.T,90.);da['Vh']['H2']=np.full(cfg.T,18000.)
    for r in rr.rt:
        t=r['t'];n=min(8,cfg.T-t)
        r['reference_metadata']['V']['H2']=deepcopy(r['reference_metadata']['V']['H1'])
        r['snap']['Qout']['H1']=np.full(n,123.)
        r['snap']['Qout']['H2']=np.full(n,90.)
        r['snap']['Vh']['H2']=np.full(n,17990.)
        hist={'H1':np.r_[np.full(t,123.),np.full(cfg.T-t,999999.)]}
        r['snap']['hydro_terminal_lookahead']=diagnostic(cfg,da,r['reference_metadata'],t,17990.,hist)
    result=full.hydro_terminal_lookahead_checks(cfg,rr,da)
    assert result['hydro_terminal_lookahead_evidence_complete'],result['hydro_terminal_lookahead_errors']
    assert result['hydro_terminal_lookahead_checked_records']==8
    r=rr.rt[1]['snap']['hydro_terminal_lookahead'][1]
    assert r['upstream_arrival_sources']==['pre_day_initial_outflow','executed_history','selected_reference']
    upstream=cfg.units['hydro'][0]
    initial=upstream['water_rate']*upstream['init_units_on']*upstream['init_p_unit']
    assert r['upstream_arrivals_m3s']==[initial,123.,600.]
    rr.rt[0]['snap']['Qout']['H1'][0]=500.
    assert not full.hydro_terminal_lookahead_checks(cfg,rr,da)['hydro_terminal_lookahead_evidence_complete']


def test_archive_id_reference_requires_saved_successful_round(tmp_path,evidence):
    cfg,rr,da=evidence;cfg.mode['intraday']['enabled']=True
    ref=deepcopy(da);ref['Qout']['H1'][:]=610.
    for r in rr.rt:
        r['reference_metadata']['V']['H1'].update(trajectory_source='intraday@0',value_source='intraday@0',round=0)
        r['snap']['hydro_terminal_lookahead']=diagnostic(cfg,ref,r['reference_metadata'],r['t'],17990.)
    write_archive(tmp_path,cfg,rr,da)
    pd.DataFrame([{'tau':0}]).to_csv(tmp_path/'id_rounds_summary.csv',index=False)
    pd.DataFrame(columns=['tau']).to_csv(tmp_path/'id_failed_rounds.csv',index=False)
    p=tmp_path/'hydro_window_evidence.json';e=json.loads(p.read_text())
    with pytest.raises(ValueError,match='ID'):validator._hydro_lookahead_audit(tmp_path,cfg)
    e['id_rounds']=[dict(tau=0,snapshot={'Qout':{'H1':ref['Qout']['H1'].tolist()}})]
    p.write_text(json.dumps(e))
    assert validator._hydro_lookahead_audit(tmp_path,cfg)['hydro_terminal_lookahead_evidence_complete']
    e['id_rounds'][0]['snapshot']['Qout']['H1'][10]=999.
    p.write_text(json.dumps(e))
    with pytest.raises(ValueError,match='Qout'):validator._hydro_lookahead_audit(tmp_path,cfg)
