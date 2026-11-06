"""Unit overview reads saved artifacts, keeps stage scope and never invents profit."""
from copy import deepcopy
from pathlib import Path
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from unit_overview import build_unit_overview, entity_names, render_unit_overview, unit_catalog, POWER, PRICE

ARCHIVE = Path(os.environ.get('IEEE39_SAVED_RUN_FOR_UI', str(ROOT / 'outputs/full_C_cost_v5_MKT_wet_id_on')))


def sample(kind='coal', uid='G', periods=4, executed=4, intraday=True):
    da = pd.DataFrame([dict(unit=uid, type=kind, bus=1, period=t, P_mw=10., on=1, energy_mwh=2.5, revenue_yuan=50.) for t in range(periods)])
    rt = da.iloc[:executed].copy(); rt.P_mw = 12.
    prices = pd.DataFrame([dict(bus=1, period=t, lmp=20., lmp_raw=21., dispatch_raw_lmp=23., pricing_source='bounded_auxiliary_pricing') for t in range(periods)])
    names = entity_names(uid, kind)
    settlement = pd.DataFrame([dict(entity=name, period=t, da_amount=50. / len(names), rt_amount=10. / len(names), total=60. / len(names)) for t in range(executed) for name in names])
    detail = pd.DataFrame([dict(unit=uid, type=kind, period=t, cost_available=True, cost_source_kind='research_assumption_not_observed_plant_data', da_cash_revenue_yuan=50., rt_deviation_cash_revenue_yuan=10., cash_revenue_yuan=60., variable_cost_yuan=65., fixed_online_cost_yuan=1., startup_cost_yuan=0., cash_operating_cost_yuan=66., simulated_operating_profit_yuan=-6.) for t in range(executed)])
    sums = {k: detail[k].sum() for k in detail if k.endswith('_yuan')}
    summary = pd.DataFrame([dict(unit=uid, type=kind, cost_available=True, cost_source_kind='research_assumption_not_observed_plant_data', **sums)])
    result = dict(summary={'expected_periods': periods, 'dt_h': .25}, da_unit_awards=da, rt_units=rt, da_nodal_prices=prices, rt_prices=prices.iloc[:executed], settlement_detail=settlement, operating_profit_by_unit=summary, operating_profit_by_period=detail)
    if intraday:
        result['id_rounds_summary'] = pd.DataFrame([dict(tau=0, round=1), dict(tau=2, round=2)])
        result['id_plans'] = pd.concat([da.assign(round=1, tau=0), da[da.period >= 2].assign(round=2, tau=2, P_mw=11.)])
        result['id_prices_indicative'] = pd.concat([prices.assign(round=1), prices[prices.period >= 2].assign(round=2, lmp=19.)])
    return result


def test_cross_stage_same_period_partial_id_and_realized_scope():
    result = sample(executed=3)
    data = build_unit_overview(result, 'G', id_tau=2)
    f = data['comparison']
    assert f[POWER['da']].tolist() == [10.] * 4
    assert f[POWER['id']].isna().tolist() == [True, True, False, False]
    assert f[POWER['rt']].isna().tolist() == [False, False, False, True]
    assert f['实时−日前 MW'].iloc[:3].tolist() == [2.] * 3
    assert f[PRICE['id']].iloc[2] == 19.
    assert len(data['cash']) == 3 and len(data['profit_period']) == 3
    assert data['profit'].simulated_operating_profit_yuan.iloc[0] == -18.


@pytest.mark.parametrize('kind', ['coal', 'gas_ccgt', 'gas_gt', 'wind', 'pv', 'storage', 'hydro', 'pumped', 'tie'])
def test_all_nine_types_are_readable_and_tie_excluded(kind):
    result = sample(kind)
    data = build_unit_overview(result, 'G', id_tau=0)
    assert len(data['stage_rows']['da']) == 4 and len(data['stage_rows']['rt']) == 4
    assert data['profit'].empty == (kind == 'tie')
    assert len(data['cash']) == 4


def test_exact_entities_do_not_match_similar_identifier():
    result = sample('storage', 'ES1')
    extra = result['settlement_detail'].assign(entity='ES1_fake', total=999999., da_amount=999999., rt_amount=0.)
    result['settlement_detail'] = pd.concat([result['settlement_detail'], extra])
    data = build_unit_overview(result, 'ES1')
    assert data['cash'].cash_revenue_yuan.sum() == 240.
    assert data['cash'].charging_settlement_yuan.sum() == 120.


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_scope', 'nan'])
def test_broken_cash_is_not_silently_summed(damage):
    result = sample('storage')
    cash = result['settlement_detail']
    if damage == 'missing': cash = cash.iloc[1:]
    elif damage == 'duplicate': cash = pd.concat([cash, cash.iloc[[0]]])
    elif damage == 'wrong_scope': cash = pd.concat([cash, cash.iloc[[0]].assign(period=9)])
    else: cash = cash.copy(); cash.loc[0, 'total'] = np.nan
    result['settlement_detail'] = cash
    data = build_unit_overview(result, 'G')
    assert data['cash'].empty and any('现金结算不可用' in x for x in data['notices'])


def test_missing_cost_stays_unknown_with_cash_still_present():
    result = sample()
    result['operating_profit_by_unit']['cost_available'] = False
    result['operating_profit_by_period']['cost_available'] = False
    data = build_unit_overview(result, 'G')
    assert data['profit'].simulated_operating_profit_yuan.isna().all()
    assert data['profit_period'].cash_operating_cost_yuan.isna().all()
    assert data['cash'].cash_revenue_yuan.sum() == 240.


@pytest.mark.parametrize('key', ['operating_profit_by_unit', 'operating_profit_by_period'])
def test_corrupt_profit_does_not_disable_physical_result(key):
    result = sample(); result[key].loc[0, 'simulated_operating_profit_yuan'] = np.inf
    data = build_unit_overview(result, 'G')
    assert len(data['stage_rows']['rt']) == 4 and not data['cash'].empty
    assert data['profit' if key.endswith('unit') else 'profit_period'].empty


def test_profit_missing_rows_and_disagreement_are_not_complete():
    result = sample(); result['operating_profit_by_period'] = result['operating_profit_by_period'].iloc[:-1]
    data = build_unit_overview(result, 'G'); assert data['profit_period'].empty
    result = sample(); result['operating_profit_by_unit']['cash_revenue_yuan'] += 100.
    result['operating_profit_by_unit']['simulated_operating_profit_yuan'] += 100.
    data = build_unit_overview(result, 'G'); assert data['profit'].empty


def test_no_id_and_da_only_dont_create_realized_cash_or_profit():
    result = sample(intraday=False)
    result['rt_units'] = pd.DataFrame(); result['rt_prices'] = pd.DataFrame()
    for key in ['settlement_detail', 'operating_profit_by_unit', 'operating_profit_by_period']:
        result.pop(key)
    data = build_unit_overview(result, 'G')
    assert data['comparison'][POWER['rt']].isna().all()
    assert data['comparison'][POWER['id']].isna().all()
    assert data['cash'].empty and data['profit'].empty
    assert len(data['stage_rows']['da']) == 4


def test_failed_id_does_not_reuse_old_plan():
    result = sample(); result['summary']['intraday'] = {'failed_rounds': [dict(tau=2, reason='failed')]}
    data = build_unit_overview(result, 'G', id_tau=2)
    assert data['stage_rows']['id'].empty
    assert data['comparison'][POWER['id']].isna().all()
    assert any('失败' in x for x in data['notices'])


def test_invalid_optional_id_does_not_reuse_another_round():
    result = sample(); result['id_plans'].loc[result['id_plans']['round'].eq(2), 'tau'] = 0
    data = build_unit_overview(result, 'G', id_tau=2)
    assert data['stage_rows']['id'].empty
    assert data['comparison'][PRICE['id']].isna().all()


def test_hydro_station_count_comes_from_hydro_table_not_binary_stage_on():
    result = sample('hydro', 'H1')
    result['rt_hydro'] = pd.DataFrame([dict(station='H1', period=t, units_on=4, V_start=100., V_1e4m3=101., Q_in=20., Q_gen=15., Q_spill=0., Q_out=15.) for t in range(4)])
    diag = {'id_rounds': [dict(tau=0, stages={'energy_sced': {'units': [dict(unit='H1', period=0, on=1)], 'hydro_states': [dict(station='H1', period=0, V_1e4m3=102., Qout_m3s=16.)]}})]}
    data = build_unit_overview(result, 'H1', id_tau=0, diagnostics=diag)
    assert data['states']['rt'].units_on.eq(4).all()
    assert 'on' not in data['states']['id'] and 'units_on' not in data['states']['id']
    assert data['states']['id'].Qout_m3s.iloc[0] == 16.


def test_all_inputs_unchanged():
    result = sample(); before = deepcopy(result)
    build_unit_overview(result, 'G', id_tau=2)
    for key, value in before.items():
        if isinstance(value, pd.DataFrame): pd.testing.assert_frame_equal(value, result[key])
        else: assert value == result[key]


@pytest.fixture(scope='module')
def real():
    if not ARCHIVE.is_dir(): pytest.skip('independent real C96 archive unavailable')
    from common import get_result
    result = get_result(dict(dir=str(ARCHIVE)))
    return result, json.loads((ARCHIVE / 'stage_diagnostics.json').read_text())


def test_real_c96_all_18_subjects_and_all_profit_periods(real):
    result, diagnostics = real
    catalog = unit_catalog(result); assert len(catalog) == 18 and catalog.type.nunique() == 9
    for uid in catalog.unit:
        data = build_unit_overview(result, uid, id_tau=0, diagnostics=diagnostics)
        assert not data['notices'], (uid, data['notices'])
        assert len(data['stage_rows']['da']) == len(data['stage_rows']['rt']) == 96
        assert len(data['cash']) == 96
        if uid != 'TIE':
            assert len(data['stage_rows']['id']) == 96 and len(data['profit_period']) == 96
            assert len(data['profit']) == 1 and data['profit'].cost_available.all()
        else:
            assert data['stage_rows']['id'].empty and data['profit'].empty


def test_real_ui_all_resource_types_and_first_middle_last_id_round(real):
    result, diagnostics = real
    def app():
        import streamlit as st
        from unit_overview import render_unit_overview
        render_unit_overview(st.session_state['result'], diagnostics=st.session_state['diag'], prefix='audit')
    at = AppTest.from_function(app, default_timeout=90)
    at.session_state['result'] = result; at.session_state['diag'] = diagnostics; at.run()
    assert not at.exception
    for uid in ['C1', 'CCGT', 'GT', 'W1', 'PV1', 'H1', 'ES1', 'PS1', 'TIE']:
        at.selectbox(key='audit:unit').select(uid).run()
        assert not at.exception, [x.message for x in at.exception]
    assert any('不纳入当前核算' in x.value for x in at.caption)
    at.selectbox(key='audit:unit').select('H1')
    for tau in [0, 48, 92]:
        at.selectbox(key='audit:id_tau').select(tau).run()
        assert not at.exception
        assert any('电站聚合主体' in x.value for x in at.info)
    at.selectbox(key='audit:unit').select('PS1').run()
    assert any(m.label == '模拟运营利润 · 元' and m.value.startswith('-') for m in at.metric)
    assert any('固定成本' in x.value or '成本' in x.value for x in at.caption)


def test_profit_wrong_resource_type_is_not_relabelled():
    result = sample(); result['operating_profit_by_unit']['type'] = 'storage'
    data = build_unit_overview(result, 'G')
    assert data['profit'].empty and not data['cash'].empty


def test_bad_id_round_does_not_hide_da_rt():
    result = sample(); result['id_rounds_summary'].loc[0, 'tau'] = 999
    data = build_unit_overview(result, 'G', id_tau=999)
    assert not data['stage_rows']['da'].empty and not data['stage_rows']['rt'].empty
    assert data['stage_rows']['id'].empty
    assert any('日内轮次不可用' in x for x in data['notices'])


def test_no_actual_execution_never_displays_stale_profit():
    result = sample(); result['rt_units'] = pd.DataFrame()
    data = build_unit_overview(result, 'G')
    assert data['profit'].empty and data['profit_period'].empty and data['cash'].empty


@pytest.mark.parametrize('variant', ['no_id', 'da_only', 'missing_cost', 'missing_states', 'bad_profit', 'bad_id'])
def test_optional_evidence_variants_render_without_exception(variant):
    result = sample('hydro', 'H1', intraday=variant != 'no_id')
    if variant == 'da_only':
        for key in ['rt_units', 'rt_prices', 'settlement_detail', 'operating_profit_by_unit', 'operating_profit_by_period']:
            result.pop(key, None)
    elif variant == 'missing_cost':
        for key in ['operating_profit_by_unit', 'operating_profit_by_period']:
            result[key]['cost_available'] = False
    elif variant == 'bad_profit': result['operating_profit_by_unit']['simulated_operating_profit_yuan'] = np.nan
    elif variant == 'bad_id': result['id_rounds_summary']['tau'] = 999
    def app():
        import streamlit as st
        from unit_overview import render_unit_overview
        render_unit_overview(st.session_state['result'], prefix='variant')
    at = AppTest.from_function(app, default_timeout=30)
    at.session_state['result'] = result; at.run()
    assert not at.exception, [x.message for x in at.exception]
    if variant == 'da_only':
        assert any(m.label == '日前中标计划金额（非实际利润）· 元' for m in at.metric)
        assert not any(m.label == '模拟运营利润 · 元' for m in at.metric)
    if variant == 'missing_cost':
        assert any(m.label == '模拟运营利润 · 元' and m.value == '未提供' for m in at.metric)
    at.selectbox(key='variant:unit').select('H1').run()
    assert not at.exception


def test_lone_legacy_lmp_remains_unknown_not_settlement():
    result = sample()
    for key in ['da_nodal_prices', 'rt_prices']:
        result[key] = result[key][['period', 'bus', 'lmp']]
    data = build_unit_overview(result, 'G')
    for stage in ['da', 'rt']:
        assert '口径未记录' in data['price_labels'][stage]
        assert PRICE[stage] not in data['comparison']
        assert data['comparison'][data['price_labels'][stage]].eq(20.).all()
    assert any('不推定' in x for x in data['notices'])


def test_explicit_clamped_price_wins_over_raw_lmp_without_reclamping():
    result = sample()
    for key in ['da_nodal_prices', 'rt_prices']:
        result[key] = result[key][['period', 'bus', 'lmp']].assign(lmp=9999., lmp_clamped=3000.)
    data = build_unit_overview(result, 'G')
    assert data['comparison'][PRICE['da']].eq(3000.).all()
    assert data['price_sources']['da']['原始节点价格 元/MWh'].eq(9999.).all()


def test_saved_clipped_source_and_raw_export_contract_identify_prices():
    result = sample()
    result['rt_prices'] = result['rt_prices'][['period', 'bus', 'lmp']].assign(settlement_price_source='bounded_auxiliary_pricing_clipped')
    data = build_unit_overview(result, 'G')
    assert data['price_labels']['rt'] == PRICE['rt']
    assert data['price_labels']['da'] == PRICE['da']  # saved lmp_raw export schema
    assert data['comparison'][PRICE['rt']].eq(20.).all()


def test_legacy_price_unknown_label_survives_ui_localization():
    result = sample()
    result['da_nodal_prices'] = result['da_nodal_prices'][['period', 'bus', 'lmp']]
    def app():
        import streamlit as st
        from unit_overview import render_unit_overview
        render_unit_overview(st.session_state['result'], prefix='legacy_price')
    at = AppTest.from_function(app, default_timeout=30)
    at.session_state['result'] = result; at.run()
    assert not at.exception
    assert any(any('日前保存价格（口径未记录）' in str(c) for c in d.value.columns) for d in at.dataframe)
    assert any('不推定' in w.value for w in at.warning)
