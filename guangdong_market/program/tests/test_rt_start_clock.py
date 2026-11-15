import copy, importlib.util
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1]/'app/gd_live_market_runtime'
def mod(name):
 s=importlib.util.spec_from_file_location('rt_start_'+name,B/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def case():
 return {'energy_inputs':{'thermal':{'g':dict(initial_state_kind='stable',initial_state_age_h=2,min_up_h=4,min_down_h=1,initial_power_mw=50)}},'batteries':{'b':dict(initial_energy_mwh=10,initial_power_mw=0,initial_mode='idle',initial_mode_age_h=1,cycle_budget={'2026-01-01':{'used_internal_mwh':0,'limit_cycles':2}})}}
def test_origin_uses_declared_state_not_day_ahead_projection():
 c=case();old={'origin':'2026-01-01T00:00:00+08:00','thermal':{'g':{'end_power_mw':99}},'storage':{'b':{'initial_energy_mwh':90}},'delivery_tick':3,'executed_end_exclusive':0}
 before=copy.deepcopy(old);z=mod('initial_state').from_declared(c,old)
 assert old==before
 assert z['thermal']['g']['end_power_mw']==50
 assert z['thermal']['g']['residual_up_ticks']==24
 assert z['storage']['b']['initial_energy_mwh']==10
 assert z['storage']['b']['cycle_budget']==c['batteries']['b']['cycle_budget']
 assert z['cutoff_tick']==z['delivery_tick']==z['issued_end_exclusive']==z['executed_end_exclusive']==0
 assert not z['predicted_issued_prefix']
def test_offline_state_does_not_reset_down_age():
 c=case();c['energy_inputs']['thermal']['g'].update(initial_state_kind='off',initial_state_age_h=.25,initial_power_mw=0)
 z=mod('initial_state').from_declared(c,{'origin':'2026-01-01T00:00:00+08:00'})
 assert z['thermal']['g']['residual_down_ticks']==9
 assert z['thermal']['g']['phase']=='off'
def test_active_initial_curve_rejected_not_silently_reset():
 c=case();c['energy_inputs']['thermal']['g']['initial_state_kind']='startup'
 with pytest.raises(ValueError):mod('initial_state').from_declared(c,{'origin':'2026-01-01T00:00:00+08:00'})
def test_new_hour_has_four_current_prices_no_da_seed():
 h=mod('hour_scope').hour(0,same_period_execution=True)
 assert h['source_windows']==[0,1,2,3]
 assert h['cells']==list(range(12))
 assert h['executed_end']==h['issued_end']==12
 assert h['excluded_issued_ticks']==[]
def test_legacy_hour_remains_explicitly_separate():
 h=mod('hour_scope').hour(0)
 assert h['source_windows']==[-1,0,1,2]
 assert h['issued_end']==15
 assert h['excluded_issued_ticks']==[12,13,14]
def test_daily_hours_never_double_count():
 hs=[mod('hour_scope').hour(i,96,same_period_execution=True) for i in range(24)]
 assert [t for h in hs for t in h['cells']]==list(range(288))
 assert [q for h in hs for q in h['source_windows']]==list(range(96))
