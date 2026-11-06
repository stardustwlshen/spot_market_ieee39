"""Independent counterexamples for input-to-cash-to-viewer consistency."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from smm.config import ROOT, filter_enabled, load_config
from smm.da import run_da
from smm.input_validation import validate_physical_inputs
from smm.rolling import snapshot
from smm.scenario import _copy_tree
from smm.settlement import settle
from smm.unit_editor import prepare_unit_edit, unit_template
from test_da_stages import tiny_case
from test_financial_archive_integrity import result
from settlement_validation import validate_saved_settlement
from final_views import settlement_identity


def test_zero_demand_interval_remains_an_explicit_readable_cash_record():
    case = tiny_case(reserve=0.)
    case.cfg.base['periods'] = 2
    case.load_bus = {2: np.array([0., 100.])}
    case.R_up = case.R_dn = np.zeros(2)
    da = run_da(case, verbose=False)
    sn = snapshot(da.model)
    rounds = []
    for t in (0, 1):
        part = deepcopy(sn)
        for key in ('P', 'u', 'Pw', 'Ph', 'Pd', 'Pc', 'Pg', 'Pp', 'load_bus', 'shed'):
            part[key] = {u: a[t:t+1] for u, a in sn[key].items()}
        part['surplus'] = sn['surplus'][t:t+1]
        prices = {k: v[:, t:t+1] if isinstance(v, np.ndarray) and v.ndim == 2 else v
                  for k, v in da.prices.items()}
        rounds.append(dict(t=t, snap=part, prices=prices))
    cash = settle(da, SimpleNamespace(rt=rounds))
    r = dict(summary={'dt_h': .25, 'expected_periods': 2},
             rt_prices=pd.DataFrame({'period': [0, 1]}),
             **{'settlement_'+k: v for k, v in cash.items()})
    load = cash['detail'].query("entity == 'L2'").set_index('period')
    assert set(load.index) == {0, 1}
    assert load.loc[0, ['q_da', 'q_rt', 'total']].eq(0.).all()
    assert cash['reconciliation'].residual.abs().max() < 1e-9
    validate_saved_settlement(r)
    assert settlement_identity(r)['通过（<1 元）'] is True


def test_equal_and_opposite_da_rt_corruption_is_not_an_identity_pass():
    r = result()
    for key, count in [('settlement_detail', 1), ('settlement_by_entity', 2), ('settlement_by_type', 4)]:
        r[key]['da_amount'] += 10000. * count
        r[key]['rt_amount'] -= 10000. * count
    with pytest.raises(ValueError, match='da_amount'):
        validate_saved_settlement(r)
    assert settlement_identity(r)['通过（<1 元）'] is False


def test_consistent_deviation_pnl_corruption_is_rejected():
    r = result()
    for key, count in [('settlement_detail', 1), ('settlement_by_entity', 2), ('settlement_by_type', 4)]:
        r[key]['dev_pnl'] += 10000. * count
    with pytest.raises(ValueError, match='dev_pnl'):
        validate_saved_settlement(r)


@pytest.mark.parametrize('field', ['q_da', 'q_rt', 'dev_mwh', 'dev_abs_mwh'])
def test_coherently_wrong_aggregate_energy_cannot_bypass_detail_link(field):
    r = result()
    r['settlement_by_entity'][field] = 10000.
    r['settlement_by_type'][field] = 20000.
    with pytest.raises(ValueError, match=field):
        validate_saved_settlement(r)


@pytest.mark.parametrize('uid', ['L3', '平衡松弛(过剩)', 'storage_leg', 'pumped_leg'])
def test_generated_settlement_names_cannot_be_resource_ids(uid):
    cfg = load_config()
    if uid == 'storage_leg':
        uid = cfg.units['storage'][0]['id'] + '_充电'
    elif uid == 'pumped_leg':
        uid = cfg.units['pumped'][0]['id'] + '_发电'
    cfg.units['thermal'][0]['id'] = uid
    with pytest.raises(ValueError, match='结算主体编号冲突'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('uid', ['L3', 'storage_leg'])
def test_web_unit_preview_rejects_cash_namespace_collision_without_writes(tmp_path, uid):
    root = tmp_path/'scene'
    _copy_tree(ROOT, root)
    if uid == 'storage_leg':
        uid = load_config(root=root).units['storage'][0]['id'] + '_充电'
    from smm.unit_editor import scene_fingerprint
    before = scene_fingerprint(root)
    with pytest.raises(ValueError, match='结算主体编号冲突'):
        prepare_unit_edit(root, 'thermal', unit_template('coal', uid, 31), reset_bid=True)
    assert scene_fingerprint(root) == before


@pytest.mark.parametrize('flag', ['false', 'true', '', 0, 1, None])
def test_enabled_flags_fail_before_filtering_and_in_direct_cases(flag):
    cfg = load_config()
    cfg.units['thermal'][0]['enabled'] = flag
    with pytest.raises(ValueError, match='enabled.*布尔'):
        filter_enabled(cfg.units)
    with pytest.raises(ValueError, match='enabled.*布尔'):
        validate_physical_inputs(cfg)


def test_real_boolean_disabled_resource_still_filters_normally():
    cfg = load_config()
    uid = cfg.units['thermal'][0]['id']
    cfg.units['thermal'][0]['enabled'] = False
    filtered = filter_enabled(cfg.units)
    assert uid in filtered['_disabled']
    assert uid not in {u['id'] for u in filtered['thermal']}
