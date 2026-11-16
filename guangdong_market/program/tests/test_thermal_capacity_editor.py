from copy import deepcopy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_provincial_inputs import apply_thermal_edits

def case():
 d=dict(min_mw={0:20.,1:20.},max_mw={0:100.,1:100.},bid_base_mw=20.,segments=[dict(width_mw=30.,price_yuan_per_mwh={0:40.,1:40.}),dict(width_mw=50.,price_yuan_per_mwh={0:50.,1:50.})],base_bid_yuan_per_h={0:800.,1:800.},initial_online=1,initial_power_mw=50.,initial_state_age_h=8.,min_up_h=4.,min_down_h=4.,startup_output_limit_mw={0:20.,1:20.},shutdown_output_limit_mw={0:20.,1:20.},fixed_power_mw={0:None,1:None},status={0:'free',1:'free'},ramp_up_mw_per_h={0:60.,1:60.},startup_yuan={0:2000.,1:2000.})
 return dict(energy_inputs=dict(thermal={'G':d,'H':deepcopy(d)}),curves={g:{'startup':[dict(begin=0.,mean=10.,end=20.,cost=123.)],'shutdown':[dict(begin=20.,mean=10.,end=0.,cost=123.)]} for g in ('G','H')},reserve={'primary_fraction':{'G':.06}})

@pytest.mark.parametrize('maximum',[60.,180.])
def test_capacity_expansion_and_reduction(maximum):
 c=case();old=deepcopy(c);apply_thermal_edits(c,{'G':{'max_mw':maximum}});d=c['energy_inputs']['thermal']['G'];orig=old['energy_inputs']['thermal']['G']
 assert d['max_mw']=={0:maximum,1:maximum}
 assert sum(z['width_mw'] for z in d['segments'])==pytest.approx(maximum-20.)
 assert d['segments'][0]['width_mw']/d['segments'][1]['width_mw']==pytest.approx(3/5)
 for k in set(orig)-{'max_mw','segments'}:assert d[k]==orig[k]
 assert [z['price_yuan_per_mwh']for z in d['segments']]==[z['price_yuan_per_mwh']for z in orig['segments']]
 assert c['curves']==old['curves'] and c['reserve']==old['reserve'] and c['energy_inputs']['thermal']['H']==old['energy_inputs']['thermal']['H']

@pytest.mark.parametrize('edit',[{'max_mw':20.},{'max_mw':40.},{'max_mw':float('nan')},{'max_mw':True},{'unsupported_parameter':10.}])
def test_bad_values_atomic(edit):
 c=case();old=deepcopy(c)
 with pytest.raises(ValueError):apply_thermal_edits(c,{'H':{'max_mw':180.},'G':edit})
 assert c==old

@pytest.mark.parametrize('bad',['segments','time','fixed','startup','curve','agc'])
def test_dependent_constraints(bad):
 c=case();d=c['energy_inputs']['thermal']['G'];agc=None
 if bad=='segments':d['segments'][0]['width_mw']=1.
 if bad=='time':d['max_mw'][1]=90.
 if bad=='fixed':d['fixed_power_mw'][1]=70.
 if bad=='startup':d['startup_output_limit_mw'][1]=70.
 if bad=='curve':c['curves']['G']['startup'][0]['end']=70.
 if bad=='agc':agc={'G':{'up_mw':{'0':30.},'down_mw':{'0':20.}}}
 with pytest.raises(ValueError):apply_thermal_edits(c,{'G':{'max_mw':60.}},agc=agc)

def test_changed_initial_output_and_noop():
 c=case();apply_thermal_edits(c,{'G':{'max_mw':40.,'initial_power_mw':30.}})
 assert c['energy_inputs']['thermal']['G']['initial_power_mw']==30.
 old=deepcopy(c);apply_thermal_edits(c,{'G':{'max_mw':40.}});assert c==old

@pytest.mark.parametrize('status',['free','off','unavailable'])
def test_fixed_zero_valid_when_not_forced_online(status):
 c=case();d=c['energy_inputs']['thermal']['G'];d['fixed_power_mw'][1]=0.;d['status'][1]=status
 apply_thermal_edits(c,{'G':{'max_mw':180.}})
 assert c['energy_inputs']['thermal']['G']['fixed_power_mw'][1]==0.

@pytest.mark.parametrize('status,power',[('on',0.),('off',30.),('unavailable',30.)])
def test_fixed_power_status_conflict(status,power):
 c=case();d=c['energy_inputs']['thermal']['G'];d['fixed_power_mw'][1]=power;d['status'][1]=status
 with pytest.raises(ValueError):apply_thermal_edits(c,{'G':{'max_mw':180.}})

@pytest.mark.parametrize('technology,old,new,before,after',[('coal',646,700,'280','280'),('coal',646,599,'280','300'),('coal',299,300,'330','300'),('gas',99,100,'600','550'),('gas',299,300,'550','500')])
def test_profit_cost_preview_separate_from_bids(technology,old,new,before,after):
 from gd_provincial_inputs import capacity_cost_preview
 z=capacity_cost_preview(technology,old,new)
 assert z['before']['generation_CNY_MWh']==before and z['after']['generation_CNY_MWh']==after
 assert z['before']['startup_CNY_per_event']==z['after']['startup_CNY_per_event']

def test_existing_custom_cost_untouched_new_capacity_new_run(tmp_path):
 from test_gd_default_costs import view
 import gd_economics_inputs as api
 v=view();record=api.defaults(v);record['units']['C']['generation_CNY_MWh']='321';record['units']['C']['source']='user cost'
 path=api.save(v,record,tmp_path);original=path.read_bytes()
 fresh=deepcopy(v);fresh['run_id']='new-run';fresh['settlement_hour_rows'][0]['capacity_mw']='599'
 new=api.load(fresh,tmp_path)
 assert new['units']['C']['generation_CNY_MWh']=='300'
 assert api.load(v,tmp_path)['units']['C']['generation_CNY_MWh']=='321' and path.read_bytes()==original
