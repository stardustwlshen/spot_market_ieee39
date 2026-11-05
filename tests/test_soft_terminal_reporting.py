"""Policy compliance, exact targets, and emergency fallback are separate claims."""
from copy import deepcopy
from types import SimpleNamespace as NS
import json
import numpy as np
import pandas as pd
import pytest
import yaml
from smm import full
from test_value_band_audit import band_fixture, result_quality_messages
from test_validate_runs_evidence import archive, validator, change


def soft_band():
    cfg,zero,r=band_fixture()
    cfg.units['storage'][0]['eta_d']=.9
    cfg.mode['realtime']['value_method']={'terminal_policy':{'mode':'soft','penalty_yuan_per_discharge_mwh':1000.}}
    r['snap']['value_window'][0].update(terminal_policy='soft',planned_terminal_soft=True,
        terminal_penalty_yuan_per_discharge_mwh=1000.,discharge_energy_factor=.9,
        deviation_discharge_mwh=54.,terminal_penalty_cost=54000.)
    return cfg,zero,r


def test_planned_terminal_deviation_is_compliant_but_never_zero_slack():
    cfg,zero,r=soft_band();a=full.state_band_policy_checks(cfg,[r],day_end_targets={('E','S'):(180.,180.)})
    assert a['terminal_policy_compliant'] and not a['state_band_targets_satisfied']
    assert a['executed_terminal_targets_zero_deviation'] is False
    assert a['executed_terminal_penalty_cost']==54000.
    assert a['emergency_state_band_slack_max']==0 and a['emergency_band_fallback_count']==0
    assert not full.slack_policy_checks(zero,[r],state_band_required=True)['rules_satisfied_no_slack']


@pytest.mark.parametrize('field',['planned_terminal_soft','terminal_policy','terminal_penalty_cost',
    'discharge_energy_factor','deviation_discharge_mwh','terminal_penalty_yuan_per_discharge_mwh'])
def test_soft_policy_evidence_fail_closed(field):
    cfg,_,r=soft_band();del r['snap']['value_window'][0][field]
    assert not full.state_band_policy_checks(cfg,[r])['terminal_policy_compliant']


def test_planned_cost_is_stock_cost_and_forecast_is_not_executed():
    cfg,_,r=soft_band();r['t']=0;r['snap']['E']['S']=np.array([100.,120.])
    r['snap']['value_window'][0]['relative_period']=1
    a=full.state_band_policy_checks(cfg,[r]);assert a['executed_terminal_penalty_cost'] is None
    r['snap']['value_window'][0]['terminal_penalty_cost']*=.25
    assert not full.state_band_policy_checks(cfg,[r])['terminal_policy_compliant']


def test_wrong_original_target_fails_even_if_soft_fields_self_consistent():
    cfg,_,r=soft_band()
    assert not full.state_band_policy_checks(cfg,[r],day_end_targets={('E','S'):(100.,100.)})['terminal_policy_compliant']


@pytest.fixture
def soft_archive(archive):
    p=archive/'scenario_snapshot/config/modes.yaml';m=yaml.safe_load(p.read_text())
    m['MKT']['realtime']['value_method']={'terminal_policy':{'mode':'soft','penalty_yuan_per_discharge_mwh':1000.}}
    p.write_text(yaml.safe_dump(m))
    p=archive/'scenario_snapshot/config/units.yaml';u=yaml.safe_load(p.read_text());u['storage'][0]['eta_d']=.95
    u['pumped'][0]['e_gen']=9.6;u['hydro'][0]['water_rate']=1.2;p.write_text(yaml.safe_dump(u))
    change(archive,'rt_storage.csv',lambda a:a.assign(E_mwh=80.))
    a=pd.read_csv(archive/'rt_value_windows.csv');a['terminal_policy']='soft';a['planned_terminal_soft']=True
    a['terminal_penalty_yuan_per_discharge_mwh']=1000.;a['value_native']=0.
    a.loc[a.kind=='E','state']=80.;a.loc[a.kind=='V','upper']=100.
    a['band_slack']=np.maximum(a.lower-a.state,0.)
    a['discharge_energy_factor']=a.kind.map({'E':.95,'Vu':9.6,'V':1/(.36*1.2)})
    a['deviation_discharge_mwh']=a.band_slack*a.discharge_energy_factor
    a['terminal_penalty_cost']=a.deviation_discharge_mwh*1000.
    a.to_csv(archive/'rt_value_windows.csv',index=False)
    change(archive,'rt_system.csv',lambda a:a.assign(state_band_slack_max_win=20.))
    return archive


def test_soft_archive_accepts_policy_without_claiming_zero_target(soft_archive):
    c=validator.checks(soft_archive)
    assert c['验收通过'] and c['终态政策合规'] and c['非计划松弛为0'],c['说明']
    assert not c['松弛为0'] and not c['日末目标零偏差'] and not c['日末约束']
    assert c['已执行终态优化罚金元']==19000.


@pytest.mark.parametrize('damage',['missing_cost','wrong_cost','fake_target','unexplained_scalar','wrong_policy'])
def test_soft_archive_does_not_whitelist_invalid_evidence(soft_archive,damage):
    if damage=='unexplained_scalar':change(soft_archive,'rt_system.csv',lambda a:a.assign(state_band_slack_max_win=40.))
    else:
        def edit(a):
            if damage=='missing_cost':return a.drop(columns='terminal_penalty_cost')
            if damage=='wrong_cost':a['terminal_penalty_cost']*=.25
            if damage=='fake_target':a.loc[a.kind=='E',['lower','upper']]=80.
            if damage=='wrong_policy':a['terminal_policy']='hard'
            return a
        change(soft_archive,'rt_value_windows.csv',edit)
    c=validator.checks(soft_archive);assert not c['验收通过'],c


def test_ui_distinguishes_planned_target_from_emergency(tmp_path):
    cfg,_,r=soft_band();c=full.state_band_policy_checks(cfg,[r]);c['rules_satisfied_no_slack']=False
    messages=result_quality_messages({'checks':c,'realtime':{'storage_mode':'value'},
        'intraday':{'rounds':2,'failure_count':1,'attempted_rounds':3}},{},directory=tmp_path)
    assert any('属于计划软目标偏差' in s for _,s in messages)
    assert any('不是市场结算付款' in s for _,s in messages)
    assert any('成功 2 轮、失败 1 轮' in s for _,s in messages)
    assert not any('紧急状态带回退' in s for _,s in messages)


def test_zero_terminal_penalty_is_not_a_valid_soft_policy():
    cfg,_,r=soft_band()
    cfg.mode['realtime']['value_method']['terminal_policy']['penalty_yuan_per_discharge_mwh']=0.
    with pytest.raises(ValueError,match='terminal policy'):
        full._terminal_policy(cfg)
    assert not full.state_band_policy_checks(cfg,[r])['terminal_policy_compliant']


def test_archive_rejects_zero_terminal_penalty(soft_archive):
    p=soft_archive/'scenario_snapshot/config/modes.yaml';m=yaml.safe_load(p.read_text())
    m['MKT']['realtime']['value_method']['terminal_policy']['penalty_yuan_per_discharge_mwh']=0.
    p.write_text(yaml.safe_dump(m))
    result=validator.checks(soft_archive)
    assert not result['验收通过'] and '冻结终态政策/罚价无效' in result['说明']
