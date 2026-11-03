"""Standalone clone pricing invariants; all solver examples are tiny."""
import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc
from smm.auxiliary_pricing import run_auxiliary_pricing
from smm.da import run_da
from test_da_stages import tiny_case
from test_storage_reserve import battery_case, pumped_case
from test_curtailment_cost import case_with_re


def values(m):
    return {v.name: (pyo.value(v), v.fixed, v.lb, v.ub) for v in m.component_data_objects(pyo.Var)}


def test_clone_prices_bands_and_original_exactly_unchanged():
    case = tiny_case(reserve=500)
    # Synthetic declared bid range makes the auxiliary line penalty bind a band.
    case.cfg.mode["price_limits"]["bid_min"] = -1000
    case.bids["units"]["CHEAP"]["segments"][0]["price"] = -500
    result = run_da(case, verbose=False)
    m = result.model
    original = values(m)
    objective, penalties, duals = pyo.value(m.obj), dict(m._pen), dict(m.dual)
    aux = run_auxiliary_pricing(m)
    assert values(m) == original and pyo.value(m.obj) == objective
    assert m._pen == penalties and dict(m.dual) == duals
    assert aux['model'] is not m and aux['integer_invariance']
    assert not aux['auxiliary_quantities_executable']
    assert aux['invocation'] == 'manual_standalone_diagnostic'
    assert aux['operational_enabled'] is False
    assert aux['model']._pen == dict(penalties, balance=10000., line=1000., reserve=2500.)
    np.testing.assert_equal(aux['dispatch_prices']['raw']['lmp'], result.prices['lmp'])
    assert not aux['auxiliary_prices']['settlement_applied']
    assert aux['auxiliary_prices']['lmp_clipped'].max() <= 800
    for row in aux['bands']:
        assert row['epsilon_mw'] == max(.03*abs(row['dispatch_mw']), 1.)
        assert abs(row['deviation_mw']) <= row['epsilon_mw']+1e-6
    assert any(abs(row['deviation_mw']) > 1e-6 for row in aux['bands'])
    assert any(row['lower_binding'] or row['upper_binding'] for row in aux['bands'])
    original_constraints = {c.name: str(c.expr) for c in m.component_data_objects(pyo.Constraint)}
    clone_constraints = {c.name: str(c.expr) for c in aux['model'].component_data_objects(pyo.Constraint)
                         if c.parent_component().local_name != 'auxiliary_dispatch_bands'}
    assert clone_constraints == original_constraints
    assert aux['model']._case.terminal == m._case.terminal
    assert {r['variable'] for r in aux['reserve_differences']} == {'r_up', 'r_dn'}
    assert aux['max_absolute_power_deviation_mw'] > 0


def test_offline_zero_dispatch_has_one_mw_band_but_cannot_wake():
    case = tiny_case(reserve=0)
    case.fixed = {'u': {('LOCAL', 0): 0}}
    case.cfg.units['thermal'][1]['init'].update(online=False, hours=48)
    m = run_da(case, verbose=False).model
    aux = run_auxiliary_pricing(m)
    row = next(r for r in aux['bands'] if r['unit'] == 'LOCAL')
    assert row['dispatch_mw'] == 0 and row['epsilon_mw'] == 1
    assert row['lower_mw'] == -1 and row['upper_mw'] == 1
    assert row['auxiliary_mw'] == 0
    assert aux['model'].u['LOCAL', 0].fixed


@pytest.mark.parametrize('factory,kind', [(battery_case, 'storage_net'), (pumped_case, 'pumped_net')])
def test_signed_charging_net_band_and_state_reporting(factory, kind):
    case = factory(mode='ch' if kind == 'storage_net' else 'pump', enabled=False, power=40.)
    m = run_da(case, verbose=False).model
    before = values(m)
    aux = run_auxiliary_pricing(m)
    row = next(r for r in aux['bands'] if r['resource_type'] == kind)
    assert row['dispatch_mw'] < 0
    assert row['epsilon_mw'] == max(.03*abs(row['dispatch_mw']), 1.)
    assert row['lower_mw'] < row['dispatch_mw'] < row['upper_mw']
    assert values(m) == before
    assert any(r['variable'] == ('E' if kind == 'storage_net' else 'Vu') for r in aux['state_differences'])


def test_every_resource_and_full_horizon_not_only_executed_period():
    case = case_with_re()
    case.cfg.base['periods'] = 2
    case.load_bus = {2: np.array([120., 120.])}
    case.R_up = case.R_dn = np.zeros(2)
    case.ren_fc = {'W': np.array([20.,20.])}
    m = run_da(case, verbose=False).model
    aux = run_auxiliary_pricing(m)
    assert len(aux['bands']) == 8  # 2 thermal + hydro + RE, each at both times
    assert {r['relative_period'] for r in aux['bands']} == {0,1}
    assert {r['resource_type'] for r in aux['bands']} == {'thermal','hydro','renewable'}
    assert len(aux['model'].auxiliary_dispatch_bands) == 8


def test_rejects_unfixed_integer_and_unsolved_model():
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m.u['CHEAP',0].unfix()
    with pytest.raises(ValueError, match='fixed integer'):
        run_auxiliary_pricing(m)
    with pytest.raises((ValueError, TypeError)):
        run_auxiliary_pricing(scuc.build_model(tiny_case(reserve=0)))


def test_failure_propagates_without_mutating_dispatch(monkeypatch):
    m = run_da(tiny_case(reserve=0), verbose=False).model
    before = values(m)
    def fail(model):
        assert model is not m
        raise RuntimeError('synthetic pricing failure')
    monkeypatch.setattr(scuc, 'solve_sced', fail)
    with pytest.raises(RuntimeError, match='synthetic pricing failure'):
        run_auxiliary_pricing(m)
    assert values(m) == before


def test_configuration_required_no_historical_fallback():
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m._case.cfg.mode.pop('auxiliary_pricing')
    m._case.cfg.mode['pricing_penalties'] = dict(line=1, balance=1, reserve=1)
    with pytest.raises(ValueError, match='explicit auxiliary_pricing'):
        run_auxiliary_pricing(m)


def test_agc_out_of_scope_rejected():
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m._agc['enabled'] = True
    with pytest.raises(ValueError, match='AGC-disabled'):
        run_auxiliary_pricing(m)


@pytest.mark.parametrize('factory,mode,flag,families', [
    (battery_case, 'idle', 'u_ch', {'rs_up','rs_dn'}),
    (pumped_case, 'gen', 'xg', {'rp_up','rp_dn'}),
])
def test_original_storage_modes_and_bids_preserved(factory, mode, flag, families):
    from copy import deepcopy
    case = factory(mode=mode, enabled=True, power=40.)
    m = run_da(case, verbose=False).model
    original_bids = deepcopy(m._case.bids)
    expected = {i: pyo.value(v) for i, v in getattr(m, flag).items()}
    assert expected  # exercise retained operating-mode columns
    aux = run_auxiliary_pricing(m)
    clone = aux['model']
    assert {i: pyo.value(v) for i, v in getattr(clone, flag).items()} == expected
    assert all(v.fixed for v in getattr(clone, flag).values())
    assert all(v.fixed for v in getattr(m, flag).values())
    assert families <= {r['variable'] for r in aux['reserve_differences']}
    assert clone._case.bids == original_bids == m._case.bids
    for attribute in ('thermal','storage','pumped'):
        for unit, parameters in getattr(m._md, attribute).items():
            for key in ('c','cd','cc','cg','cp','noload_bid','states','cold'):
                if key in parameters:
                    assert getattr(clone._md, attribute)[unit][key] == parameters[key]


@pytest.mark.parametrize('coefficient', [None, 1000.])
def test_real_storage_anchor_keeps_targets_and_changes_cost_only_if_explicit(coefficient):
    case = battery_case(mode='idle', enabled=False)
    d = case.cfg.units['storage'][0]
    target = d['soc0'] * d['ecap']
    case.terminal.update(storage_anchor={'ES1': [(0,target,target)]}, anchor_penalty=123456.)
    if coefficient is None:
        # Explicit historical absence, not the newly configured research default.
        case.cfg.mode['auxiliary_pricing'].pop('anchor_penalty_yuan_per_discharge_mwh', None)
    else:
        case.cfg.mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = coefficient
    m = run_da(case, verbose=False).model
    assert len(m.A) == 1
    aux = run_auxiliary_pricing(m)['model']
    assert aux._anchors == m._anchors
    assert aux._case.terminal['anchor_penalty'] == 123456.
    for name in ('c_anc_hi','c_anc_lo'):
        assert {i:str(c.expr) for i,c in getattr(aux,name).items()} == {i:str(c.expr) for i,c in getattr(m,name).items()}
    assert pyo.value(aux.A_lo[0]) == target == pyo.value(aux.A_hi[0])
    from pyomo.repn import generate_standard_repn
    coefficients = {v.name: c for v,c in zip(generate_standard_repn(aux.obj.expr).linear_vars,
                                            generate_standard_repn(aux.obj.expr).linear_coefs)}
    assert coefficients['sA_up[0]'] == (123456. if coefficient is None else coefficient*d['eta_d'])
    assert coefficients['sA_dn[0]'] == (123456. if coefficient is None else coefficient*d['eta_d'])
    original = generate_standard_repn(m.obj.expr)
    original_coefficients = {v.name:c for v,c in zip(original.linear_vars, original.linear_coefs)}
    assert original_coefficients['sA_up[0]'] == 123456.


def test_real_hydro_hard_window_target_is_preserved():
    case = case_with_re()
    case.terminal = {'hydro_window_end': {'H1': 100.}}
    m = run_da(case, verbose=False).model
    aux = run_auxiliary_pricing(m)['model']
    assert str(aux.c_hydro_window_end['H1'].expr) == str(m.c_hydro_window_end['H1'].expr)
    assert pyo.value(aux.V['H1',0]) == pytest.approx(100., abs=1e-6)
    assert aux._case.terminal == m._case.terminal


@pytest.mark.parametrize('dt', [.25,.5])
@pytest.mark.parametrize('eta_d', [.8,.9])
def test_anchor_conversion_heterogeneous_plants_no_dt_or_water_flow_factor(dt, eta_d):
    from types import SimpleNamespace
    from smm.auxiliary_pricing import _override_anchor_costs
    from pyomo.repn import generate_standard_repn
    m = pyo.ConcreteModel()
    m._md = SimpleNamespace(dt=dt, storage={'ES': {'eta_d': eta_d}}, pumped={'PS1': {'e_gen': 10.}, 'PS2': {'e_gen': 17.}})
    m._case = SimpleNamespace(terminal={'anchor_penalty': 123456., 'pumped_anchor_penalty': 654321.})
    m._anchor_cost_coefficients = {0:123456., 1:654321., 2:654321.}
    m._anchors = [('ES',0,1.,1.,'E'), ('PS1',0,2.,2.,'Vu'), ('PS2',0,3.,3.,'Vu')]
    m.sA_up = pyo.Var(range(3), initialize=1.)
    m.sA_dn = pyo.Var(range(3), initialize=0.)
    m.obj = pyo.Objective(expr=123456.*(m.sA_up[0]+m.sA_dn[0])+sum(654321.*(m.sA_up[i]+m.sA_dn[i]) for i in (1,2)))
    rows = _override_anchor_costs(m, {'anchor_penalty_yuan_per_discharge_mwh': 500.})
    repn = generate_standard_repn(m.obj.expr)
    coefs = {v.name: c for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    for i, expected in enumerate([500.*eta_d,5000.,8500.]):
        assert coefs[f'sA_up[{i}]'] == expected
        assert coefs[f'sA_dn[{i}]'] == expected
        assert rows[i]['new_yuan_per_native_unit'] == expected
    assert pyo.value(m.obj) == 13500.+500.*eta_d


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf'), True, '1000'])
def test_invalid_anchor_cost_rejected_without_mutation(bad):
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m._case.cfg.mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = bad
    before = values(m)
    with pytest.raises(ValueError):
        run_auxiliary_pricing(m)
    assert values(m) == before


def test_no_anchors_returns_empty_cost_diagnostics_with_explicit_coefficient():
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m._case.cfg.mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = 500.
    assert run_auxiliary_pricing(m)['anchor_costs'] == []


def test_explicit_null_anchor_override_is_disabled():
    m = run_da(tiny_case(reserve=0), verbose=False).model
    m._case.cfg.mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = None
    assert run_auxiliary_pricing(m)['anchor_costs'] == []
