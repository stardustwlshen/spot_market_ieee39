"""Incremental battery/pumped reserve limits, without full-day solves.

第二轮F项：备用可用电量/库容改按时段末存量 E[t]/Vu[t]（已扣除同时段能量计划的充放电/发电），
替代原“时段初存量”口径；以下期望值按时段末存量重算。"""
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
        c.init = {'thermal': {g['id']: deepcopy(g['init']) for g in c.cfg.units['thermal']}, 'hydro': {}, 'storage': {'ES1': initial_energy}}
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
        c.init = {'thermal': {g['id']: deepcopy(g['init']) for g in c.cfg.units['thermal']}, 'hydro': {}, 'pumped': {'PS1': {'V': initial_volume, 'ng': int(mode == 'gen'), 'np': int(mode == 'pump'), 'xg': int(mode == 'gen'), 'xp': int(mode == 'pump')}}}
    return c


def solve(c, modify=None):
    m = scuc.build_model(c)
    if modify:
        modify(m)
    scuc.solve_scuc(m)
    incumbent = pyo.value(m.obj)
    assert not hasattr(m, 'z_s') and not hasattr(m, 'z_p')
    scuc.fix_integers(m)
    scuc.solve_sced(m)
    assert pyo.value(m.obj) <= incumbent + max(1e-5, abs(incumbent)*1e-8)
    return m


def expected_battery(m):
    d = m._md.storage['ES1']; p = pyo.value(m.Pd['ES1',0]-m.Pc['ES1',0])
    e = pyo.value(m.E['ES1',0])  # 第二轮F项：时段末存量
    H = m._case.cfg.mode['reserve']['T_dur_h']
    up = min(d['pmax']-p, (e-d['soc_min']*d['ecap'])*d['eta_d']/H)
    down = min(d['pmax']+p, (d['soc_max']*d['ecap']-e)/d['eta_c']/H)
    return max(0.,up), max(0.,down)


@pytest.mark.parametrize('mode,power', [('idle',0.),('ch',20.),('dis',20.)])
def test_battery_cross_zero_extremes_and_efficiency(mode,power):
    m=solve(battery_case(mode,power))
    up,dn=expected_battery(m)
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


def test_zero_reserve_keeps_fast_baseline_energy_trajectory():
    # 80 MW discharge is feasible for 15 min, not sustainably for a whole hour.
    m=solve(battery_case('dis',80.),lambda m:(m.rs_up['ES1',0].fix(0), m.rs_dn['ES1',0].fix(0)))
    assert pyo.value(m.rs_up['ES1',0])==0 and pyo.value(m.rs_dn['ES1',0])==0
    assert pyo.value(m.Pd['ES1',0])==80
    assert pyo.value(m.E['ES1',0])==pytest.approx(100-80*.25/.9)


@pytest.mark.parametrize('mode', ['pump','idle'])
def test_pumped_pump_and_idle_cannot_supply_reserve(mode):
    m=solve(pumped_case(mode))
    assert pyo.value(m.rp_up['PS1',0])==0 and pyo.value(m.rp_dn['PS1',0])==0


def test_pumped_generation_uses_effective_both_reservoir_bounds():
    m=solve(pumped_case())
    assert m._md.pumped['PS1']['v_lo']==100  # max(vup_min=80, vtotal-vdnmax=100)
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(60)  # power headroom binds; incremental water allows 90 MW
    assert pyo.value(m.rp_dn['PS1',0])==pytest.approx(20)
    m=solve(pumped_case(initial_volume=130.,vdn_max=180.))
    assert m._md.pumped['PS1']['v_lo']==120
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(60)


def test_pumped_actual_initial_low_volume_limits_incremental_reserve():
    # Original 15 min baseline can run at 40 MW from V102; 1h qualification cannot.
    m=solve(pumped_case(initial_volume=102.))
    assert pyo.value(m.Vu['PS1',0])==pytest.approx(101)
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(10)  # 第二轮F项：(V_end101-v_lo100)*e_gen10/1h
    assert pyo.value(m.rp_dn['PS1',0])==pytest.approx(20)


def test_disabled_has_empty_qualification_and_no_duration_domain_change():
    c=battery_case('dis',80.,enabled=False)
    c.cfg.mode['reserve']['T_dur_h']=float('nan')  # ignored when participation is disabled
    m=solve(c)
    assert not hasattr(m,'z_s') and not hasattr(m,'z_p')
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
    assert pyo.value(m.rp_up['PS1',0])==pytest.approx(60.)
    assert pyo.value(m.rp_dn['PS1',0])==0
    assert len(m.c_Rdn)==0


def test_down_enabled_flag_requires_boolean():
    c=battery_case()
    c.cfg.mode['reserve']['down_enabled']='false'
    with pytest.raises(ValueError,match='down_enabled'):
        scuc.build_model(c)


@pytest.mark.parametrize('factory,upvar,stock,lo,conversion', [
    (lambda: battery_case('dis',80.), 'rs_up', 'E', 20., .9),
    (lambda: pumped_case(initial_volume=102.), 'rp_up', 'Vu', 100., 10.),
])
def test_incremental_limit_is_not_full_baseline_plus_activation_guarantee(factory,upvar,stock,lo,conversion):
    m=solve(factory());uid=next(iter(m.S if stock=='E' else m.PS))
    R=pyo.value(getattr(m,upvar)[uid,0]);available=pyo.value(getattr(m,stock)[uid,0])  # 第二轮F项：时段末存量
    available=(available-lo)*conversion
    baseline=pyo.value(m.Pd[uid,0] if stock=='E' else m.Pg[uid,0])
    assert R<=available+1e-6
    assert baseline+R>available  # documented simplification, not an equivalence claim


def test_pumped_upper_bound_limits_down_incremental_reserve():
    m=solve(pumped_case(initial_volume=280.))
    assert pyo.value(m.Vu['PS1',0])==pytest.approx(279.)
    assert pyo.value(m.rp_dn['PS1',0])==pytest.approx(10.)  # 第二轮F项：V_end=279，上库余量1×e_gen10/1h


def test_duration_scaling_and_no_added_integers():
    c=battery_case();c.cfg.mode['reserve']['T_dur_h']=2.
    m=solve(c)
    assert pyo.value(m.rs_up['ES1',0])==pytest.approx(36.)
    assert pyo.value(m.rs_dn['ES1',0])==pytest.approx(50.)
    assert not any(v.name.startswith(('z_s[','z_p[')) for v in m.component_data_objects(pyo.Var))


def test_initial_below_operating_band_can_recover_with_zero_up_reserve():
    c=battery_case('ch',20.,initial_energy=20.)
    c.bids['units']['ES1']['soc_min']=.11  # declared Emin22, technical Emin20
    m=solve(c)
    assert pyo.value(m.E['ES1',0])==pytest.approx(24.)
    assert pyo.value(m.rs_up['ES1',0])==pytest.approx((24.-22.)*.9)  # 第二轮F项：时段末存量高于申报Emin的部分


def test_period_end_stock_counts_same_period_planned_charge():
    # 第二轮F项（已知简化）：同时段计划充入的电量计入时段末存量，可用于该时段上备用
    m=solve(battery_case('ch',20.,initial_energy=20.))
    assert pyo.value(m.E['ES1',0])==pytest.approx(24.)
    assert pyo.value(m.rs_up['ES1',0])==pytest.approx((24.-20.)*.9)


@pytest.mark.parametrize('factory, constraint, stockvar', [
    (battery_case, 'c_rsup_soc', 'E'), (pumped_case, 'c_rpup_volume', 'Vu')])
def test_later_period_uses_own_period_end_stock(factory, constraint, stockvar):
    from pyomo.core.expr.visitor import identify_variables
    c=factory();c.cfg.base['periods']=2
    c.load_bus={k:np.repeat(v,2) for k,v in c.load_bus.items()}
    c.R_up=np.repeat(c.R_up,2);c.R_dn=np.repeat(c.R_dn,2)
    m=scuc.build_model(c);uid=next(iter(m.S if stockvar=='E' else m.PS))
    names={v.name for v in identify_variables(getattr(m,constraint)[uid,1].body)}
    assert getattr(m,stockvar)[uid,1].name in names      # 第二轮F项：时段末存量
    assert getattr(m,stockvar)[uid,0].name not in names


@pytest.mark.parametrize('mode,power,initial,lo,hi,expected', [
    ('ch',80.,20.,.18,.19,36.),
    ('dis',72.,180.,.79,.80,160.),
])
def test_first_period_power_tightening_uses_actual_initial_state(mode,power,initial,lo,hi,expected):
    c=battery_case(mode,power,initial_energy=initial)
    c.bids['units']['ES1'].update(soc_min=lo,soc_max=hi)
    m=solve(c)
    assert pyo.value(m.E['ES1',0])==pytest.approx(expected)


@pytest.mark.parametrize('factory,variable,expected', [
    (lambda: battery_case(initial_energy=25.), 'rs_up',18.),
    (lambda: pumped_case(initial_volume=101.), 'rp_up',0.),  # 第二轮F项：V_end=100=v_lo
])
def test_fifteen_minute_incremental_energy_conversion(factory,variable,expected):
    c=factory();c.cfg.mode['reserve']['T_dur_h']=.25
    m=solve(c);uid=next(iter(m.S if variable=='rs_up' else m.PS))
    assert pyo.value(getattr(m,variable)[uid,0])==pytest.approx(expected)
