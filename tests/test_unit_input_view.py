"""Complete selected-unit input display, with no writes or inferred zero costs."""
import copy
import sys
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from unit_input_view import field_rows, unit_information_sections


@pytest.fixture
def cfg(tmp_path):
    from smm.config import load_config
    config = load_config()
    config = copy.deepcopy(config)
    config.root = tmp_path
    (tmp_path / 'config').mkdir()
    for filename in ('fixed_costs.yaml', 'bid_cost_basis.yaml', 'units.yaml'):
        (tmp_path / 'config' / filename).write_bytes((ROOT / 'config' / filename).read_bytes())
    return config


@pytest.mark.parametrize('group,uid', [('thermal', 'C1'), ('renewable', 'W1'), ('hydro', 'H1'),
                                      ('hydro', 'H2'), ('storage', 'ES1'), ('pumped', 'PS1'), ('tie', 'TIE')])
def test_every_selected_unit_field_is_preserved(cfg, group, uid):
    unit = next(x for x in yaml.safe_load((cfg.root / 'config/units.yaml').read_text())[group] if x['id'] == uid)
    before = copy.deepcopy(cfg.units)
    sections = unit_information_sections(cfg, uid)
    actual = {row['原始字段']: row['数值'] for title, rows in sections.items()
              if title in ('主体信息', '技术与运行约束', '初始状态', '季节约束', '技术档案费用参数（不是独立核算成本）')
              for row in rows}
    expected = {row['原始字段']: row['数值'] for row in field_rows(unit)}
    assert all(actual[key] == value for key, value in expected.items())
    assert cfg.units == before
    assert actual['enabled'] == ('是' if unit.get('enabled', True) else '否')


def test_unknown_nested_fields_null_and_empty_containers_survive(cfg):
    unit = cfg.units['thermal'][0]
    unit['future_constraint'] = {'custom_bounds': [1.23456789012345, None, {'new_key': False}], 'empty': [], 'map': {}}
    sections = unit_information_sections(cfg, unit['id'])
    rows = sections['技术与运行约束']
    by_path = {row['原始字段']: row for row in rows}
    assert by_path['future_constraint.custom_bounds.[0]']['数值'] == '1.23456789012345'
    assert by_path['future_constraint.custom_bounds.[1]']['数值'] == '未设置（null）'
    assert by_path['future_constraint.custom_bounds.[2].new_key']['数值'] == '否'
    assert by_path['future_constraint.empty']['数值'] == '[]'
    assert by_path['future_constraint.map']['数值'] == '{}'
    assert 'future_constraint' in by_path['future_constraint.empty']['参数']


@pytest.mark.parametrize('uid', ['C1', 'W1', 'H1', 'ES1', 'PS1'])
def test_costs_are_selected_unit_only_and_source_values_are_complete(cfg, uid):
    sections = unit_information_sections(cfg, uid)
    fixed = yaml.safe_load((cfg.root / 'config/fixed_costs.yaml').read_text())
    expected = field_rows(fixed['units'][uid])
    assert sections['原成本档案（报价依据，非本次现货核算）'] == expected
    basis = yaml.safe_load((cfg.root / 'config/bid_cost_basis.yaml').read_text())
    group = next(group for group, units in cfg.units.items() if isinstance(units, list) and any(isinstance(u, dict) and u['id'] == uid for u in units))
    source_group = 'renewable_vom' if group == 'renewable' else group
    raw = basis[source_group][uid]
    assert sections['报价生成成本依据（不等于核算成本）'] == field_rows(raw if isinstance(raw, dict) else {source_group: raw})
    serialized = str(sections)
    assert 'C2' not in serialized and 'ES2' not in serialized
    if uid == 'C1':
        assert any('非独立核算成本' in row['参数'] for row in sections['技术档案费用参数（不是独立核算成本）'])


@pytest.mark.parametrize('missing', ['file', 'unit', 'invalid'])
def test_missing_costs_remain_unknown_never_zero(cfg, missing):
    for filename in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
        path = cfg.root / 'config' / filename
        if missing == 'file':
            path.unlink()
        elif missing == 'unit':
            path.write_text('{}')
        else:
            path.write_text('[broken YAML')
    sections = unit_information_sections(cfg, 'C1')
    for title in ('原成本档案（报价依据，非本次现货核算）', '报价生成成本依据（不等于核算成本）'):
        assert all('未知' in row['数值'] for row in sections[title])
        assert not any(row['数值'] == '0' for row in sections[title])
    assert sections['技术档案费用参数（不是独立核算成本）']


def test_render_read_only_and_never_writes_parameters(cfg):
    paths = list((cfg.root / 'config').glob('*'))
    before = {path: path.read_bytes() for path in paths}
    units_before = copy.deepcopy(cfg.units)

    def app():
        import streamlit as st
        from unit_input_view import render_unit_information
        render_unit_information(st.session_state['cfg'], 'H2')

    at = AppTest.from_function(app)
    at.session_state['cfg'] = cfg
    at.run()
    assert not at.exception
    assert not at.button and not at.number_input and not at.text_input and not at.text_area
    assert len(at.dataframe) >= 5
    data = [element.value for element in at.dataframe]
    assert any('否' in table['数值'].tolist() for table in data)
    assert all(list(table.columns) == ['参数', '数值'] for table in data)
    assert any('季节约束 / 丰水期 / 初始库容（万m³）' in table['参数'].tolist() for table in data)
    assert {path: path.read_bytes() for path in paths} == before
    assert cfg.units == units_before


def test_unknown_unit_does_not_show_another_unit(cfg):
    with pytest.raises(ValueError, match='不存在'):
        unit_information_sections(cfg, 'NO_SUCH_UNIT')


def test_quote_parameter_labels_preserve_historical_startup_fields():
    from unit_input_view import parameter_rows
    quote = {'mode': 'quantity_only', 'startup_cost': {'cold': 60, 'warm': 50, 'hot': 40, 'very_hot': 30},
             'startup_cost_yuan': 40, 'startup_cost_mode': 'uniform_per_unit',
             'startup_cost_source': 'legacy_hot_migration', 'avail_pmax': None,
             'self_schedule': [1, 2], 'future_mode': 'future_value'}
    rows = field_rows(quote)
    assert len([r for r in rows if r['原始字段'].startswith('startup_cost.')]) == 4
    assert any(r['数值'] == '只报量不报价' for r in rows)
    assert any(r['数值'] == '沿用该机组历史热态启动报价' for r in rows)
    assert any(r['数值'] == 'future_value' for r in rows)
    assert all(set(r) == {'参数', '数值'} for r in parameter_rows(quote))


@pytest.fixture
def network_cfg(cfg):
    import shutil
    shutil.copytree(ROOT / 'data/case39', cfg.root / 'data/case39')
    return cfg


def network_app():
    import streamlit as st
    from unit_input_view import render_network_and_units
    render_network_and_units(st.session_state['cfg'], key_prefix='test-network')


def test_network_capacity_separates_disabled_and_storage_energy(network_cfg):
    from unit_input_view import resource_capacity_rows, scenario_unit_catalog
    rows = resource_capacity_rows(network_cfg)
    hydro = [r for r in rows if r['资源类型'] == '水电']
    assert {r['状态']: r['发电/放电容量（MW）'] for r in hydro} == {'启用': 508., '未启用（不参与运行）': 100.}
    storage = next(r for r in rows if r['资源类型'] == '储能')
    assert storage['储能额定能量（MWh）'] == 2000.
    assert storage['发电/放电容量（MW）'] == 700.
    pumped = next(r for r in rows if r['资源类型'] == '抽水蓄能')
    assert pumped['发电/放电容量（MW）'] == pumped['充电/抽水容量（MW）'] == 600.
    assert pumped['抽蓄有效库容发电容量（MWh）'] == 3744.
    assert any(unit['id'] == 'H2' and not unit['enabled'] for _, unit in scenario_unit_catalog(network_cfg))


@pytest.mark.parametrize('tie_mode,actual_peak,da_peak', [('supply','3,000.00','3,100.00'), ('fixed_load','4,045.00','4,145.00')])
def test_network_view_uses_overrides_and_never_writes(network_cfg,tie_mode,actual_peak,da_peak):
    network_cfg.base['tie_line_mode'] = tie_mode
    import pandas as pd
    from smm.forecast import generate_profiles, apply_profile_overrides
    overrides = pd.DataFrame({'load_actual': [3000.] * network_cfg.T, 'load_da': [3100.] * network_cfg.T,
                              'W1_actual': [111.] * network_cfg.T, 'W1_da': [112.] * network_cfg.T})
    overrides.to_csv(network_cfg.root / 'data/profile_overrides.csv', index=False)
    before = {path: path.read_bytes() for path in network_cfg.root.rglob('*') if path.is_file()}
    units_before = copy.deepcopy(network_cfg.units)
    at = AppTest.from_function(network_app)
    at.session_state['cfg'] = network_cfg
    at.run()
    assert not at.exception and not at.error
    metrics = {item.label: item.value for item in at.metric}
    assert metrics['节点数量'] == '39' and metrics['支路数量'] == '46'
    assert metrics['实际负荷峰值（MW）'] == actual_peak
    assert metrics['日前预测负荷峰值（MW）'] == da_peak
    if tie_mode == 'supply':
        assert any('供给注入，未加到负荷' in item.value for item in at.caption)
    assert len(at.get('vega_lite_chart')) == 2
    assert len(at.get('plotly_chart')) == 2  # topology and selectable renewable input curves
    assert not at.button and not at.number_input and not at.text_input
    selector = next(item for item in at.selectbox if item.label == '查看其他机组参数')
    assert any('H2' in label and '未启用' in label for label in selector.options)
    selector.select('H2').run()
    assert not at.exception and any('H2' in item.value for item in at.subheader)
    assert any('季节约束 / 丰水期 / 初始库容（万m³）' in item.value['参数'].tolist()
               for item in at.dataframe if '参数' in item.value)
    assert {path: path.read_bytes() for path in network_cfg.root.rglob('*') if path.is_file()} == before
    assert network_cfg.units == units_before


def test_invalid_network_profile_never_falls_back_and_peer_catalog_still_available(network_cfg):
    (network_cfg.root / 'data/profile_overrides.csv').write_text('load_actual\n-1\n')
    at = AppTest.from_function(network_app)
    at.session_state['cfg'] = network_cfg
    at.run()
    assert not at.exception
    assert any('曲线暂不能读取' in item.value for item in at.error)
    assert not at.get('vega_lite_chart')
    assert not any('负荷峰值' in item.label for item in at.metric)
    assert any(item.label == '查看其他机组参数' for item in at.selectbox)


def test_compact_unit_view_preserves_every_classified_row(cfg):
    def app():
        import streamlit as st
        from unit_input_view import render_unit_information
        render_unit_information(st.session_state['cfg'], 'H1', compact=True)

    at = AppTest.from_function(app)
    at.session_state['cfg'] = cfg
    at.run()
    assert not at.exception
    assert len(at.dataframe) == 1
    table = at.dataframe[0].value
    expected = [{'分类': title, '参数': row['参数'], '数值': row['数值']}
                for title, rows in unit_information_sections(cfg, 'H1').items() for row in rows]
    assert table.to_dict('records') == expected
    assert list(table.columns) == ['分类', '参数', '数值']
