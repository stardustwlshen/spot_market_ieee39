"""Value-method audit regressions: missing evidence is not a passing result."""
from copy import deepcopy
from types import SimpleNamespace as NS
import importlib.util
from pathlib import Path
import sys
import json
import numpy as np
import pandas as pd
import pytest
import yaml
from smm import full, rolling

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from market_views import result_quality_messages


def band_fixture():
    cfg=NS(T=2,mode={'realtime':{'storage':{'mode':'value'},'hydro':'sced'}},
           units={'storage':[{'id':'S'}],'pumped':[],'hydro':[]})
    w=dict(kind='E',unit='S',period=1,relative_period=0,label='day_end',value_native=0.,
           state=120.,lower=180.,upper=180.,slack_up=0.,slack_dn=60.,soft=True)
    zero=dict(shed_mwh=0.,surplus_mwh=0.,line_slack_max=0.,reserve_up_short_max=0.,
              reserve_dn_short_max=0.,anchor_slack_max_mwh=0.,state_band_slack_max=0.)
    r={'t':1,'snap':{'E':{'S':np.array([120.])},'value_window':[w]},'slack':dict(zero,state_band_slack_max=60.)}
    return cfg,zero,r


def test_actual_soft_day_end_deficit_is_not_no_slack():
    cfg,zero,r=band_fixture()
    a=full.slack_policy_checks(zero,[r],state_band_required=True)
    b=full.state_band_policy_checks(cfg,[r])
    assert not a['rules_satisfied_no_slack']
    assert b['state_band_constraints_satisfied'] and not b['state_band_targets_satisfied']
    assert b['storage_state_band_slack_max_mwh']==60.
    assert b['storage_executed_day_end_deviation_mwh']==60.


def test_real_solver_sixty_mwh_deficit_regression():
    from test_storage_reserve import battery_case
    c=battery_case('idle');c.R_up=np.zeros(1);c.R_dn=np.zeros(1);c.fixed={};c.stage='RT'
    c.cfg.mode['reserve']['down_enabled']=False
    c.cfg.mode['realtime'].get('value_method',{}).pop('terminal_policy',None)  # historical emergency-softening case
    c.terminal={'state_band':[('E','ES1',0,180.,180.,'day_end')],
                'state_band_soft':True,'state_band_penalty':1e5}
    r=rolling.clear_window(c);r['t']=0
    assert r['snap']['E']['ES1'][0]==pytest.approx(120.)
    assert r['slack']['state_band_slack_max']==pytest.approx(60.)
    zero={k:0. for k in r['slack']}
    assert not full.slack_policy_checks(zero,[r],state_band_required=True)['rules_satisfied_no_slack']
    assert full.state_band_policy_checks(c.cfg,[r])['storage_state_band_slack_max_mwh']==pytest.approx(60.)


@pytest.mark.parametrize('damage',['missing_rows','missing_resource','duplicate','nan','wrong_state','wrong_period','wrong_label','wrong_slack','hard','missing_summary'])
def test_value_band_evidence_fail_closed(damage):
    cfg,zero,r=band_fixture();w=r['snap']['value_window'][0]
    if damage=='missing_rows':del r['snap']['value_window']
    elif damage=='missing_resource':r['snap']['value_window']=[]
    elif damage=='duplicate':r['snap']['value_window'].append(deepcopy(w))
    elif damage=='nan':w['state']=np.nan
    elif damage=='wrong_state':w['state']+=1
    elif damage=='wrong_period':w['period']=0
    elif damage=='wrong_label':w['label']='band'
    elif damage=='wrong_slack':w['slack_dn']=0
    elif damage=='hard':w['soft']=False
    else:del r['slack']['state_band_slack_max']
    assert not full.state_band_policy_checks(cfg,[r])['state_band_constraints_satisfied']


def test_missing_scalar_cannot_certify_current_value_mode():
    _,zero,r=band_fixture();del r['slack']['state_band_slack_max']
    assert not full.slack_policy_checks(zero,[r],state_band_required=True)['rules_satisfied_no_slack']


def test_window_day_end_not_mislabelled_as_executed():
    cfg,_,r=band_fixture();r['t']=0;r['snap']['E']['S']=np.array([100.,120.])
    r['snap']['value_window'][0]['relative_period']=1
    result=full.state_band_policy_checks(cfg,[r])
    assert result['storage_day_end_window_deviation_max_mwh']==60.
    assert result['storage_executed_day_end_deviation_mwh'] is None


def test_ui_value_band_units_and_executed_day_end(tmp_path):
    cfg,zero,r=band_fixture();a=full.slack_policy_checks(zero,[r],state_band_required=True)
    a.update(full.state_band_policy_checks(cfg,[r]))
    a.update(physical_feasible_checked_constraints=True,pumped_state_band_slack_max_1e4m3=2.)
    messages=result_quality_messages({'checks':a,'realtime':{'storage_mode':'value'}},{},directory=tmp_path)
    assert any('已执行日末目标偏差 60.0000 MWh' in s for _,s in messages)
    assert any('电池 60.0000 MWh' in s and '抽蓄 2.0000 万m³' in s for _,s in messages)
    assert not any(level=='error' for level,_ in messages)


def test_ui_old_no_slack_cannot_hide_saved_value_deviation(tmp_path):
    pd.DataFrame([{'kind':'E','band_slack':60.}]).to_csv(tmp_path/'rt_value_windows.csv',index=False)
    messages=result_quality_messages({'checks':{'rules_satisfied_no_slack':True},'realtime':{'storage_mode':'value'}},{},directory=tmp_path)
    assert any('旧汇总的无松弛标记不能覆盖' in s for _,s in messages)
    assert any('完整校核证据未记录' in s for _,s in messages)


def test_hydro_selected_id_counts_not_compared_with_da(monkeypatch):
    from test_tracking_reporting import evidence
    da,rr=evidence(monkeypatch,soft=False)
    da.case.cfg.mode['realtime']['hydro']='value'
    da.case.cfg.mode['intraday']={'enabled':True}
    rr.rt[0]['snap']['Vh']['H']=np.array([100.])
    rr.rt[0]['snap']['hydro_constraints']['H']['qin_m3s']=[0.]
    rr.rt[0]['snap']['delta'][('H',0)]=np.array([2])
    rr.rt[0]['hydro_reference_delta']={('H',0):np.array([2])}
    rr.rt[0]['reference_metadata']={'V':{'H':{'trajectory_source':'intraday@0'}}}
    result=full.hydro_window_checks(da,rr)
    assert result['hydro_window_constraints_satisfied']
    assert result['hydro_reference_zone_count_mismatch']==0
    assert result['hydro_da_zone_count_mismatch']==0
    rr.rt[0]['hydro_reference_delta'][('H',0)]=np.array([1])
    result=full.hydro_window_checks(da,rr)
    assert result['hydro_reference_zone_count_mismatch']==1
    assert not result['hydro_window_constraints_satisfied']


def test_missing_id_reference_is_not_a_da_pass(monkeypatch):
    from test_tracking_reporting import evidence
    da,rr=evidence(monkeypatch)
    da.case.cfg.mode['realtime']['hydro']='value';da.case.cfg.mode['intraday']={'enabled':True}
    result=full.hydro_window_checks(da,rr)
    assert not result['hydro_reference_evidence_complete']
    assert not result['hydro_window_constraints_satisfied']


def test_hydro_independent_unit_count_and_power_bounds(monkeypatch):
    from test_tracking_reporting import evidence
    da,rr=evidence(monkeypatch)
    da.case.cfg.units['hydro'][0].update(n_units=1,zones=[[0.,100.]])
    rr.rt[0]['snap']['delta'][('H',0)]=np.array([2])
    rr.rt[0]['hydro_reference_delta']={('H',0):np.array([2])}
    result=full.hydro_window_checks(da,rr)
    assert result['hydro_unit_count_violation_max']==1
    assert not result['hydro_window_constraints_satisfied']
