import pytest
from smm.config import load_config
from smm.input_validation import validate_time_grid,validate_physical_inputs
from smm import pipeline

@pytest.mark.parametrize('storage_mode',['value','fixed_da','sced_anchor'])
def test_all_modes_reject_conflicting_interval(storage_mode):
    cfg=load_config();cfg.mode['realtime']['storage']['mode']=storage_mode
    cfg.mode['realtime']['interval_min']=30
    with pytest.raises(ValueError,match='interval_min'):
        validate_time_grid(cfg)

@pytest.mark.parametrize('key,value',[('interval_min',0),('interval_min',float('nan')),('interval_min',True),
 ('lookahead_h',0),('lookahead_h',.3),('lookahead_h',float('inf')),('binding_periods',2),('binding_periods',True)])
def test_invalid_clock_parameters_fail_closed(key,value):
    cfg=load_config();cfg.mode['realtime'][key]=value
    with pytest.raises(ValueError):validate_time_grid(cfg)

@pytest.mark.parametrize('interval',[0,.3,float('nan'),True])
def test_enabled_id_interval_must_be_positive_integral_grid(interval):
    cfg=load_config();cfg.mode['intraday'].update(enabled=True,interval_h=interval)
    with pytest.raises(ValueError,match='intraday.interval_h'):validate_time_grid(cfg)

@pytest.mark.parametrize('dt,minutes,hours',[(.25,15,2),(.5,30,2),(.25,15,.25)])
def test_consistent_grid_accepted_without_rounding(dt,minutes,hours):
    cfg=load_config();cfg.base['dt_h']=dt;cfg.mode['realtime'].update(interval_min=minutes,lookahead_h=hours)
    validate_time_grid(cfg)

def test_disabled_id_interval_not_used():
    cfg=load_config();cfg.mode['intraday'].update(enabled=False,interval_h=0)
    validate_time_grid(cfg)

def test_prepare_rejects_clock_before_input_or_profile_access(monkeypatch):
    cfg=load_config();cfg.mode['realtime']['interval_min']=30
    monkeypatch.setattr(pipeline,'build_network',lambda *a,**k:pytest.fail('inputs read before clock check'))
    with pytest.raises(ValueError,match='interval_min'):pipeline.prepare(cfg,read_only=True)
