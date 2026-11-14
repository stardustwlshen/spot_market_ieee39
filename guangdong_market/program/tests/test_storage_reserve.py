"""Qualified battery/pumped reserve extremes, without full-day solves."""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc
from smm.config import load_config
from smm.bids import default_bids
from smm.rolling import snapshot
from test_da_stages import tiny_case


def battery_case(mode='idle', power=0., *, enabled=True, initial_energy=None):
    c = tiny_case(reserve=1000.)
    c.R_dn = np.array([1000.])
    c.cfg.mode['reserve'].update(storage_reserve=enabled, down_enabled=True, T_dur_h=1., providers=[], mu_RE=0.)
    source = load_config()
    d = deepcopy(source.units['storage'][0])
    d.update(bus=2, eta_c=.8, eta_d=.9, soc0=.5)
    c.cfg.units['storage'] = [d]
    c.bids['units']['ES1'] = default_bids(source)['units']['ES1']
    c.bids['units']['ES1']['soc_end'] = None
    c.fixed = {'u_dis': {('ES1', 0): int(mode == 'dis')}, 'u_ch': {('ES1', 0): int(mode == 'ch')}}
    c.terminal = {'fix_power': {'Pd': {('ES1', 0): power if mode == 'dis' else 0},
                              'Pc': {('ES1', 0): power if mode == 'ch' else 0}}}
    if initial_energy is not None:
        c.init = {'hydro': {}, 'storage': {'ES1': initial_energy}}
    return c


def pumped_case(mode='gen', power=40., *, enabled=True, initial_volume=None, vdn_max=200.):
    c = tiny_case(reserve=1000.)
    c.R_dn = np.array([1000.])
    c.cfg.mode['reserve'].update(storage_reserve=enabled, down_enabled=True, T_dur_h=1., providers=[], mu_RE=0.)
    source = load_config()
    d = deepcopy(source.units['pumped'][0])
    d.update(bus=2, n_units=1, unit_gen_max=100., unit_gen_min=20., unit_pump=100., unit_pump_min=70.,
             e_gen=10., e_pump=12., v_total=300., vup_min=80., vup_max=280., vdn_min=20.,
             vdn_max=vdn_max, vup0=110., vup_end_bid=None, init_mode=mode)
    c.cfg.units['pumped'] = [d]
    c.bids['units']['PS1'] = dict(mode='price_quantity',
        gen_segments=[dict(p_start=0., p_end=100., price=50.)],
        pump_segments=[dict(p_start=0., p_end=100., price=0.)], vup_end=None)
    c.fixed = {name: {('PS1', 0): value} for name, value in {
        'xg': int(mode == 'gen'), 'xp': int(mode == 'pump'),
        'ngen': int(mode == 'gen'), 'npump': int(mode == 'pump')}.items()}
    c.terminal = {'fix_power': {'Pg': {('PS1', 0): power if mode == 'gen' else 0},
                              'Pp': {('PS1', 0): 100. if mode == 'pump' else 0}}}
    if initial_volume is not None:
        c.init = {'hydro': {}, 'pumped': {'PS1': {'V': initial_volume}}}
    return c


def solve(c, modify=None):
    m = scuc.build_model(c)
    if modify:
        modify(m)
    scuc.solve_scuc(m)
    incumbent = pyo.value(m.obj)
    flags = {name: {i: pyo.value(v) for i, v in getattr(m, name).items()} for name in ('z_s', 'z_p')}
    scuc.fix_integers(m)
    scuc.solve_sced(m)
    assert pyo.value(m.obj) <= incumbent + max(1e-5, abs(incumbent)*1e-8)
    for name, values in flags.items():
        assert all(getattr(m, name)[i].fixed and pyo.value(getattr(m, name)[i]) == value for i, value in values.items())
    return m


def expected_battery(m):
    d = m._md.storage['ES1']; p = pyo.value(m.Pd['ES1',0]-m.Pc['ES1',0])
    points = [d['soc0']*d['ecap'], pyo.value(m.E['ES1',0])]
    up = min(d['pmax']-p, *[(e-d['soc_min']*d['ecap'])*d['eta_d']-p for e in points])
    down = min(d['pmax']+p, *[(d['soc_max']*d['ecap']-e)/d['eta_c']+p for e in points])
    return max(0.,up), max(0.,down)


@pytest.mark.parametrize('mode,power', [('idle',0.),('ch',20.),('dis',20.)])
def test_battery_cross_zero_extremes_and_efficiency(mode,power):
    m=solve(battery_case(mode,power))
    up,dn=expected_battery(m)
    assert pyo.value(m.z_s['ES1',0])==1
    assert pyo.value(m.rs_up['ES1',0])==pytest.approx(up)
    assert pyo.value(m.rs_dn['ES1',0])==pytest.approx(dn)
    p=pyo.value(m.Pd['ES1',0]-m.Pc['ES1',0])
    assert p+up>0 and p-dn<0  # reserve may cross zero; baseline binaries stay unchanged
    assert pyo.value(m.u_ch['ES1',0]+m.u_dis['ES1',0])<=1
    pr=scuc.extract_prices(m)
    assert pr["reserve_down_enabled"] is True
    assert np.isfinite(pr["reserve_dn_price"]).all()


def test_battery_actual_initial_energy_and_start_end_boundaries():
    m=solve(battery_case('idle',initial_energy=25.))
    assert m._md.storage['ES1']['soc0']*200==25
    assert pyo.value(m.rs_up['ES1',0])==pytest.approx(4.5)
    # At Emin / Emax, one reserve direction must be zero, the other may still qualify.
    low=solve(battery_case(initial_energy=20.))
    high=solve(battery_case(initial_energy=180.))
    assert pyo.value(low.rs_up['ES1',0])==pytest.approx(0)
    assert pyo.value(high.rs_dn['ES1',0])==pytest.approx(0)


def test_unqualified_zero_reserve_keeps_fast_baseline_energy_trajectory():
    # 80 MW discharge is feasible for 15 min, not sustainably for a whole hour.
    m=solve(battery_case('dis',80.),lambda m:m.z_s['ES1',0].fix(0))
    assert pyo.value(m.rs_up['ES1',0])==0 and pyo.value(m.rs_dn['ES1',0])==0
    assert pyo.value(m.Pd['ES1',0])==80
    assert pyo.value(m.E['ES1',0])==pytest.approx(100-80*.25/.9)


@pytest.mark.parametrize('mode', ['pump','idle'])
def test_pumped_pump_and_idle_cannot_supply_reserve(mode):
    m=solve(pumped_case(mode))
    assert pyo.value(m.z_p['PS1',0])==0
    assert pyo.value(m.rp_up['PS1',0])==0 and pyo.value(m.rp_dn['PS1',0])==0


def test_pumped_generation_uses_effective_both_reservoir_bounds():
    m=solve(pumped_case())
    assert m._md.pumped['PS1']['v_lo']==100  # max(vup_min=80, vtotal-vdnmax=100)
    assert pyo.value(m.z_p['PS1',0])==1
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(50)  # end V109, (109-100)*10 - P40
    assert pyo.value(m.rp_dn['PS1',0])==pytest.approx(20)
    m=solve(pumped_case(initial_volume=130.,vdn_max=180.))
    assert m._md.pumped['PS1']['v_lo']==120
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(50)


def test_pumped_actual_initial_low_volume_can_decline_qualification():
    # Original 15 min baseline can run at 40 MW from V102; 1h qualification cannot.
    m=solve(pumped_case(initial_volume=102.),lambda m:m.z_p['PS1',0].fix(0))
    assert pyo.value(m.Vu['PS1',0])==pytest.approx(101)
    assert pyo.value(m.rp_up['PS1',0])==0 and pyo.value(m.rp_dn['PS1',0])==0


def test_disabled_has_empty_qualification_and_no_duration_domain_change():
    c=battery_case('dis',80.,enabled=False)
    c.cfg.mode['reserve']['T_dur_h']=float('nan')  # ignored when participation is disabled
    m=solve(c)
    assert len(m.z_s)==0 and len(m.z_p)==0
    assert not hasattr(m,'c_rsup_soc') and not hasattr(m,'c_rpup_volume')
    assert pyo.value(m.Pd['ES1',0])==80
    assert pyo.value(m.rs_up['ES1',0])==0


@pytest.mark.parametrize('duration',[None,0.,-1.,float('nan'),float('inf'),True,'1'])
def test_enabled_duration_must_be_explicit_finite_positive(duration):
    c=battery_case()
    if duration is None:c.cfg.mode['reserve'].pop('T_dur_h')
    else:c.cfg.mode['reserve']['T_dur_h']=duration
    with pytest.raises(ValueError,match='T_dur_h'):scuc.build_model(c)


def test_up_only_reduced_charging_can_qualify_without_hour_long_baseline():
    c=battery_case('ch',80.,initial_energy=120.)
    c.cfg.mode['reserve']['down_enabled']=False
    m=solve(c,lambda m:m.rs_up['ES1',0].fix(40.))
    assert pyo.value(m.z_s['ES1',0])==1
    assert pyo.value(m.E['ES1',0])==pytest.approx(136.)
    # Pu=-80+40=-40 MW: 1h activation adds32MWh within end headroom44;
    # the unactivated -80 baseline would add64MWh and must NOT be checked instead.
    assert 40*.8<=180-pyo.value(m.E['ES1',0])
    assert 80*.8>180-pyo.value(m.E['ES1',0])
    assert len(m.c_Rdn)==0
    for name in ('r_dn','rh_dn','rs_dn','rp_dn','sR_dn'):
        assert all(v.fixed and pyo.value(v)==0 for v in getattr(m,name).values())
    pr=scuc.extract_prices(m)
    assert pr['reserve_down_enabled'] is False
    assert pr['reserve_dn_price']==[None]


def test_up_only_pumped_generation_has_no_down_award():
    c=pumped_case()
    c.cfg.mode['reserve']['down_enabled']=False
    m=solve(c)
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(50.)
    assert pyo.value(m.rp_dn['PS1',0])==0
    assert len(m.c_Rdn)==0


def test_down_enabled_flag_requires_boolean():
    c=battery_case()
    c.cfg.mode['reserve']['down_enabled']='false'
    with pytest.raises(ValueError,match='down_enabled'):
        scuc.build_model(c)
