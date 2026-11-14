from copy import deepcopy
import hashlib,json
from pathlib import Path
import pytest
import scenario_inputs as s

def test_reject_bad_values():
    for field,value in [('solar_capacity_factor',True),('solar_capacity_factor',float('nan')),('solar_capacity_factor',1.1),('name','')]:
        x=s.defaults();x[field]=value
        with pytest.raises(ValueError):s.checked_spec(x)
    x=s.defaults();x['thermal_offer_prices']['C1']=[300,200,300,400,500,600]
    with pytest.raises(ValueError):s.checked_spec(x)
    x=s.defaults();x['reserve_mw']=0
    with pytest.raises(ValueError):s.checked_spec(x)

def test_compiler_exact_coupled_sources_and_old_untouched(tmp_path):
    baseline={str(p):s.sha(p) for p in s.base_paths().values()};x=s.defaults();x['solar_capacity_factor']=.9;x['thermal_offer_prices']['C1']=[v+1 for v in x['thermal_offer_prices']['C1']];x['minimum_adjustable_prices']['C1']+=1
    a=s.compile_scenario(x,tmp_path/'a');b=s.compile_scenario(x,tmp_path/'b');assert a==b
    assert baseline=={str(p):s.sha(p) for p in s.base_paths().values()}
    loads,_,prior_value=s.codec();case=loads((tmp_path/'a/case.json').read_bytes());old=loads(s.base_paths()['case'].read_bytes());static=s.read(tmp_path/'a/rt_assets/static.json');p=s.read(tmp_path/'a/policy.json');priors=loads((tmp_path/'a/rt_assets/priors.codec.json').read_bytes());sun=json.loads(priors['contract']['sources']['prior:solar_mw']['artifact'])
    assert case['reserve']==old['reserve'] and case['batteries']==old['batteries'] and case['battery_contract']==old['battery_contract']
    assert case['energy_inputs']['load_mw']==old['energy_inputs']['load_mw']
    assert (tmp_path/'a/rt_assets/preparation.json').read_bytes()==s.base_paths()['preparation'].read_bytes()
    u=case['energy_inputs']['thermal']['C1'];assert static['resources']['C1']['original_offer'][1]['price']==281
    assert set(u['segments'][0]['price_yuan_per_mwh'].values())=={281}
    assert set(u['base_bid_yuan_per_h'].values())=={290*281}
    assert p['minimum_adjustable_prices']['C1']['yuan_per_mwh']==81
    for kind in ('startup','shutdown'):
        assert all(z['cost']==case['no_load_cost']['C1']+z['mean']*281 for z in case['curves']['C1'][kind])
    for key,value in s.solar_means(sun,prior_value,case['contract']['day_start'],576).items():assert case['energy_inputs']['renewable_mw'][key]==value
    original_priors=loads(s.base_paths()['priors'].read_bytes())
    for family in ('load_mw','wind_mw','boundary_tie_mw'):assert priors['contract']['sources']['prior:'+family]==original_priors['contract']['sources']['prior:'+family]
    with pytest.raises(ValueError):s.compile_scenario(x,tmp_path/'a')
    (tmp_path/'a/spec.json').write_text('{}')
    with pytest.raises(ValueError):s.validate_scenario(tmp_path/'a')

def test_zero_solar_preserves_zero_midnight_and_non_solar(tmp_path):
    x=s.defaults();x['solar_capacity_factor']=0;s.compile_scenario(x,tmp_path/'z');loads,_,_=s.codec();case=loads((tmp_path/'z/case.json').read_bytes());old=loads(s.base_paths()['case'].read_bytes());solar={'33','4','8'}
    assert all(v==0 if b in solar else v==old['energy_inputs']['renewable_mw'][b,t] for (b,t),v in case['energy_inputs']['renewable_mw'].items())
