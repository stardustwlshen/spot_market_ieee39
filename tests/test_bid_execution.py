"""End-to-end bid-to-dispatch contracts on a valid two-unit, one-period case.

These tests use the actual run_da/clear_window and production bid validator. They
need only tiny solver calls, not the full-day fixture or any write to bids_v2.
"""
import copy

import numpy as np
import pytest

from smm.bid_simulation import simulate_bids
from smm.bids import raise_on_errors, validate_bids
from smm.da import run_da
from smm import scuc
from test_da_stages import tiny_case


def _awards(result):
    return result.tables['units'].set_index('unit').P_mw.to_dict()


def test_selected_manual_offer_changes_actual_dispatch_and_nodal_price():
    original_case = tiny_case(reserve=0, local_price=700)
    original_bids = copy.deepcopy(original_case.bids)
    edited_case = copy.deepcopy(original_case)
    edited_case.bids['units']['CHEAP']['segments'][0]['price'] = 750
    raise_on_errors(validate_bids(edited_case.cfg, edited_case.bids, require_complete=True))

    before = run_da(original_case, verbose=False)
    after = run_da(edited_case, verbose=False)

    assert _awards(before) == pytest.approx({'CHEAP': 50, 'LOCAL': 50})
    assert _awards(after) == pytest.approx({'CHEAP': 0, 'LOCAL': 100})
    assert after.model._md.thermal['CHEAP']['c'] == [750]
    np.testing.assert_allclose(before.prices['lmp'][:, 0], [10, 700], atol=1e-5)
    np.testing.assert_allclose(after.prices['lmp'][:, 0], [700, 700], atol=1e-5)
    np.testing.assert_allclose(after.prices['lmp'], scuc.extract_prices(after.model)['lmp'])
    assert after.sced_info['objective'] == pytest.approx(100 * 700 * .25)
    assert original_case.bids == original_bids
    assert edited_case.bids['units']['LOCAL'] == original_bids['units']['LOCAL']
    assert edited_case.bids['units']['CHEAP']['segments'][0]['price'] == 750


def _frozen_merit_reversing_simulation():
    case = tiny_case(reserve=0, local_price=700)
    case.bids['units']['CHEAP']['segments'][0]['price'] = 690
    original = copy.deepcopy(case.bids)
    # Choose a deterministic, explicitly recorded scenario that crosses the
    # nearby merit-order threshold. This is test construction, not a UI policy
    # that searches for a preferred market result.
    for seed in range(64):
        preview = simulate_bids(case.cfg, selected_unit_ids=['CHEAP'], seed=seed,
                                base_bids=original)
        if preview['units']['CHEAP']['segments'][0]['price'] > 701:
            assert preview == simulate_bids(case.cfg, selected_unit_ids=['CHEAP'],
                                             seed=seed, base_bids=original)
            return case, original, preview
    raise AssertionError('Expected a positive-shift scenario in the fixed seed set')


def test_selected_simulated_offer_reaches_model_and_reverses_merit_order():
    base_case, original_bids, preview = _frozen_merit_reversing_simulation()
    scenario_case = copy.deepcopy(base_case)
    scenario_case.bids = copy.deepcopy(preview)
    before = run_da(base_case, verbose=False)
    after = run_da(scenario_case, verbose=False)

    selected_price = preview['units']['CHEAP']['segments'][0]['price']
    assert after.model._md.thermal['CHEAP']['c'] == [selected_price]
    assert _awards(before) == pytest.approx({'CHEAP': 50, 'LOCAL': 50})
    assert _awards(after) == pytest.approx({'CHEAP': 0, 'LOCAL': 100})
    np.testing.assert_allclose(before.prices['lmp'][:, 0], [690, 700], atol=1e-5)
    np.testing.assert_allclose(after.prices['lmp'][:, 0], [700, 700], atol=1e-5)
    assert base_case.bids == original_bids
    assert scenario_case.bids == preview
    assert preview['units']['LOCAL'] == original_bids['units']['LOCAL']
    assert preview['_simulation']['selected_unit_ids'] == ['CHEAP']


def test_rt_reuses_frozen_simulated_submission_without_regeneration(monkeypatch):
    from smm.rolling import clear_window
    import smm.bid_simulation as simulation_module

    case, original_bids, preview = _frozen_merit_reversing_simulation()
    case.bids = copy.deepcopy(preview)
    case.stage = 'RT'
    def forbidden(*args, **kwargs):
        raise AssertionError('A frozen market submission must not be re-randomized in RT')
    monkeypatch.setattr(simulation_module, 'simulate_bids', forbidden)
    result = clear_window(case)
    assert result['model']._md.thermal['CHEAP']['c'] == [preview['units']['CHEAP']['segments'][0]['price']]
    assert result['snap']['P']['CHEAP'][0] == pytest.approx(0)
    assert result['snap']['P']['LOCAL'][0] == pytest.approx(100)
    np.testing.assert_allclose(result['prices']['lmp'][:, 0], [700, 700], atol=1e-5)
    assert case.bids == preview
    assert original_bids['units']['CHEAP']['segments'][0]['price'] == 690
