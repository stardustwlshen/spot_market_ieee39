"""Retained scene drafts and explicit single-unit quotation previews."""
import copy
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'app/pages/2_运行.py'


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'app'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    for name in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
        shutil.copy2(ROOT / 'config' / name, sc.scenario_path('默认') / 'config' / name)
    return sc


def item(elements, label):
    return next(x for x in elements if x.label == label)


def open_app():
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception
    return at


def key_for(at):
    # The editor uses the scene's one current working draft.
    return item(at.number_input, '空载报价（元/h）').key.removeprefix('quote_edit:').removesuffix(':C1:noload')


def files(root):
    return {p: p.read_bytes() for p in root.rglob('*')
            if p.is_file() and p.name not in {'current_quote.json', 'current_quote.lock'}}


@pytest.mark.parametrize('uniform', [False, True])
def test_startup_modes_unit_edit_isolation_and_saved_archives(scenario, uniform):
    from smm.bids import load_bids
    from smm.config import load_config
    modes = scenario.read_yaml('默认', 'modes')
    modes['MKT']['startup_cost_mode'] = 'uniform_per_unit' if uniform else 'state_dependent'
    scenario.write_yaml('默认', 'modes', modes)
    cfg = load_config(root=scenario.scenario_path('默认'))
    original = load_bids(cfg)
    before = files(scenario.scenario_path('默认'))
    at = open_app()
    assert not any(widget.label == '报价来源' for widget in at.selectbox)
    scalar_label = '单次启动报价（元/次）'
    label = scalar_label if uniform else '空载报价（元/h）'
    assert any(x.label == scalar_label for x in at.number_input) == uniform
    first = item(at.number_input, label).value
    item(at.number_input, label).set_value(first - 1).run()
    item(at.selectbox, '模拟机组').select('CCGT').run()
    expected = (original['units']['CCGT'].get('startup_cost_yuan', original['units']['CCGT']['startup_cost']['hot'])
                if uniform else original['units']['CCGT']['noload_cost'])
    assert item(at.number_input, label).value == expected
    item(at.selectbox, '模拟机组').select('C1').run()
    assert item(at.number_input, label).value == first - 1
    assert files(scenario.scenario_path('默认')) == before
    current = at.session_state[key_for(at)]
    assert current['units']['C1']['startup_cost_yuan' if uniform else 'noload_cost'] == first - 1
    assert current['units']['C1']['startup_cost'] == original['units']['C1']['startup_cost']
    assert current['units']['CCGT'] == original['units']['CCGT']
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert load_bids(cfg) == original
    assert files(scenario.scenario_path('默认')) == before


def test_cost_preview_requires_acceptance_and_preserves_other_units_and_all_inputs(scenario):
    at = open_app()
    key = key_for(at)
    initial = copy.deepcopy(at.session_state[key])
    before = files(scenario.scenario_path('默认'))
    item(at.button, '生成报价预览').click().run()
    assert not at.exception
    assert at.session_state[key] == initial
    assert at.session_state[key + ':preview']['candidate']['_cost_basis']
    item(at.button, '采用预览报价').click().run()
    assert not at.exception
    current = at.session_state[key]
    assert current['units']['C1'] != initial['units']['C1']
    assert {u: b for u, b in current['units'].items() if u != 'C1'} == {u: b for u, b in initial['units'].items() if u != 'C1'}
    assert not at.session_state.get(key + ':manual', [])
    assert files(scenario.scenario_path('默认')) == before
    amount = item(at.number_input, '空载报价（元/h）').value
    item(at.number_input, '空载报价（元/h）').set_value(amount + 1).run()
    assert 'C1' in at.session_state[key + ':manual']
    manual = copy.deepcopy(at.session_state[key])
    item(at.button, '生成报价预览').click().run()
    assert not at.exception
    assert any('模拟报价未生成' in x.value for x in at.error)
    assert key + ':preview' not in at.session_state
    assert at.session_state[key] == manual
    assert files(scenario.scenario_path('默认')) == before


def test_missing_cost_worksheet_fails_without_fallback_or_mutation(scenario):
    path = scenario.scenario_path('默认') / 'config/bid_cost_basis.yaml'
    path.unlink(missing_ok=True)
    at = open_app()
    key = key_for(at)
    initial = copy.deepcopy(at.session_state[key])
    item(at.button, '生成报价预览').click().run()
    assert not at.exception
    assert any('模拟报价未生成' in x.value for x in at.error)
    assert at.session_state[key] == initial
    assert key + ':preview' not in at.session_state
    assert not path.exists()


@pytest.mark.parametrize('change', ['manual', 'seed', 'fixed_cost', 'physical'])
def test_preview_cannot_overwrite_newer_quote_or_parameter_changes(scenario, change):
    at = open_app()
    item(at.radio, '生成方式').set_value('随机策略').run()
    item(at.button, '生成报价预览').click().run()
    assert not at.exception
    assert not item(at.button, '采用预览报价').disabled
    if change == 'manual':
        item(at.number_input, '空载报价（元/h）').set_value(item(at.number_input, '空载报价（元/h）').value + 1).run()
    elif change == 'seed':
        item(at.number_input, '报价随机种子').set_value(item(at.number_input, '报价随机种子').value + 1).run()
    elif change == 'fixed_cost':
        path = scenario.scenario_path('默认') / 'config/fixed_costs.yaml'
        path.write_text(path.read_text() + '\n# external cost archive revision\n')
        at.run()
    else:
        units = scenario.read_yaml('默认', 'units')
        units['thermal'][0]['ramp'] += 1
        scenario.write_yaml('默认', 'units', units)
        at.run()
    assert not at.exception
    assert item(at.button, '采用预览报价').disabled
    assert any('报价或参数已变化' in x.value for x in at.warning)


def test_missing_fixed_cost_is_unknown_not_zero_and_no_cost_editor(scenario):
    path = scenario.scenario_path('默认') / 'config/fixed_costs.yaml'
    path.unlink(missing_ok=True)
    at = open_app()
    unknown = [x.value for x in at.dataframe if '数值' in x.value.columns
               and x.value['数值'].astype(str).str.contains('未知').any()]
    assert unknown
    assert any('主体核算成本' in frame['参数'].tolist() for frame in unknown)
    assert not any('固定边际成本' in x.label for x in at.number_input)
    assert not path.exists()


@pytest.mark.parametrize('cap', [None, 0.0, 1200.0])
def test_hydro_outflow_limit_and_source_are_read_only_lossless(scenario, cap):
    units = scenario.read_yaml('默认', 'units')
    hydro = units['hydro'][0]
    hydro['qout_max'] = cap
    hydro['qout_max_source'] = '合成研究假设：仅供测试，非真实工程资料'
    scenario.write_yaml('默认', 'units', units)
    before = files(scenario.scenario_path('默认'))
    at = open_app()
    item(at.selectbox, '模拟机组').select(hydro['id']).run()
    assert not at.exception
    rows = [row for table in at.dataframe if {'参数', '数值'} <= set(table.value)
            for row in table.value.to_dict('records')]
    values = {row['参数']: row['数值'] for row in rows}
    assert values['出库流量上限（m³/s）'] == ('未设置（null）' if cap is None else str(cap))
    assert values['出库流量上限来源'] == hydro['qout_max_source']
    assert not any('出库' in widget.label for widget in at.number_input)
    assert files(scenario.scenario_path('默认')) == before


def test_manual_replacement_requires_explicit_opt_in_and_acceptance(scenario):
    at = open_app()
    key = key_for(at)
    amount = item(at.number_input, '空载报价（元/h）').value + 1
    item(at.number_input, '空载报价（元/h）').set_value(amount).run()
    manual = copy.deepcopy(at.session_state[key])
    item(at.checkbox, '允许模拟报价替换该机组手工修改').check().run()
    item(at.button, '生成报价预览').click().run()
    assert not at.exception
    assert at.session_state[key] == manual
    assert not item(at.button, '采用预览报价').disabled
    item(at.button, '采用预览报价').click().run()
    assert not at.exception
    assert at.session_state[key]['units']['C1'] != manual['units']['C1']
    assert all(at.session_state[key]['units'][uid] == bid for uid, bid in manual['units'].items() if uid != 'C1')


@pytest.mark.parametrize('source', ['saved_file', 'legacy_session'])
@pytest.mark.parametrize('uid,field,label,invalid,valid', [
    ('C1', 'noload_cost', '空载报价（元/h）', -1.0, 6000.0),
    ('ES1', 'soc_end', '期末 SOC（0–1，日前硬约束）', 1.1, 0.5),
])
def test_out_of_range_existing_scalar_remains_visible_and_repairable(
        scenario, source, uid, field, label, invalid, valid):
    import json
    from smm.config import load_config
    from smm.bids import bid_dir
    cfg = load_config(root=scenario.scenario_path('默认'))
    path = max(bid_dir(cfg).glob('bids_v*.json'), key=lambda p: int(p.stem.split('_v')[-1]))
    raw = json.loads(path.read_text())
    raw['units'][uid][field] = invalid
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    if source == 'saved_file':
        path.write_text(json.dumps(raw, ensure_ascii=False))
    else:
        # Pre-existing former input-page drafts must be recoverable without
        # new widget constraints throwing or silently normalizing their data.
        legacy_key = f"draft_默认_{raw['version']}"
        from smm.bids import fill_missing_bids
        # Former pages stored the already-loaded complete bid, not raw archive JSON.
        at.session_state[legacy_key] = fill_missing_bids(cfg, copy.deepcopy(raw))
        at.session_state['quote_source:默认'] = legacy_key
    at.session_state['quote_unit:默认'] = uid
    before = files(scenario.scenario_path('默认'))
    at.run()
    assert not at.exception
    assert item(at.number_input, label).value == invalid
    assert item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert any('报价存在违规' in x.value for x in at.error)
    assert files(scenario.scenario_path('默认')) == before
    item(at.number_input, label).set_value(valid).run()
    assert not at.exception
    assert item(at.number_input, label).value == valid
    assert not item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert files(scenario.scenario_path('默认')) == before
    if source == 'legacy_session':
        assert at.session_state[legacy_key]['units'][uid][field] == invalid


def test_missing_saved_resources_are_named_without_rewriting_file(scenario):
    import json
    from smm.config import load_config
    from smm.bids import bid_dir
    cfg = load_config(root=scenario.scenario_path('默认'))
    path = bid_dir(cfg) / 'bids_v2.json'
    raw = json.loads(path.read_text())
    del raw['units']['C2']
    assert 'PS1' not in raw['units']  # This historical version predates PS1.
    path.write_text(json.dumps(raw, ensure_ascii=False))
    before = files(scenario.scenario_path('默认'))
    at = open_app()
    notice = next(x.value for x in at.warning if '原报价未包含以下主体' in x.value)
    assert 'C2' in notice and 'PS1' in notice
    assert '原报价文件未改写' in notice
    assert not item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert files(scenario.scenario_path('默认')) == before


@pytest.mark.parametrize('terminal_status', ['done', 'failed', 'stopped'])
def test_timer_fragment_requests_full_refresh_and_unlocks_quote_controls(scenario, terminal_status):
    import ast
    from types import SimpleNamespace
    from smm.live_runner import atomic_json
    from common import run_status
    directory = scenario.RUNS_DIR / '默认' / 'fixture'
    directory.mkdir(parents=True)
    meta = {'scenario': '默认', 'mode': 'full', 'status': 'running',
            'started': '2026-11-03T00:00:00', 'periods': 96, 'stage': 'RT', 'bid_version': 2}
    atomic_json(directory / 'meta.json', meta)
    at = open_app()
    for label in ('▶ 开始运行', '生成报价预览'):
        assert item(at.button, label).disabled
    assert item(at.number_input, '空载报价（元/h）').disabled
    assert item(at.selectbox, '模拟机组').disabled

    # Execute the actual monitor body as a timer fragment, retaining the old
    # page-level active=True. A plain AppTest.run alone would hide this bug.
    node = next(n for n in ast.parse(PAGE.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'monitor')
    node.decorator_list = []
    refreshes = []
    class AppRefresh(Exception):
        pass
    def request_refresh(*, scope):
        refreshes.append(scope)
        raise AppRefresh()
    env = {'sc': scenario, 'run_status': run_status, 'active': True,
           'st': SimpleNamespace(rerun=request_refresh)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(PAGE), 'exec'), env)
    meta['status'] = terminal_status
    atomic_json(directory / 'meta.json', meta)
    with pytest.raises(AppRefresh):
        env['monitor']()
    assert refreshes == ['app']
    # Deliver only the app-level refresh explicitly requested by that timer.
    at.run()
    assert not at.exception
    for label in ('▶ 开始运行', '生成报价预览'):
        assert not item(at.button, label).disabled
    assert not item(at.number_input, '空载报价（元/h）').disabled
    assert not item(at.selectbox, '模拟机组').disabled
