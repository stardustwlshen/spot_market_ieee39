"""Selected-unit workflow stays separate from participation and saved inputs."""
from copy import deepcopy
import json

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from smm.config import load_config
from smm.unit_editor import TYPES
from test_single_day_workspace import app, files, item, scene, step
from test_unit_editor import input_bytes
from unit_input_view import selected_unit_rows


TYPE_UNITS = {
    'coal': 'C1', 'gas_ccgt': 'CCGT', 'gas_gt': 'GT', 'hydro': 'H1',
    'storage': 'ES1', 'pumped': 'PS1', 'wind': 'W1', 'pv': 'PV1',
}


def raw_units(scene):
    return {u['id']: u for rows in scene.read_yaml('默认', 'units').values()
            if isinstance(rows, list) for u in rows}


def select(at, uid):
    item(at.selectbox, '当前机组').set_value(uid).run()
    assert not at.exception
    return at


def table_values(table):
    return dict(zip(table['参数'], table['数值']))


def test_top_selector_filters_types_without_changing_participation_or_quotes(scene):
    before = files(scene.SCEN_DIR)
    at = step(app(), '2 机组')
    assert not at.exception
    assert [x.label for x in at.main.selectbox][:2] == ['机组类型', '当前机组']
    assert item(at.selectbox, '机组类型').value == 'all'
    assert item(at.selectbox, '当前机组').value == 'C1'
    assert any('H2' in x and '不参与模拟' in x for x in item(at.selectbox, '当前机组').options)
    assert not any(x.startswith('TIE') for x in item(at.selectbox, '当前机组').options)
    assert not any(x.label == '要编辑的机组' for x in at.selectbox)
    assert len(at.dataframe) == 2  # One selected unit, not the full-network grid.
    for title in ('完整技术参数（当前机组）', '全网资源汇总与日初状态', '系统接线图与输入曲线'):
        assert not item(at.expander, title).proto.expanded
    quotes = deepcopy(at.session_state['quote_current:默认'])
    original = raw_units(scene)
    for kind, uid in TYPE_UNITS.items():
        item(at.selectbox, '机组类型').set_value(kind).run()
        assert not at.exception
        expected = {u['id'] for group, rows in scene.read_yaml('默认', 'units').items()
                    if group != 'tie' for u in rows if u.get('type', group) == kind}
        assert {x.split(' · ')[0] for x in item(at.selectbox, '当前机组').options} == expected
        select(at, uid)
        assert item(at.metric, '参与模拟（已保存）').value == '是'
        assert at.session_state['quote_current:默认'] == quotes
        assert raw_units(scene) == original
    assert files(scene.SCEN_DIR) == before
    assert not scene.RUNS_DIR.exists()


def test_selected_unit_survives_editor_quote_navigation_and_back(scene):
    before = files(scene.SCEN_DIR)
    at = step(app(), '2 机组')
    item(at.selectbox, '机组类型').set_value('storage').run()
    select(at, 'ES2')
    quote = deepcopy(at.session_state['quote_current:默认'])
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    assert at.session_state['unit_editor:默认']['original_id'] == 'ES2'
    assert item(at.number_input, '能量容量（MWh）').value == 400
    assert item(at.selectbox, '当前机组').disabled
    assert item(at.selectbox, '机组类型').disabled
    assert item(at.radio, '执行流程').disabled
    assert item(at.button, '上一步').disabled and item(at.button, '下一步').disabled
    item(at.button, '取消编辑').click().run()
    assert not at.exception and item(at.selectbox, '当前机组').value == 'ES2'
    item(at.button, '下一步').click().run()
    assert item(at.radio, '执行流程').value == '3 报价'
    assert item(at.selectbox, '模拟机组').value == 'ES2'
    assert at.session_state['quote_current:默认'] == quote
    item(at.selectbox, '模拟机组').set_value('C2').run()
    assert at.session_state['selected_unit:默认'] == 'C2'
    item(at.button, '上一步').click().run()
    assert not at.exception
    assert item(at.selectbox, '当前机组').value == 'C2'
    assert item(at.selectbox, '机组类型').value == 'all'
    item(at.button, '核对技术参数').click().run()
    assert at.session_state['unit_editor:默认']['original_id'] == 'C2'
    item(at.button, '取消编辑').click().run()
    assert files(scene.SCEN_DIR) == before


@pytest.mark.parametrize('kind', TYPES)
def test_omitted_enabled_has_save_only_toggle_for_every_unit_type(scene, kind):
    candidates = {**TYPE_UNITS, 'run_of_river': 'H2'}
    assert set(candidates) == set(TYPES)
    uid = candidates[kind]
    data = scene.read_yaml('默认', 'units')
    for rows in data.values():
        for unit in rows:
            if unit['id'] == uid:
                unit.pop('enabled', None)
    scene.write_yaml('默认', 'units', data)
    before = files(scene.SCEN_DIR)
    at = select(step(app(), '2 机组'), uid)
    assert 'enabled' not in raw_units(scene)[uid]
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    toggle = item(at.checkbox, '参与模拟')
    assert toggle.value is True and not toggle.disabled
    assert '保存' in toggle.proto.help
    toggle.uncheck().run()
    assert not at.exception
    assert item(at.metric, '参与模拟（已保存）').value == '是'
    assert files(scene.SCEN_DIR) == before
    item(at.button, '取消编辑').click().run()
    assert not at.exception and files(scene.SCEN_DIR) == before
    item(at.button, '核对技术参数').click().run()
    assert item(at.checkbox, '参与模拟').value is True
    item(at.button, '取消编辑').click().run()
    assert files(scene.SCEN_DIR) == before


def test_disabled_h2_is_viewable_editable_and_can_be_enabled_only_by_save(scene):
    root = scene.scenario_path('默认')
    before = input_bytes(root)
    at = select(step(app(), '2 机组'), 'H2')
    assert item(at.metric, '参与模拟（已保存）').value == '否'
    assert any('不参与下一次出清' in x.value for x in at.info)
    initial = table_values(at.dataframe[1].value)
    assert initial['初始在线台数'] == '2'  # Participation is not initial on/off state.
    assert initial['初始单机功率（MW）'] == '40'
    item(at.checkbox, '查看完整技术参数').check().run()
    assert not at.exception
    assert any('主体编号' in table_values(x.value) and table_values(x.value)['主体编号'] == 'H2'
               for x in at.dataframe if {'参数', '数值'} <= set(x.value.columns))
    item(at.button, '下一步').click().run()
    assert item(at.selectbox, '模拟机组').value == 'H2'
    assert any('H2 当前不参与模拟' in x.value for x in at.info)
    assert not any(x.label == '空载报价（元/h）' for x in at.number_input)
    assert 'H2' not in at.session_state['quote_current:默认']['units']
    item(at.button, '上一步').click().run()
    item(at.button, '核对技术参数').click().run()
    assert not at.exception and not item(at.checkbox, '参与模拟').value
    item(at.checkbox, '参与模拟').check()
    item(at.button, '检查参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(root) == before
    assert item(at.metric, '参与模拟（已保存）').value == '否'
    item(at.button, '取消编辑').click().run()
    assert not at.exception and input_bytes(root) == before
    item(at.button, '核对技术参数').click().run()
    assert item(at.checkbox, '参与模拟').value is False
    item(at.checkbox, '参与模拟').check()
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert item(at.selectbox, '当前机组').value == 'H2'
    assert item(at.metric, '参与模拟（已保存）').value == '是'
    assert raw_units(scene)['H2']['enabled'] is True
    assert 'H2' in at.session_state['quote_current:默认']['units']
    item(at.button, '下一步').click().run()
    assert not at.exception and item(at.selectbox, '模拟机组').value == 'H2'
    assert any(x.value == 'H2 报价' for x in at.subheader)


def test_save_disable_then_enable_preserves_peer_inputs_and_quotes(scene):
    at = select(step(app(), '2 机组'), 'ES1')
    root = scene.scenario_path('默认')
    units_before = raw_units(scene)
    quotes_before = deepcopy(at.session_state['quote_current:默认']['units'])
    for enabled in (False, True):
        before = input_bytes(root)
        item(at.button, '核对技术参数').click().run()
        item(at.checkbox, '参与模拟').set_value(enabled)
        item(at.button, '检查参数').click().run()
        assert not at.exception and not at.error
        assert input_bytes(root) == before
        item(at.button, '保存机组').click().run()
        assert not at.exception and not at.error
        assert any('已保存 ES1' in x.value for x in at.success)
        assert raw_units(scene)['ES1']['enabled'] is enabled
        assert item(at.selectbox, '当前机组').value == 'ES1'
        assert item(at.metric, '参与模拟（已保存）').value == ('是' if enabled else '否')
        saved = json.loads((root / 'current_quote.json').read_bytes())
        assert ('ES1' in saved['draft']['units']) is enabled
        assert saved['draft'] == at.session_state['quote_current:默认']
        assert {k: v for k, v in raw_units(scene).items() if k != 'ES1'} == {
            k: v for k, v in units_before.items() if k != 'ES1'}
        assert {k: v for k, v in saved['draft']['units'].items() if k != 'ES1'} == {
            k: v for k, v in quotes_before.items() if k != 'ES1'}
        item(at.button, '下一步').click().run()
        assert not at.exception and item(at.selectbox, '模拟机组').value == 'ES1'
        if not enabled:
            assert any('ES1 当前不参与模拟' in x.value for x in at.info)
            assert any('ES1' in old['units'] for old in saved['history'].values())
        item(at.button, '上一步').click().run()


@pytest.mark.parametrize('action,uid', [('新增机组', 'SELECT_NEW'), ('复制机组', 'SELECT_COPY')])
def test_new_or_copied_unit_becomes_selected_after_save(scene, action, uid):
    at = step(app(), '2 机组')
    item(at.selectbox, '机组类型').set_value('coal').run()
    select(at, 'C2')
    original = deepcopy(at.session_state['quote_current:默认']['units'])
    item(at.radio, '机组操作').set_value(action).run()
    if action == '新增机组':
        item(at.selectbox, '新增类型').set_value('storage').run()
    item(at.text_input, '新机组编号（不能重复）').set_value(uid)
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    before = input_bytes(scene.scenario_path('默认'))
    assert item(at.selectbox, '当前机组').value == 'C2'
    item(at.button, '检查参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene.scenario_path('默认')) == before
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert item(at.selectbox, '当前机组').value == uid
    assert at.session_state['selected_unit:默认'] == uid
    if action == '新增机组':
        assert item(at.selectbox, '机组类型').value == 'all'
    assert {k: v for k, v in at.session_state['quote_current:默认']['units'].items() if k != uid} == original
    item(at.button, '下一步').click().run()
    assert not at.exception and item(at.selectbox, '模拟机组').value == uid
    item(at.button, '上一步').click().run()
    assert not at.exception and item(at.selectbox, '当前机组').value == uid


@pytest.mark.parametrize('uid', ['C1', 'CCGT', 'H1', 'H2', 'ES1', 'PS1', 'W1', 'PV1'])
def test_selected_initial_state_tables_are_scoped_factual_and_readonly(scene, uid):
    before = files(scene.SCEN_DIR)
    cfg = load_config(root=scene.scenario_path('默认'))
    _, unit, operation, initial = selected_unit_rows(cfg, uid)
    at = select(step(app(), '2 机组'), uid)
    assert_frame_equal(at.dataframe[0].value, pd.DataFrame(operation))
    assert_frame_equal(at.dataframe[1].value, pd.DataFrame(initial))
    assert any('未核验为实测' in x.value and '不自动接续前一天' in x.value for x in at.caption)
    values = table_values(pd.DataFrame(initial))
    if uid in ('C1', 'CCGT'):
        assert values['初始状态 / 在线'] == ('是' if unit['init']['online'] else '否')
        assert float(values['初始状态 / 初始功率（MW）']) == unit['init']['p0']
        assert float(values['初始状态 / 已持续时间（h）']) == unit['init']['hours']
        # CCGT starts offline but is still participating in simulation.
        assert item(at.metric, '参与模拟（已保存）').value == '是'
    elif uid in ('H1', 'H2'):
        assert float(values['初始库容（万m³）']) == unit['seasons'][cfg.base['season']]['v0']
        assert int(values['初始在线台数']) == unit['init_units_on']
        assert any('逐台初态' in key for key in values)
    elif uid == 'ES1':
        assert float(values['日初库存电量（MWh）']) == pytest.approx(100.)
        assert float(values['高于技术SOC下限的可放电量（MWh）']) == pytest.approx(76.)
    elif uid in ('W1', 'PV1'):
        assert '无独立开停初态' in values['日初状态']
    assert files(scene.SCEN_DIR) == before


@pytest.mark.parametrize('mode,ng,np', [('idle', 0, 0), ('gen', 2, 0), ('pump', 0, 2)])
def test_pumped_capacity_and_initial_energy_are_distinct_with_both_reservoir_bounds(scene, mode, ng, np):
    data = scene.read_yaml('默认', 'units')
    pumped = data['pumped'][0]
    pumped.update(vdn_max=400, vdn_min=200, vup0=200, init_mode=mode)
    scene.write_yaml('默认', 'units', data)
    before = files(scene.SCEN_DIR)
    at = select(step(app(), '2 机组'), 'PS1')
    operation, initial = (table_values(x.value) for x in at.dataframe[:2])
    assert float(operation['有效上库下限（万m³）']) == 120
    assert float(operation['有效上库上限（万m³）']) == 320
    assert float(operation['有效库容发电容量（MWh）']) == pytest.approx(1920)
    assert float(initial['日初高于有效下限的可发电量（MWh）']) == pytest.approx(768)
    assert int(initial['初始发电台数']) == ng
    assert int(initial['初始抽水台数']) == np
    assert float(initial['日初下库库容（万m³）']) == 320
    assert '有效库容发电容量（MWh）' not in initial
    assert any('不是日初可用电量' in x.value for x in at.caption)
    assert files(scene.SCEN_DIR) == before
