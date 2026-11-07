"""Truthful selectable renewable input views, including all-period inspection."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from renewable_input_view import (STAGES, available_stages, renewable_figure,
                                  renewable_inputs, selected_curves)


@pytest.fixture
def inputs():
    from smm.time_grid import period_label
    cfg = SimpleNamespace(T=96, dt=.25, units={'renewable': [
        {'id': 'W1', 'type': 'wind', 'cap': 1000},
        {'id': 'W2', 'type': 'wind', 'cap': 1000},
        {'id': 'PV1', 'type': 'pv', 'cap': 1000},
        {'id': 'PV2', 'type': 'pv', 'cap': 1000},
        {'id': 'W_DISABLED', 'type': 'wind', 'cap': 1000, 'enabled': False},
    ]})
    profiles = pd.DataFrame({'period': range(1, 97), 'time': [period_label(t, cfg.dt) for t in range(96)]})
    for i, uid in enumerate(('W1', 'W2', 'PV1', 'PV2', 'NOT_IN_ROSTER', 'W_DISABLED'), 1):
        for k, stage in enumerate(('actual', 'da', 'id', 'rt')):
            profiles[f'{uid}_{stage}'] = np.arange(96, dtype=float) + i * 10 + k
    bids = {'units': {uid: {'avail_pmax': None} for uid in ('W1', 'W2', 'PV1', 'PV2')}}
    bids['units']['W1']['avail_pmax'] = list(np.arange(96, dtype=float) + 200)
    return cfg, profiles, bids


def test_totals_sum_each_period_exactly_once_without_unconfigured_or_disabled_resources(inputs):
    cfg, profiles, bids = inputs
    data = renewable_inputs(cfg, profiles, bids)
    table, warnings = selected_curves(data, kinds=['wind', 'pv'], stages=['actual', 'actual'])
    expected = profiles[['W1_actual', 'W2_actual', 'PV1_actual', 'PV2_actual']].sum(axis=1)
    assert table.shape == (96, 3) and not warnings
    np.testing.assert_array_equal(table.iloc[:, 2], expected)
    assert list(data.units) == ['W1', 'W2', 'PV1', 'PV2']
    assert table['时间'].tolist() == profiles.time.tolist()


def test_wind_pv_groups_and_selected_units_control_every_curve(inputs):
    cfg, profiles, bids = inputs
    data = renewable_inputs(cfg, profiles, bids)
    table, _ = selected_curves(data, kinds=['wind', 'pv'], stages=['da'], view='type')
    assert len(table.columns) == 4
    np.testing.assert_array_equal(table.iloc[:, 2], profiles.W1_da + profiles.W2_da)
    np.testing.assert_array_equal(table.iloc[:, 3], profiles.PV1_da + profiles.PV2_da)
    wind, _ = selected_curves(data, kinds=['wind'], stages=['id', 'rt'], view='unit', unit_ids=['W2', 'W2'])
    assert wind.shape == (96, 4)
    np.testing.assert_array_equal(wind.iloc[:, 2], profiles.W2_id)
    np.testing.assert_array_equal(wind.iloc[:, 3], profiles.W2_rt)
    subset, _ = selected_curves(data, kinds=['wind', 'pv'], stages=['actual'], unit_ids=['W2', 'PV2'])
    assert '所选' in subset.columns[2]
    np.testing.assert_array_equal(subset.iloc[:, 2], profiles.W2_actual + profiles.PV2_actual)


@pytest.mark.parametrize('change', ['duplicate_id', 'unknown_type', 'duplicate_column', 'duplicate_time', 'missing_time', 'duplicate_period', 'short'])
def test_ambiguous_resource_or_time_grid_fails_explicitly(inputs, change):
    cfg, profiles, bids = inputs
    if change == 'duplicate_id':
        cfg.units['renewable'].append(copy.deepcopy(cfg.units['renewable'][0]))
    elif change == 'unknown_type':
        cfg.units['renewable'][0]['type'] = 'unknown'
    elif change == 'duplicate_column':
        profiles = pd.concat([profiles, profiles[['W1_da']]], axis=1)
    elif change == 'duplicate_time':
        profiles.loc[1, 'time'] = profiles.loc[0, 'time']
    elif change == 'missing_time':
        profiles = profiles.drop(columns='time')
    elif change == 'duplicate_period':
        profiles.loc[1, 'period'] = 1
    else:
        profiles = profiles.iloc[:-1]
    with pytest.raises(ValueError):
        renewable_inputs(cfg, profiles, bids)


def test_missing_column_and_nan_do_not_make_partial_totals_or_zero(inputs):
    cfg, profiles, bids = inputs
    profiles = profiles.drop(columns='W2_da')
    profiles.loc[7, 'PV1_actual'] = np.nan
    data = renewable_inputs(cfg, profiles, bids)
    table, warnings = selected_curves(data, kinds=['wind', 'pv'], stages=['actual', 'da'])
    assert pd.isna(table.iloc[7, 2]) and table.iloc[:, 3].isna().all()
    assert table.iloc[:, 2].notna().sum() == 95
    assert any('W2' in msg and '缺失' in msg for msg in data.warnings)
    assert len(warnings) == 2
    pv, warnings = selected_curves(data, kinds=['pv'], stages=['da'])
    assert not warnings and pv.iloc[:, 2].notna().all()


@pytest.mark.parametrize('bad', [np.inf, -1, 1001, True, 'invalid'])
def test_invalid_values_are_explicit_gaps_not_clipped_or_fabricated(inputs, bad):
    cfg, profiles, bids = inputs
    profiles['W1_actual'] = profiles.W1_actual.astype(object)
    profiles.loc[0, 'W1_actual'] = bad
    data = renewable_inputs(cfg, profiles, bids)
    assert pd.isna(data.curves['W1', 'actual'].iloc[0])
    assert data.warnings


def test_quote_priority_preserves_original_curves_and_input_objects(inputs):
    cfg, profiles, bids = inputs
    before, bid_before, cfg_before = profiles.copy(deep=True), copy.deepcopy(bids), copy.deepcopy(cfg.units)
    data = renewable_inputs(cfg, profiles, bids)
    table, warnings = selected_curves(data, kinds=['wind'], stages=['da', 'declared', 'effective_da'])
    np.testing.assert_array_equal(table.iloc[:, 2], profiles.W1_da + profiles.W2_da)
    assert table.iloc[:, 3].isna().all()  # W2 did not declare, not a zero declaration.
    np.testing.assert_array_equal(table.iloc[:, 4], np.array(bids['units']['W1']['avail_pmax']) + profiles.W2_da)
    assert warnings and not data.warnings
    pd.testing.assert_frame_equal(profiles, before)
    assert bids == bid_before and cfg.units == cfg_before


def test_missing_quote_context_has_no_fabricated_effective_or_declared_stage(inputs):
    cfg, profiles, _ = inputs
    data = renewable_inputs(cfg, profiles)
    assert available_stages(data, list(data.units)) == ['actual', 'da', 'id', 'rt']
    assert not data.quote_known


def test_explicit_zero_declaration_is_preserved(inputs):
    cfg, profiles, bids = inputs
    bids['units']['W1']['avail_pmax'] = [0.] * cfg.T
    data = renewable_inputs(cfg, profiles, bids)
    assert data.curves['W1', 'declared'].eq(0).all()
    assert data.curves['W1', 'effective_da'].eq(0).all()
    assert data.curves['W1', 'da'].gt(0).all()


@pytest.mark.parametrize('malformed', [[], [3.] * 95, [True] * 96, [np.nan] * 96])
def test_invalid_quote_never_silently_falls_back_to_da(inputs, malformed):
    cfg, profiles, bids = inputs
    bids['units']['W1']['avail_pmax'] = malformed
    data = renewable_inputs(cfg, profiles, bids)
    assert data.curves['W1', 'effective_da'].isna().all()
    assert data.warnings


def test_missing_quote_resource_does_not_fall_back(inputs):
    cfg, profiles, bids = inputs
    bids['units'].pop('W2')
    data = renewable_inputs(cfg, profiles, bids)
    assert data.curves['W2', 'effective_da'].isna().all()
    assert any('W2' in msg and '报价缺失' in msg for msg in data.warnings)


def test_unavailable_stages_are_not_offered(inputs):
    cfg, profiles, bids = inputs
    profiles = profiles.drop(columns=[c for c in profiles if c.endswith(('_id', '_rt'))])
    data = renewable_inputs(cfg, profiles, bids)
    assert 'id' not in available_stages(data, ['W1']) and 'rt' not in available_stages(data, ['W1'])
    assert 'declared' not in available_stages(data, ['W2', 'PV1'])


def test_shared_hover_keeps_all_96_points_on_every_selected_trace(inputs):
    data = renewable_inputs(*inputs)
    table, _ = selected_curves(data, kinds=['wind', 'pv'], stages=['actual', 'da', 'id', 'rt'], view='unit')
    fig = renewable_figure(table)
    assert len(fig.data) == 16
    assert fig.layout.hovermode == 'x unified' and fig.layout.hoverdistance == -1
    assert fig.layout.hoverlabel.namelength == -1
    assert fig.layout.xaxis.spikemode == 'across'
    assert fig.layout.yaxis.title.text == '可用功率（MW）'
    for i, trace in enumerate(fig.data, 2):
        assert list(trace.x) == table['时间'].tolist() and len(trace.y) == 96
        np.testing.assert_array_equal(trace.y, table.iloc[:, i])
        assert '%{fullData.name}' in trace.hovertemplate and 'MW' in trace.hovertemplate
        assert trace.connectgaps is False and trace.visible is None
    assert '实际可用功率' in fig.data[0].name and '实际发电' not in fig.data[0].name
    assert '日内预测基线' in fig.data[2].name and '实时预测基线' in fig.data[3].name


def render_app():
    import streamlit as st
    from renewable_input_view import render_renewable_inputs
    render_renewable_inputs(st.session_state['cfg'], st.session_state['profiles'], bids=st.session_state['bids'], key_prefix='test')


def ui(inputs):
    cfg, profiles, bids = inputs
    at = AppTest.from_function(render_app)
    at.session_state['cfg'], at.session_state['profiles'], at.session_state['bids'] = cfg, profiles, bids
    return at.run()


def figure(at):
    return json.loads(at.get('plotly_chart')[0].proto.spec)


def test_ui_controls_really_filter_and_table_exposes_all_periods(inputs):
    at = ui(inputs)
    assert not at.exception and not at.error
    assert len(figure(at)['data']) == 2  # concise default total: actual + effective DA
    assert not at.dataframe
    at.multiselect(key='test:kinds').set_value(['pv']).run()
    at.radio(key='test:view').set_value('unit').run()
    at.multiselect(key='test:units').set_value(['PV2']).run()
    at.multiselect(key='test:stages').set_value(['actual', 'id', 'rt']).run()
    at.checkbox(key='test:table').check().run()
    assert not at.exception and not at.error
    spec = figure(at)
    assert len(spec['data']) == 3 and all(trace['name'].startswith('PV2 · 光伏') for trace in spec['data'])
    assert all(len(trace['x']) == len(trace['y']) == 96 for trace in spec['data'])
    table = at.dataframe[0].value
    assert table.shape == (96, 5) and table['时段'].tolist() == list(range(1, 97))
    np.testing.assert_array_equal(table.iloc[:, 2], inputs[1].PV2_actual)
    np.testing.assert_array_equal(table.iloc[:, 3], inputs[1].PV2_id)
    assert any('提前量插值' in item.value for item in at.caption)


def test_ui_empty_choice_stays_empty_and_changed_type_drops_stale_units(inputs):
    at = ui(inputs)
    at.radio(key='test:view').set_value('unit').run()
    at.multiselect(key='test:units').set_value(['W1']).run()
    at.multiselect(key='test:kinds').set_value(['pv']).run()
    assert not at.exception
    assert at.multiselect(key='test:units').value == [] and not at.get('plotly_chart')
    at.multiselect(key='test:units').set_value(['PV1']).run()
    at.multiselect(key='test:stages').set_value(['id']).run()
    assert len(figure(at)['data']) == 1
    at.multiselect(key='test:stages').set_value([]).run()
    assert not at.exception and not at.get('plotly_chart')
    at.run()
    assert at.multiselect(key='test:stages').value == []


def test_ui_no_resources_or_data_is_explicit(inputs):
    cfg, profiles, bids = inputs
    cfg.units['renewable'] = []
    at = ui(inputs)
    assert not at.exception and not at.get('plotly_chart')
    assert any('没有启用' in x.value for x in at.info)


def test_both_entry_points_pass_current_working_quote():
    for filename in ('app/single_day_workspace.py', 'app/pages/2_运行.py'):
        import ast
        tree = ast.parse((ROOT / filename).read_text(encoding='utf8'))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'render_network_and_units']
        assert calls and all(any(k.arg == 'bids' and isinstance(k.value, ast.Name) and k.value.id == 'draft'
                                 for k in call.keywords) for call in calls)


def test_uploaded_profiles_and_current_quote_match_real_da_input_without_writes(tmp_path):
    from smm.bids import default_bids, effective_bids
    from smm.case import build_da_case
    from smm.config import load_config
    from smm.forecast import apply_profile_overrides, generate_profiles
    from smm.network import build_network
    import shutil

    cfg = copy.deepcopy(load_config())
    cfg.root = tmp_path
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    shutil.copytree(ROOT / 'data/case39', tmp_path / 'data/case39')
    overrides = pd.DataFrame({'W1_actual': [111.] * cfg.T, 'W1_da': [112.] * cfg.T,
                              'W1_id': [113.] * cfg.T, 'W1_rt': [114.] * cfg.T})
    overrides.to_csv(tmp_path / 'data/profile_overrides.csv', index=False)
    bids = default_bids(cfg)
    bids['units']['W1']['avail_pmax'] = [222.] * cfg.T
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    bid_before = copy.deepcopy(bids)
    profiles = apply_profile_overrides(cfg, generate_profiles(cfg))
    data = renewable_inputs(cfg, profiles, bids)
    case = build_da_case(cfg, build_network(cfg), profiles, effective_bids(cfg, bids))
    table, warnings = selected_curves(data, kinds=['wind', 'pv'], stages=['effective_da'])
    assert not warnings
    np.testing.assert_array_equal(table.iloc[:, 2], sum(case.ren_fc.values()))
    assert data.curves['W1', 'actual'].eq(111).all()
    assert data.curves['W1', 'da'].eq(112).all()
    assert data.curves['W1', 'id'].eq(113).all()
    assert data.curves['W1', 'rt'].eq(114).all()
    assert data.curves['W1', 'effective_da'].eq(222).all()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert bids == bid_before
