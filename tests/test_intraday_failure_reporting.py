"""Failed ID attempts must export separately and certify actual DA references."""
from copy import deepcopy
from types import SimpleNamespace as NS
import json
import numpy as np
import pandas as pd
import pytest
import yaml
from smm import full
from test_commitment_stage_audit import stage_evidence
from test_soft_terminal_reporting import soft_archive
from test_validate_runs_evidence import archive,validator


def evidence():
    cfg=NS(T=4,mode={'intraday':{'enabled':True,'interval_h':1.,'lock_types':[]},
                    'realtime':{'hydro':'sced','storage':{'mode':'value'}}},
           units={'storage':[{'id':'S'}],'pumped':[],'hydro':[]})
    failure=dict(tau=0,phase='scuc',solver_status='HighsModelStatus.kInfeasible',reason='proven infeasible',fallback='day_ahead')
    plan={'u':{'G':np.ones(4)},'E':{'S':np.full(4,100.)},'Vu':{},'Vh':{},'delta':{},
          'state_values':{'E':{'S':{'value_native':np.array([1.,2.,3.,0.])}}}}
    r=dict(t=0,handoff_config={'intraday_attempted':True},
           reference_metadata={'E':{'S':{'trajectory_source':'day_ahead','value_source':'day_ahead','fallback_reason':'ID failed'}},
                               'u':{'G':{'source':'day_ahead','fallback_reason':'ID failed'}}},
           thermal_reference_u={'G':np.ones(2)},hydro_reference_delta={},
           snap={'value_window':[dict(kind='E',unit='S',period=1,reference_state=100.,value_native=2.)]})
    return cfg,NS(id_failures=[failure],id_rounds=[],rt=[r]),plan


def test_first_failure_is_not_success_and_clears_old_reference():
    cfg,rr,plan=evidence()
    assert all(full.intraday_failure_checks(cfg,rr,plan).values())
    summary=full.intraday_run_summary(cfg,rr)
    assert summary['rounds']==0 and summary['failure_count']==summary['attempted_rounds']==1
    rr.id_rounds=[{'tau':2}];r=deepcopy(rr.rt[0]);r['t']=2;r['reference_metadata']['E']['S']['trajectory_source']='intraday@2'
    r['snap']['value_window'][0].update(reference_state=123.,value_native=999.)
    rr.rt.append(r)
    assert all(full.intraday_failure_checks(cfg,rr,plan).values())  # later success restores new ID


@pytest.mark.parametrize('damage',['value','trajectory','thermal','reason','source','attempted','phase','status','duplicate','success_overlap'])
def test_failed_id_cannot_relabel_stale_values_as_day_ahead(damage):
    cfg,rr,plan=evidence();r=rr.rt[0];f=rr.id_failures[0]
    if damage=='value':r['snap']['value_window'][0]['value_native']=999.
    elif damage=='trajectory':r['snap']['value_window'][0]['reference_state']=999.
    elif damage=='thermal':r['thermal_reference_u']['G'][0]=0
    elif damage=='reason':r['reference_metadata']['E']['S']['fallback_reason']=None
    elif damage=='source':r['reference_metadata']['E']['S']['trajectory_source']='intraday@0'
    elif damage=='attempted':r['handoff_config']['intraday_attempted']=False
    elif damage=='phase':f['phase']='auxiliary_pricing'
    elif damage=='status':f['solver_status']='time_limit'
    elif damage=='duplicate':rr.id_failures.append(deepcopy(f))
    else:rr.id_rounds=[{'tau':0}]
    assert not all(full.intraday_failure_checks(cfg,rr,plan).values())


def test_failure_csv_has_headers_when_none_and_all_failed(tmp_path):
    cfg,rr,_=evidence();full.write_intraday_failures(rr,tmp_path)
    a=pd.read_csv(tmp_path/'id_failed_rounds.csv');assert len(a)==1 and a.iloc[0].fallback=='day_ahead'
    rr.id_failures=[];full.write_intraday_failures(rr,tmp_path)
    a=pd.read_csv(tmp_path/'id_failed_rounds.csv');assert a.empty and 'solver_status' in a


def test_first_failed_attempt_still_checks_gt_da_lock(stage_evidence):
    da,rr,rt=stage_evidence;rr.id_rounds=[]
    r=rr.rt[0];r['handoff_config']['intraday_attempted']=True
    r['thermal_reference_u']['T'][:]=1.;r['snap']['u']['T'][:]=1.
    r['reference_metadata']['u']['T']={'source':'day_ahead','round':None}
    rt['units'].loc[rt['units'].unit=='T','on']=1
    assert full.checks(da,rr,rt)['thermal_reference_evidence_complete']
    del r['thermal_reference_u']['T'];del r['reference_metadata']['u']['T']
    assert not full.checks(da,rr,rt)['thermal_reference_evidence_complete']


@pytest.fixture
def failure_archive(soft_archive):
    p=soft_archive/'scenario_snapshot/config/modes.yaml';m=yaml.safe_load(p.read_text())
    m['MKT']['intraday']={'enabled':True,'interval_h':.25};p.write_text(yaml.safe_dump(m))
    f=dict(tau=0,phase='scuc',solver_status='HighsModelStatus.kInfeasible',reason='proven infeasible',fallback='day_ahead')
    p=soft_archive/'summary.json';s=json.loads(p.read_text());s['intraday']={'rounds':1,'failure_count':1,'attempted_rounds':2,'failed_rounds':[f]}
    s['checks'].update(intraday_failure_records_valid=True,intraday_failed_reference_evidence_complete=True)
    p.write_text(json.dumps(s));pd.DataFrame([f]).to_csv(soft_archive/'id_failed_rounds.csv',index=False)
    pd.DataFrame([{'tau':1}]).to_csv(soft_archive/'id_rounds_summary.csv',index=False)
    da={'E':{'S':[100.,100.]},'Vu':{'P':[50.,50.]},'Vh':{'H':[50.,50.]},'u':{'G':[1.,1.]},'delta':[]}
    metadata={k:{u:{'trajectory_source':'day_ahead','value_source':'day_ahead','fallback_reason':'ID failed'}} for k,u in [('E','S'),('Vu','P'),('V','H')]}
    metadata['u']={'G':{'source':'day_ahead','fallback_reason':'ID failed'}}
    rows=[]
    for t in range(2):
        rows.append(dict(t=t,snapshot={},handoff_config={'intraday_attempted':True},reference_metadata=deepcopy(metadata),
            thermal_reference_u={'G':[1.]*(2-t)},hydro_reference_delta=[],
            value_window=[dict(kind=k,unit=u,period=1,reference_state=x,value_native=0.) for k,u,x in [('E','S',100.),('Vu','P',50.),('V','H',50.)]]))
    (soft_archive/'hydro_window_evidence.json').write_text(json.dumps({'da':da,'rt_windows':rows}))
    return soft_archive


def test_failure_archive_verifies_da_values_not_only_saved_green_flag(failure_archive):
    a=validator.checks(failure_archive);assert a['验收通过'] and a['日内失败回退合规'],a['说明']
    p=failure_archive/'hydro_window_evidence.json';e=json.loads(p.read_text());e['rt_windows'][0]['value_window'][0]['reference_state']=999.
    p.write_text(json.dumps(e));a=validator.checks(failure_archive)
    assert not a['验收通过'] and not a['日内失败回退合规']


@pytest.mark.parametrize('damage',['missing_file','success_overlap','wrong_summary','invalid_phase','missing_window','stale_gt'])
def test_failure_archive_fail_closed(failure_archive,damage):
    if damage=='missing_file':(failure_archive/'id_failed_rounds.csv').unlink()
    elif damage=='success_overlap':pd.DataFrame([{'tau':0}]).to_csv(failure_archive/'id_rounds_summary.csv',index=False)
    elif damage=='wrong_summary':
        p=failure_archive/'summary.json';s=json.loads(p.read_text());s['intraday']['rounds']=2;p.write_text(json.dumps(s))
    elif damage=='invalid_phase':
        p=failure_archive/'id_failed_rounds.csv';a=pd.read_csv(p);a['phase']='auxiliary_pricing';a.to_csv(p,index=False)
    else:
        p=failure_archive/'hydro_window_evidence.json';e=json.loads(p.read_text())
        if damage=='missing_window':e['rt_windows'].pop()
        else:e['rt_windows'][0]['thermal_reference_u']['G'][0]=0.
        p.write_text(json.dumps(e))
    assert not validator.checks(failure_archive)['验收通过']


# Exercise the real reference selection and fixed-commitment constructor. Only
# optimization is stubbed; these tests must not invent already-correct metadata.
from smm import rolling
from test_handoff_integrity import context, install_window_probe, da_case_probe


@pytest.mark.parametrize('policy',[
    'default','source_day_ahead','storage_disabled','pumped_disabled','hydro_disabled',
    'commitment_disabled','commitment_free','hydro_fixed_da','storage_anchor','id_free_ccgt_rt_locked',
])
def test_real_first_failure_reference_scope(context,monkeypatch,policy):
    cfg,plan=context
    cfg.mode['intraday']['enabled']=True
    rc=cfg.mode['realtime']
    if policy=='source_day_ahead':rc['handoff_source']='day_ahead'
    elif policy.endswith('_disabled'):rc['handoff'][policy.removesuffix('_disabled')]=False
    elif policy=='commitment_free':rc['intraday_commit_rt']='free'
    elif policy=='hydro_fixed_da':rc['hydro']='fixed_da';rc.pop('tracking_policy',None)
    elif policy=='storage_anchor':rc['storage'].update(mode='sced_anchor',anchor='hourly_eq')
    elif policy=='id_free_ccgt_rt_locked':
        cfg.mode['intraday']['lock_types']=['coal']
        cfg.mode['intraday']['free_types']=['gas_gt','gas_ccgt','hydro','storage']
    def clear(case,**kwargs):
        rows=[dict(kind=k,unit=u,period=t,relative_period=t,value_native=v)
              for k,u,t,v in case.terminal.get('state_value',[])]
        return dict(snap={'hydro_constraints':{},'value_window':rows},slack={})
    install_window_probe(monkeypatch,clear)
    ref=rolling.failed_intraday_reference(plan,0,'proven physical infeasibility')
    result=rolling.run_rt_period(da_case_probe(cfg),plan,ref,cfg,rolling.initial_state(cfg),0,ref=ref,id_active=True)
    result['t']=0
    rr=NS(id_rounds=[],id_failures=[dict(tau=0,phase='scuc',solver_status='infeasible',
          reason='proven physical infeasibility',fallback='day_ahead')],rt=[result])
    checked=full.intraday_failure_checks(cfg,rr,plan)
    assert all(checked.values()),(policy,checked,result['reference_metadata'])
    # Base locks always select the original DA, so None is not missing evidence.
    for u in cfg.units['thermal']:
        if u['type'] in rc['lock_types']:
            assert result['reference_metadata']['u'][u['id']]['fallback_reason'] is None
    for family,kind in [('storage','E'),('pumped','Vu'),('hydro','V')]:
        relevant=(rc['hydro']=='value' if kind=='V' else rc['storage']['mode']=='value')
        needs_reason=rc['handoff_source']=='intraday' and rc['handoff'][family] and relevant
        for meta in result['reference_metadata'][kind].values():
            assert bool(meta['fallback_reason'])==needs_reason
            if needs_reason:
                previous=meta['fallback_reason'];meta['fallback_reason']=None
                assert not full.intraday_failure_checks(cfg,rr,plan)['intraday_failed_reference_evidence_complete']
                meta['fallback_reason']=previous
    for u in cfg.units['thermal']:
        meta=result['reference_metadata']['u'].get(u['id'])
        if meta and u['type'] not in rc['lock_types']:
            previous=meta['fallback_reason'];assert previous
            meta['fallback_reason']=None
            assert not full.intraday_failure_checks(cfg,rr,plan)['intraday_failed_reference_evidence_complete']
            meta['fallback_reason']=previous
    # DA-only resources still require actual numeric DA references, not labels.
    result['thermal_reference_u'][next(iter(result['thermal_reference_u']))][0]=0
    assert not full.intraday_failure_checks(cfg,rr,plan)['intraday_failed_reference_evidence_complete']
