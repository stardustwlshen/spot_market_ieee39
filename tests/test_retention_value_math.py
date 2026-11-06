"""Independent nonzero dual arithmetic and capture/release probes. No optimizer is run."""
import copy,gc,inspect,json,weakref
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc,rolling,result_retention as keep
from smm.hydro_daily_energy import id_state_values
from smm.state_bridge import digest
from test_handoff_integrity import context,id_round


def same(a,b):
    if isinstance(a,np.ndarray):
        assert a.dtype==b.dtype
        np.testing.assert_array_equal(a,b)
    elif isinstance(a,dict):
        assert a.keys()==b.keys()
        for k in a:same(a[k],b[k])
    elif isinstance(a,(tuple,list)):
        assert type(a)==type(b) and len(a)==len(b)
        for x,y in zip(a,b):same(x,y)
    else:assert a==b


def probe():
    cfg,plan=context.__wrapped__();tau=4;n=cfg.T-tau
    r,_=id_round(plan,tau);r['snap']['surplus']=np.zeros(n)
    m=pyo.ConcreteModel();m.K=pyo.RangeSet(0,n-1)
    m.S=pyo.Set(initialize=list(plan['E']));m.PS=pyo.Set(initialize=list(plan['Vu']));m.HS=pyo.Set(initialize=list(plan['Vh']))
    m.dual=pyo.Suffix(direction=pyo.Suffix.IMPORT)
    m._md=NS(T=n,pumped={x['id']:x for x in cfg.units['pumped']},hydro={x['id']:x for x in cfg.units['hydro']})
    oracle={};index=0
    for kind,units,varname,conname,endname in [('E',m.S,'E','c_soc','c_eend'),('Vu',m.PS,'Vu','c_pres','c_pvend'),('V',m.HS,'V','c_water','c_vend')]:
        setattr(m,varname,pyo.Var(units,m.K));v=getattr(m,varname)
        setattr(m,conname,pyo.Constraint(units,m.K,rule=lambda _,u,k,v=v:v[u,k]==1.))
        setattr(m,endname,pyo.Constraint(units,rule=lambda _,u,v=v:v[u,n-1]==1.))
        for u in units:
            raw=np.array([(-1.)**(index+k)*(13.25+index*.003+k*.17) for k in range(n)],dtype=np.float64)
            factor=1. if kind=='E' else 1./m._md.pumped[u]['e_gen'] if kind=='Vu' else .36*m._md.hydro[u]['water_rate']
            for k in range(n):m.dual[getattr(m,conname)[u,k]]=raw[k]
            end=7.25+index;m.dual[getattr(m,endname)[u]]=end
            oracle[kind,u]=dict(raw=raw,factor=factor,end=end,con=conname)
            index+=1
    r.update(model=m,pricing_model=m.clone(),issue_t=3,prices={},dispatch_prices={},pricing_info={},slack={},mip={},
             sced={'hydro_water_selection':{'status':'oracle_only'}},solution_quality={},repriced=True,pricing_source='bounded_auxiliary_pricing',
             stage_diagnostics={k:dict(units=[],summary={},integer_values=[],prices=[],hydro_states=[]) for k in ('scuc_preplan','energy_sced','auxiliary_pricing')})
    return cfg,plan,r,oracle


def forbidden(*a,**k):raise AssertionError('No optimization/repricing is permitted in numeric capture/read tests')


@pytest.mark.parametrize('damage',['none','missing_balance','nonfinite_balance','invalid_balance_type','missing_model','missing_dual','missing_terminal'])
def test_nonzero_original_dual_oracle_and_error_semantics(monkeypatch,damage):
    for name in ('solve_scuc','solve_sced','_solver'):
        monkeypatch.setattr(scuc,name,forbidden)
    cfg,plan,r,oracle=probe();m=r['model'];first=next(iter(m.S));bad=('E',first)
    if damage=='missing_balance':del m.dual[m.c_soc[first,5]]
    elif damage=='nonfinite_balance':m.dual[m.c_soc[first,5]]=np.nan
    elif damage=='invalid_balance_type':m.dual[m.c_soc[first,5]]=None
    elif damage=='missing_model':r.pop('model')
    elif damage=='missing_dual':m.del_component('dual')
    elif damage=='missing_terminal':del m.dual[m.c_eend[first]]
    before=rolling.intraday_reference(plan,plan,r,cfg.T,cfg=cfg)
    caught=None
    try:strict_before=id_state_values(r)
    except (TypeError,ValueError) as e:caught=(type(e),str(e))
    record=keep.capture_state_evidence(r);compact=keep.detach_id_models(r)
    # The oracle below does NOT call state_values or the cache validator.
    for kind,u in oracle:
        if damage in ('missing_model','missing_dual') or (kind,u)==bad and damage in ('missing_balance','nonfinite_balance','invalid_balance_type'):
            assert u not in record['values'][kind]
            continue
        row=record['values'][kind][u];o=oracle[kind,u];expected=np.r_[-o['raw'][1:],0.]
        np.testing.assert_array_equal(row['dual_raw'],o['raw'])
        np.testing.assert_array_equal(row['value_native'],expected)
        np.testing.assert_array_equal(row['value_yuan_per_mwh'],expected*o['factor'])
        assert row['value_native'][-1]==0 and np.any(row['value_native'][:-1]!=0)
        if damage=='missing_terminal' and (kind,u)==bad:
            assert row['terminal_target_dual']['available'] is False
            assert row['terminal_target_dual']['value_native'] is None
        else:assert row['terminal_target_dual']['value_native']==o['end']
    if damage in ('missing_balance','nonfinite_balance','invalid_balance_type'):
        assert record['resource_errors'] and first not in record['values']['E']
        assert any(record['values'][k] for k in ('Vu','V'))
        assert before['reference_metadata']['E'][first]['trajectory_source']=='day_ahead'
    monkeypatch.setattr(scuc,'state_values',forbidden)
    same(before,rolling.intraday_reference(plan,plan,compact,cfg.T,cfg=cfg))
    if caught:
        with pytest.raises(caught[0]) as exc:id_state_values(compact)
        assert str(exc.value)==caught[1]
    else:same(strict_before,id_state_values(compact))
    if damage=='missing_balance':
        assert strict_before is None and record['strict_audit']['status']=='unavailable'
    assert 'model' not in compact and 'pricing_model' not in compact


def test_detached_graph_collects_real_pyomo_cycles_and_keeps_numeric_bytes():
    cfg,plan,r,oracle=probe();refs=[weakref.ref(r['model']),weakref.ref(r['pricing_model'])]
    keep.capture_state_evidence(r);before=copy.deepcopy({k:v for k,v in r.items() if k not in ('model','pricing_model')})
    compact=keep.detach_id_models(r)
    same(before,{k:v for k,v in compact.items() if k!='models_retained'})
    del r;gc.collect();assert all(x() is None for x in refs)
    def walk(x,seen):
        if id(x) in seen:return
        seen.add(id(x));assert not isinstance(x,pyo.Component)
        if isinstance(x,dict):
            for k,v in x.items():walk(k,seen);walk(v,seen)
        elif isinstance(x,(list,tuple)):
            for v in x:walk(v,seen)
        elif isinstance(x,np.ndarray):assert x.dtype!=object
    walk(compact,set())


def test_direct_debug_api_retains_default():
    from smm.full import run_full
    from smm.series import run_series
    assert inspect.signature(run_full).parameters['retain_id_models'].default is True
    assert inspect.signature(rolling.run_rolling).parameters['retain_id_models'].default is True
    assert inspect.signature(run_series).parameters['result_retention'].default=='full_debug'


@pytest.mark.parametrize('damage',['valid','native_shift','native_tail','E_conversion','V_conversion','Vu_conversion','factor','missing_anchor','missing_factor','terminal'])
def test_partial_strict_none_still_checks_math_and_independent_anchor(tmp_path,damage):
    import pandas as pd
    from smm.state_bridge import plain
    cfg,plan,r,_=probe();m=r['model'];bad=next(iter(m.S))
    del m.dual[m.c_soc[bad,5]]
    keep.capture_state_evidence(r);row=keep.detach_id_models(r)
    assert row['state_value_evidence']['resource_errors'] and keep.strict_audit_values(row) is None
    archive=keep.archive_state_evidence(NS(id_models_retained=False,id_rounds=[row]))
    hydro={'id_rounds':[{'tau':row['tau'],'snapshot':{k:plain(row['snap'][k]) for k in ('E','Vu','Vh')},'state_values':None}]}
    summary={'id_result_retention':dict(keep.ARCHIVE_CONTRACT),'id_state_value_parameters':keep.conversion_parameters(cfg),'intraday':{'rounds':1},'expected_periods':cfg.T}
    frozen=tmp_path/'hydro_window_evidence.json';frozen.write_text(json.dumps(hydro))
    pd.DataFrame([{'tau':row['tau']}]).to_csv(tmp_path/'id_rounds_summary.csv',index=False)
    original=frozen.read_bytes();rec=archive['rounds'][0]['evidence']
    if damage=='native_shift':rec['values']['V']['H1']['value_native'][0]+=123.
    elif damage=='native_tail':rec['values']['V']['H1']['value_native'][-1]=123.
    elif damage in ('E_conversion','V_conversion','Vu_conversion'):
        kind=damage.split('_')[0];unit=next(iter(rec['values'][kind]));rec['values'][kind][unit]['value_yuan_per_mwh'][0]+=123.
    elif damage=='factor':
        rec['conversion_factors']['V']['H1']*=2
        target=rec['values']['V']['H1'];target['value_yuan_per_mwh']=[v*2 for v in target['value_yuan_per_mwh']]
        target['terminal_target_dual']['value_yuan_per_mwh']*=2
    elif damage=='missing_anchor':summary.pop('id_state_value_parameters')
    elif damage=='missing_factor':rec['conversion_factors']['V'].pop('H1')
    elif damage=='terminal':rec['values']['V']['H1']['terminal_target_dual']['value_yuan_per_mwh']+=123.
    rec['evidence_hash']=digest({k:v for k,v in rec.items() if k!='evidence_hash'})
    (tmp_path/'id_state_value_evidence.json').write_text(json.dumps(archive))
    (tmp_path/'summary.json').write_text(json.dumps(summary))
    if damage=='valid':assert keep.audit_archive(tmp_path,summary)['valid']
    else:
        with pytest.raises(ValueError):keep.audit_archive(tmp_path,summary)
    assert frozen.read_bytes()==original  # Independent strict=None root never altered.


def test_no_id_and_no_state_assets_accept_empty_maps(tmp_path):
    import pandas as pd
    cfg,plan,_,_=probe()
    for kind in ('storage','pumped','hydro'):cfg.units[kind]=[]
    parameters=keep.conversion_parameters(cfg)
    assert keep._parameter_factors(parameters)=={'E':{},'Vu':{},'V':{}}
    rr=NS(id_models_retained=False,id_rounds=[])
    tables=rolling.intraday_tables(NS(case=NS(cfg=cfg,net=NS())),rr)
    tables['summary'].to_csv(tmp_path/'id_rounds_summary.csv',index=False)
    (tmp_path/'id_state_value_evidence.json').write_text(json.dumps(keep.archive_state_evidence(rr)))
    (tmp_path/'hydro_window_evidence.json').write_text(json.dumps({'id_rounds':[]}))
    s={'id_result_retention':dict(keep.ARCHIVE_CONTRACT),'id_state_value_parameters':parameters,'intraday':{'rounds':0},'expected_periods':cfg.T}
    assert keep.audit_archive(tmp_path,s)['valid']
