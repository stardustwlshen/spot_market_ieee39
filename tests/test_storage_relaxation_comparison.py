"""Explicit LP storage comparison preserves its relaxed feasible set in SCED."""
import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc
from smm.da import run_da
from smm.auxiliary_pricing import _integers, run_auxiliary_pricing
from test_storage_reserve import battery_case


def comparison_case():
    case = battery_case(enabled=False)
    case.cfg.base['solver']['storage_binary'] = False
    case.net.branches['rate_mw'] = 200.
    case.R_up = case.R_dn = np.zeros(1)
    case.fixed = {}
    case.terminal = None
    case.bids['units']['ES1'].update(
        dis_segments=[dict(p_start=0, p_end=100, price=-10.)],
        ch_segments=[dict(p_start=0, p_end=100, price=-20.)])
    case.bids['units']['CHEAP']['segments'][0]['price'] = -50.
    return case


def test_fixing_preserves_relaxed_storage_exactly_instead_of_faking_two_on_modes():
    model = scuc.build_model(comparison_case())
    scuc.solve_scuc(model)
    before = {name: pyo.value(getattr(model, name)['ES1', 0]) for name in ('u_ch', 'u_dis')}
    assert all(0. < value < 1. for value in before.values())
    assert sum(before.values()) == pytest.approx(1.)
    objective = pyo.value(model.obj)
    scuc.fix_integers(model)
    assert {name: pyo.value(getattr(model, name)['ES1', 0]) for name in before} == before
    scuc.solve_sced(model)
    assert pyo.value(model.obj) == pytest.approx(objective)
    assert pyo.value(model.E['ES1', 0]) == pytest.approx(100.)
    assert scuc.storage_simultaneous(model) == 1  # Intentional nonphysical relaxation, honestly reported.
    scuc.fix_integers(model)  # Repeated stage preparation must not round already-relaxed states.
    assert {name: pyo.value(getattr(model, name)['ES1', 0]) for name in before} == before


@pytest.mark.parametrize('auxiliary', [False, True])
def test_da_and_auxiliary_preserve_explicit_comparison_scope(auxiliary):
    case = comparison_case()
    case.cfg.mode['auxiliary_pricing']['enabled'] = auxiliary
    result = run_da(case, verbose=False)
    assert result.scuc_info['storage_relaxation_comparison'] is True
    assert result.scuc_info['storage_simultaneous'] == 1
    assert result.stage_comparison['integer_mismatch'] == 0
    assert result.stage_comparison['objective_nonincrease']
    assert result.prices['lmp'].shape == (2, 1)
    assert np.isfinite(result.prices['lmp']).all()
    if auxiliary:
        diagnostics = result.pricing_info
        assert diagnostics['storage_relaxation_comparison'] is True
        assert diagnostics['dispatch_model_scope'] == 'storage_lp_relaxation_comparison_not_physical_dispatch'
        assert diagnostics['original_dispatch_unchanged'] is True
        for name in ('u_ch', 'u_dis'):
            assert pyo.value(getattr(result.pricing_model, name)['ES1', 0]) == pyo.value(getattr(result.model, name)['ES1', 0])


def test_comparison_does_not_relax_actual_integer_validation():
    result = run_da(comparison_case(), verbose=False)
    result.model.u['CHEAP', 0].fix(.5)
    with pytest.raises(ValueError, match='fixed integer'):
        run_auxiliary_pricing(result.model)


def test_mutating_configuration_cannot_make_a_built_binary_state_fractional():
    case = comparison_case()
    case.cfg.base['solver']['storage_binary'] = True
    result = run_da(case, verbose=False)
    result.model._case.cfg.base['solver']['storage_binary'] = False
    result.model.u_ch['ES1', 0].fix(.5)
    with pytest.raises(ValueError, match='fixed integer'):
        _integers(result.model)


@pytest.mark.parametrize('value', [-.1, 1.1, float('nan'), float('inf')])
def test_relaxed_domain_still_rejects_invalid_state(value):
    result = run_da(comparison_case(), verbose=False)
    result.model.u_ch['ES1', 0].fix(value)
    with pytest.raises(ValueError):
        _integers(result.model)


@pytest.mark.parametrize('entry',['full','rolling'])
@pytest.mark.parametrize('flag',[False,0,np.bool_(False)])
def test_relaxed_comparison_rejected_before_any_executed_chain(entry,flag,monkeypatch):
    from types import SimpleNamespace
    from smm import full,rolling
    case=comparison_case()
    case.cfg.base['solver']['storage_binary']=flag
    if entry=='full':
        monkeypatch.setattr(full,'run_da',lambda *a,**k:pytest.fail('LP comparison entered actual DA/RT chain'))
        call=lambda:full.run_full(case.cfg,case)
    else:
        monkeypatch.setattr(rolling,'_da_plan_arrays',lambda *a:pytest.fail('relaxed state was rounded into actual chain'))
        call=lambda:rolling.run_rolling(SimpleNamespace(case=case))
    with pytest.raises(ValueError,match='storage_binary=false'):call()
