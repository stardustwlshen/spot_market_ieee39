"""The offline lookahead audit must use the independently archived v3 registry."""
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from smm import full, handoff, rolling, scuc
from smm.causal_types import MODEL, ResourceSpec
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider
from smm.config import load_config
from test_hydro_lookahead_audit import write_archive
from test_validate_runs_evidence import validator


@pytest.fixture(scope='module')
def frozen(tmp_path_factory):
    cfg=load_config();cfg.base['forecast']={'model':MODEL}
    cfg.units['hydro']=cfg.units['hydro'][:1]
    for kind in ('thermal','storage','pumped'):cfg.units[kind]=[]
    h=cfg.units['hydro'][0]['id'];cfg.units['hydro'][0].pop('commitment',None)
    cfg.mode['realtime']['value_method']['hydro_lookahead']='reference_continuation'
    cfg.mode['intraday']['enabled']=False
    a=lambda x:np.full(cfg.T,float(x))
    plan=dict(u={},E={},Vu={},Vh={h:a(17990)},Qout={h:a(600)},
        delta={(h,z):a(0) for z in range(len(cfg.units['hydro'][0]['zones']))},state_values={
        'V':{h:dict(value_native=a(-10),value_yuan_per_mwh=a(-10),dual_raw=a(10),native_unit='native')}})
    meta=handoff.selected_reference(cfg,plan,None,False)[1]
    start='2026-07-15T00:00:00Z'
    _,provider=compile_provider(ProcessPlan(start,1,'UTC',(ResourceSpec(h,'natural_inflow',100.),),root_seed='41'))
    rounds=[]
    for t in range(cfg.T):
        selected=provider.select_day(start,t,t,stage='RT',current_interval=True,executed_through_period=t)
        end=min(t+8,cfg.T)
        terms=rolling.hydro_terminal_lookahead_terms(cfg,plan,NS(hydro_hist={}),t,end,meta,
            selected_inputs=selected,day_start_utc=start)
        model=pyo.ConcreteModel()
        model._case=NS(stage='RT',cfg=cfg,t0=t,terminal={'hydro_terminal_lookahead':terms})
        model._md=NS(T=end-t,hydro={h:cfg.units['hydro'][0]})
        model._state_band_terminal_policy=cfg.mode['realtime']['value_method']['terminal_policy']
        model.V=pyo.Var([h],range(end-t),initialize=17990.)
        scuc._attach_hydro_terminal_lookahead(model)
        if terms:
            model.hydro_terminal_lookahead.slack[h].value=max(terms[h]['minimum_window_end_volume']-17990.,0.)
        source=selected.evidence();source['day_start_utc']=start
        rounds.append(dict(t=t,reference_metadata=deepcopy(meta),snap=dict(Vh={h:np.full(end-t,17990.)},
            Qout={h:np.full(end-t,600.)},input_provenance=source,
            hydro_terminal_lookahead=scuc.hydro_terminal_lookahead_diagnostics(model))))
    path=tmp_path_factory.mktemp('causal_lookahead_archive')
    write_archive(path,cfg,NS(rt=rounds,id_rounds=[],id_failures=[]),plan)
    target=path/'hydro_window_evidence.json';hydro=json.loads(target.read_text())
    hydro['causal_input_registry']=provider.archive(start);target.write_text(json.dumps(hydro))
    return cfg,path


@pytest.fixture
def archive_copy(frozen,tmp_path):
    cfg,path=frozen;target=tmp_path/'archive';shutil.copytree(path,target)
    return cfg,target


def test_offline_audit_passes_exact_frozen_registry(archive_copy,monkeypatch):
    cfg,path=archive_copy
    original=full.hydro_terminal_lookahead_checks;seen=[]
    def check(*args,**kwargs):
        seen.append(kwargs['input_registry'])
        return original(*args,**kwargs)
    monkeypatch.setattr(full,'hydro_terminal_lookahead_checks',check)
    result=validator._hydro_lookahead_audit(path,cfg)
    assert result['hydro_terminal_lookahead_evidence_complete']
    assert result['hydro_terminal_lookahead_checked_records']==88
    saved=json.loads((path/'hydro_window_evidence.json').read_text())['causal_input_registry']
    assert seen==[saved]  # Not reconstructed from the diagnostic's claimed arrays.


@pytest.mark.parametrize('damage',['missing','null','empty','wrong_type','missing_selection','wrong_selection','missing_forecast','wrong_forecast'])
def test_bad_registry_never_falls_back_to_season_or_self_claim(archive_copy,damage):
    cfg,path=archive_copy;target=path/'hydro_window_evidence.json';hydro=json.loads(target.read_text())
    registry=hydro['causal_input_registry']
    first=hydro['rt_windows'][0]['snapshot']['input_provenance']
    if damage=='missing':hydro.pop('causal_input_registry')
    elif damage=='null':hydro['causal_input_registry']=None
    elif damage=='empty':hydro['causal_input_registry']={}
    elif damage=='wrong_type':hydro['causal_input_registry']=[]
    elif damage=='missing_selection':registry['selections'].pop(first['selected_inputs_hash'])
    elif damage=='wrong_selection':registry['selections'][first['selected_inputs_hash']]['rows'][0]['values'][0]+=1.
    elif damage=='missing_forecast':registry['forecasts'].pop(first['forecast_id'])
    else:registry['forecasts'][first['forecast_id']]['series'][0]['values'][10]+=1.
    target.write_text(json.dumps(hydro))
    with pytest.raises((ValueError,TypeError,AttributeError,KeyError)):
        validator._hydro_lookahead_audit(path,cfg)
