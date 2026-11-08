"""Adequacy screens are conservative necessary conditions, never certificates."""
import copy
import numpy as np
import pytest

from smm.config import load_config, ROOT
from smm.bids import default_bids
from smm.case import build_da_case
from smm.forecast import generate_profiles
from smm.network import build_network
from smm.scenario_assessment import assess_boundaries, combination_matrix
from smm.typical_scenarios import prepare_typical, create_typical, fingerprint
from smm import scenario


def case(mode='supply',season='wet', *, reserve_policy=None):
    cfg=load_config(season=season);cfg.base['tie_line_mode']=mode
    if reserve_policy is not None:
        assert reserve_policy in ('formula', 'fixed400')
        cfg.mode=copy.deepcopy(cfg.mode)
        cfg.mode['reserve']['up_requirement_mode']='formula' if reserve_policy=='formula' else 'fixed'
        cfg.mode['reserve']['fixed_up_mw']=400.
    return build_da_case(cfg,build_network(cfg),generate_profiles(cfg),default_bids(cfg))


@pytest.mark.parametrize('reserve_policy', ['formula', 'fixed400'])
def test_supply_conversion_difference_is_twice_tie_not_a_storage_solution(reserve_policy):
    before=fingerprint(ROOT)
    supply=case(reserve_policy=reserve_policy);fixed=case('fixed_load',reserve_policy=reserve_policy)
    a,b=assess_boundaries(supply),assess_boundaries(fixed)
    assert not a['physical_feasibility_certified'] and not b['physical_feasibility_certified']
    assert all(row['net_energy_upper_mwh']==0 for row in a['storage_net_energy_bounds'])
    assert a['hydro_energy_bounds'][0]['upper_mwh']==pytest.approx(11200)
    dry=assess_boundaries(case(season='dry',reserve_policy=reserve_policy))
    assert dry['hydro_energy_bounds'][0]['upper_mwh']==pytest.approx(4400)
    astage=next(r for r in a['stages'] if r['stage']=='actual')
    bstage=next(r for r in b['stages'] if r['stage']=='actual')
    assert bstage['demand_mwh']-astage['demand_mwh']==pytest.approx(supply.profiles.TIE_sched.sum()*.25)
    assert astage['optimistic_supply_energy_upper_mwh']-bstage['optimistic_supply_energy_upper_mwh']==pytest.approx(supply.profiles.TIE_sched.sum()*.25)
    # TIE conversion changes the energy balance under either reserve policy.
    # Tier differences below are reserve-pressure screens, not feasibility proofs.
    energy_keys=('unavoidable_energy_shortfall_lower_mwh','instantaneous_shortfall_lower_mw')
    reserve_key='energy_or_up_reserve_shortfall_lower_mw'
    for assessment in (a,b):
        assert all(row[key]==pytest.approx(0.) for row in assessment['stages'] for key in energy_keys)
        assert not assessment['physical_feasibility_certified']
    assert a['tier']=='unverified_candidate'
    assert all(row[reserve_key]==pytest.approx(0.) and row['energy_or_up_reserve_deficient_periods']==0
               for row in a['stages'])
    if reserve_policy=='formula':
        assert np.all(supply.R_up>400.) and np.all(fixed.R_up>400.)
        assert b['tier']=='pressure_required'
        assert all(row[reserve_key]>0. and row['energy_or_up_reserve_deficient_periods']>0
                   for row in b['stages'])
    else:
        for model in (supply,fixed):
            np.testing.assert_array_equal(model.R_up,np.full(len(model.R_up),400.))
        assert b['tier']=='unverified_candidate'
        assert all(row[reserve_key]==pytest.approx(0.) and row['energy_or_up_reserve_deficient_periods']==0
                   for row in b['stages'])
    assert fingerprint(ROOT)==before


def test_screen_cannot_certify_network_and_all_factorial_cells_present():
    c=case();a=assess_boundaries(c)
    c.net.branches['rate_mw']=.001
    b=assess_boundaries(c)
    assert a==b and not b['physical_feasibility_certified']
    basic=combination_matrix();split=combination_matrix(True)
    assert len(basic)==18 and len({x['id'] for x in basic})==18
    assert len(split)==54 and len({x['id'] for x in split})==54
    assert {(x['season'],x['wind_multiplier'],x['pv_multiplier'],x['load_multiplier']) for x in split} == {
        (s,w,p,l) for s in ('wet','dry') for w in (.8,1.,1.2) for p in (.8,1.,1.2) for l in (.9,1.,1.1)}


def test_independent_wind_pv_and_sustained_event_preserve_source_and_restore(tmp_path,monkeypatch):
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    before=fingerprint(ROOT)
    baseline=prepare_typical(ROOT)
    modified=prepare_typical(ROOT,wind_multiplier=.8,pv_multiplier=1.2,wind_event='evening_lull')
    h=(np.arange(96)+.5)*.25
    mask=np.interp(h,[0,16,17,21,22,24],[1,1,.25,.25,1,1])
    for stage in ('actual','da','id','rt'):
        np.testing.assert_allclose(modified['profiles'][f'W1_{stage}'],baseline['profiles'][f'W1_{stage}']*.8*mask)
        np.testing.assert_allclose(modified['profiles'][f'PV1_{stage}'],np.minimum(baseline['profiles'][f'PV1_{stage}']*1.2,652))
        np.testing.assert_allclose(modified['profiles'][f'load_{stage}'],baseline['profiles'][f'load_{stage}'])
    np.testing.assert_allclose(modified['profiles'].TIE_sched,baseline['profiles'].TIE_sched)
    assert fingerprint(ROOT)==before
    created=create_typical('mixed_event',modified)
    restored=prepare_typical(created,restore=True)
    assert restored['wind_event']=='none'
    np.testing.assert_allclose(restored['profiles'].W1_actual,baseline['profiles'].W1_actual)
    repeated=prepare_typical(created,wind_multiplier=.8,pv_multiplier=1.2,wind_event='evening_lull')
    np.testing.assert_allclose(repeated['profiles'].W1_actual,modified['profiles'].W1_actual)


@pytest.mark.parametrize('args',[{'wind_multiplier':float('nan')},{'pv_multiplier':False},{'wind_event':'unknown'}])
def test_invalid_independent_assumptions_fail_closed(args):
    with pytest.raises(ValueError):prepare_typical(ROOT,**args)
