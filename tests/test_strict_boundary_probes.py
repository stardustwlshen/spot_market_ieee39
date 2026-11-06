"""Independent counterexamples against abd1cb7. Failing assertions are expected pre-fix."""
import json
import numpy as np
import pandas as pd
import pytest
from smm.config import load_config
from smm.forecast import generate_profiles, apply_profile_overrides
from smm.case import reserve_requirement
from smm.input_validation import validate_physical_inputs


def test_half_hour_time_labels_follow_validated_grid():
    c=load_config(); c.base.update(periods=48,dt_h=.5)
    c.mode['realtime']['interval_min']=30
    validate_physical_inputs(c)
    p=generate_profiles(c)
    assert p.time.iloc[-1]=='23:30'


def test_no_conventional_resources_when_contingency_disabled():
    c=load_config()
    for family in ('thermal','hydro','pumped'):c.units[family]=[]
    c.mode['reserve']['include_ctg']=False
    validate_physical_inputs(c)
    up,dn,info=reserve_requirement(c,np.array([100.]),np.array([20.]))
    assert info['R_ctg']==0
    assert up[0]==pytest.approx(5.)


def test_boolean_uploaded_boundary_must_not_become_one_mw():
    c=load_config();p=generate_profiles(c)
    with pytest.raises(ValueError):
        apply_profile_overrides(c,p,pd.DataFrame({'W1_rt':[True]*c.T}))


def test_misspelled_wind_profile_must_not_silently_become_pv():
    c=load_config();c.units['renewable'][0]['profile']='wind_a '
    with pytest.raises(ValueError):
        validate_physical_inputs(c)
        generate_profiles(c)


def test_zero_load_summary_is_json_compatible():
    from test_da_stages import tiny_case
    from smm.da import run_da,summarize
    c=tiny_case(reserve=0);c.load_bus={2:np.zeros(1)}
    s=summarize(run_da(c,verbose=False))
    json.dumps(s,allow_nan=False)


def test_reversed_clearing_limits_must_not_reprice_every_bus_to_upper_limit():
    from test_da_stages import tiny_case
    from smm.da import run_da
    c=tiny_case(reserve=0);c.cfg.mode['price_limits'].update(clear_min=800,clear_max=-50)
    with pytest.raises(ValueError):
        run_da(c,verbose=False)


@pytest.mark.parametrize('lo,hi', [(800., -50.), (float('nan'),800.), (-50.,float('inf')), (True,800.), ('0',800.)])
def test_invalid_clearing_limits_rejected_before_model_build(lo, hi):
    c=load_config(); c.mode['price_limits'].update(clear_min=lo,clear_max=hi)
    with pytest.raises(ValueError, match='price_limits'):
        validate_physical_inputs(c)


@pytest.mark.parametrize('include,n1,expected',[(False,0,0),(False,80,0),(True,0,0),(True,80,80)])
def test_empty_conventional_contingency_preserves_explicit_n1(include,n1,expected):
    c=load_config()
    for family in ('thermal','hydro','pumped'):c.units[family]=[]
    c.mode['reserve']['include_ctg']=include;c.base['n1_loss_mw']=n1
    up,dn,info=reserve_requirement(c,np.array([100.]),np.array([20.]))
    assert info['max_unit']==0 and info['R_ctg']==expected
    assert up[0]==pytest.approx(5+expected)


@pytest.mark.parametrize('value',[True,False,np.bool_(True)])
def test_mixed_object_boolean_override_rejected(value):
    c=load_config();p=generate_profiles(c);column=np.full(c.T,1.,dtype=object);column[3]=value
    with pytest.raises(ValueError,match='布尔'):
        apply_profile_overrides(c,p,pd.DataFrame({'W1_rt':column}))


def test_zero_load_da_print_summary_is_explicitly_not_applicable(capsys):
    from test_da_stages import tiny_case
    from smm.da import run_da,summarize
    from smm.pipeline import print_summary
    c=tiny_case(reserve=0);c.load_bus={2:np.zeros(1)}
    s=summarize(run_da(c,verbose=False));assert s['lmp_load_weighted_avg'] is None
    print_summary(s);assert '不适用（无负荷）' in capsys.readouterr().out


def test_production_day_and_synthetic_horizon_scopes(monkeypatch):
    from smm.pipeline import prepare
    from smm.input_validation import validate_production_day
    from smm.live_runner import completion_metadata
    c=load_config();c.base['periods']=48
    with pytest.raises(ValueError,match='periods×dt_h=24'):prepare(c,read_only=True)
    c.base['dt_h']=.5;c.mode['realtime']['interval_min']=30
    validate_production_day(c)
    assert completion_metadata(48,48,dt_h=.5)['settled'] is True
    assert completion_metadata(48,48,dt_h=.25)['settled'] is False


def test_hourly_price_statistics_follow_dt_and_undefined_load():
    from smm.full import _hourly_means,_weighted_or_none
    assert _hourly_means([10,20,300,400],.5)==[15,350]
    assert _hourly_means([10,20],.75)==[12.5]
    assert _hourly_means([np.nan,np.nan],.5)==[None]
    assert _weighted_or_none([10,20],[0,0]) is None


def test_zero_load_full_chain_summary_uses_null_for_undefined_type_prices():
    from test_da_stages import tiny_case
    from smm.full import run_full, summarize_full
    case = tiny_case(reserve=0)
    case.load_bus = {2: np.zeros(1)}
    case.profiles = pd.DataFrame({'period': [1], 'time': ['00:00'],
                                 **{f'load_{stage}': [0.] for stage in ('actual', 'da', 'id', 'rt')}})
    case.cfg.mode['reserve'].update(include_ctg=False, rho_L=0., rho_RE=0.)
    result = run_full(case.cfg, case, periods=1, log=lambda *a: None)
    summary = summarize_full(result)
    json.dumps(summary, allow_nan=False)
    assert summary['settlement_by_type']['coal']['avg_price'] is None
    assert summary['spread']['rt_lw_avg'] is None
    assert summary['settlement_calculated'] is True
    assert summary['settled'] is False
