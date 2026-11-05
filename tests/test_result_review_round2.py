"""Independent operator review regressions, isolated from all real scene/run writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from result_policy_notes import storage_policy_note, saved_stage_diagnostics, saved_object, storage_endpoint_rows
from result_navigation import validate_line_scope
from common import line_loading, congestion_period_count


def value_result(policy=None, horizon=4):
    rt = {'storage_mode': 'value', 'lookahead_h': horizon}
    if policy is not None:
        rt['terminal_policy_mode'] = policy
    return {'summary': {'realtime': rt}, 'rt_value_windows': pd.DataFrame({
        'reference_source': ['day_ahead', 'intraday@8'], 'value_source': ['day_ahead', 'intraday@8']})}


def test_value_note_uses_saved_horizon_sources_and_soft_policy():
    note = storage_policy_note(value_result('soft'))
    assert '前瞻 4 小时' in note and '逐资源、逐窗口' in note and '日内' in note
    assert '计划软目标' in note and '不要求末态硬等于日前' in note
    assert 'v 取日前' not in note and '每个2小时' not in note


def test_legacy_hard_unknown_and_conflicting_policy_are_not_relabelled_soft():
    assert '硬目标政策' in storage_policy_note(value_result('hard'))
    assert '未明确记录' in storage_policy_note(value_result())
    frozen = {'realtime': {'value_method': {'terminal_policy': {'mode': 'soft'}}}}
    assert '计划软目标' in storage_policy_note(value_result(), frozen)
    assert '记录不一致' in storage_policy_note(value_result('hard'), frozen)
    assert '前瞻时长未记录' in storage_policy_note(value_result('soft', float('nan')))


@pytest.mark.parametrize('payload', ['{', '[]', 'null', '{"da": []}', '{"rt_windows": {}}', '{"rt_windows": [null]}'])
def test_bad_optional_diagnostics_are_reported_not_crashed_or_rewritten(tmp_path, payload):
    path = tmp_path / 'stage_diagnostics.json'; path.write_text(payload)
    data, error = saved_stage_diagnostics(tmp_path)
    assert data == {} and error and path.read_text() == payload


def test_optional_diagnostics_missing_is_distinct_from_corrupt(tmp_path):
    assert saved_stage_diagnostics(tmp_path) == ({}, None)
    data = {'da': {}, 'rt_windows': [{'t': 0, 'stages': {}}]}
    (tmp_path / 'stage_diagnostics.json').write_text(json.dumps(data))
    assert saved_stage_diagnostics(tmp_path) == (data, None)
    (tmp_path / 'run_provenance.json').write_text('null')
    assert saved_object(tmp_path / 'run_provenance.json')[1]


@pytest.mark.parametrize('da', [None, pd.DataFrame(), pd.DataFrame({'unit': ['ES1'], 'period': [0], 'E_mwh': [50.]})])
def test_missing_da_storage_endpoint_keeps_unknown_reference(da):
    rt = pd.DataFrame({'unit': ['ES1', 'ES1'], 'period': [0, 1], 'E_mwh': [40., 45.]})
    rows = storage_endpoint_rows({'rt_storage': rt, 'da_storage': da}, '方案甲')
    assert len(rows) == 1 and pd.isna(rows[0]['日前电量@末时段'])
    assert rows[0]['实时电量@末时段'] == 45.


def line_result(tmp_path, frozen=True):
    if frozen:
        path = tmp_path / 'data/case39/branches.csv'; path.parent.mkdir(parents=True)
        path.write_text('branch_id\nL1\nL2\n')
    prices = pd.DataFrame({'period': [0, 1]})
    lines = pd.DataFrame([{'period': t, 'line': u, 'flow_mw': 10., 'mu_plus': 1. if u == 'L2' else 0.,
                           'mu_minus': 0., 'rate_mw': 20.} for t in range(2) for u in ('L1', 'L2')])
    return {'full': True, 'snapshot_root': str(tmp_path), 'da_nodal_prices': prices,
            'rt_prices': prices.copy(), 'da_line_flows': lines, 'rt_lines': lines.copy()}


@pytest.mark.parametrize('damage', ['whole_line', 'one_row', 'whole_period', 'duplicate', 'nan_flow', 'infinite_dual', 'unknown_line'])
def test_partial_line_network_cannot_be_reported_as_zero_congestion(tmp_path, damage):
    result = line_result(tmp_path)
    validate_line_scope(result)
    assert congestion_period_count(line_loading(result, 'rt')) == 2
    frame = result['rt_lines']
    if damage == 'whole_line': frame = frame[frame.line != 'L2']
    elif damage == 'one_row': frame = frame.iloc[1:]
    elif damage == 'whole_period': frame = frame[frame.period != 1]
    elif damage == 'duplicate': frame = pd.concat([frame, frame.iloc[:1]])
    elif damage == 'nan_flow': frame.loc[0, 'flow_mw'] = float('nan')
    elif damage == 'infinite_dual': frame.loc[0, 'mu_plus'] = float('inf')
    else: frame.loc[frame.line == 'L2', 'line'] = 'Other'
    result['rt_lines'] = frame
    with pytest.raises(ValueError, match='线路'):
        validate_line_scope(result)


def test_absent_frozen_line_catalog_makes_network_coverage_unknown(tmp_path):
    result = line_result(tmp_path, frozen=False)
    validate_line_scope(result)
    assert not any(result['line_scope_verified'].values()) and len(result['line_scope_warnings']) == 2
    assert pd.isna(congestion_period_count(line_loading(result, 'rt')))
    assert result['rt_lines'].flow_mw.tolist() == [10.] * 4


def test_line_scope_does_not_require_optional_absent_historical_flows(tmp_path):
    result = line_result(tmp_path);result['rt_lines'] = None
    validate_line_scope(result)
    assert result['line_scope_verified'] == {'da': True, 'rt': False}


def write_run(root, scene, name, value):
    directory = root / scene / name;directory.mkdir(parents=True)
    (directory / 'meta.json').write_text(json.dumps(value))
    return directory


@pytest.mark.parametrize('value', [[], None, {'scenario': '默认', 'mode': 'full', 'status': 'done', 'started': 9},
                                 {'scenario': '默认', 'mode': [], 'status': 'done'},
                                 {'scenario': '默认', 'mode': 'full', 'status': 'done', 'periods': '8'}])
def test_bad_meta_is_isolated_visible_not_done_and_original_unchanged(tmp_path, monkeypatch, value):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path)
    bad = write_run(tmp_path, '默认', 'bad', value)
    before = (bad / 'meta.json').read_bytes()
    good = write_run(tmp_path, '默认', 'good', {'scenario': '默认', 'mode': 'da', 'status': 'done', 'started': '2026-11-05'})
    rows = sc.list_runs()
    assert len(rows) == 2 and rows[0]['dir'] == str(good)
    assert rows[1]['status'] == 'invalid' and rows[1]['metadata_error']
    import common
    assert '元数据损坏' in common.run_label(rows[1])
    assert common.run_status(rows[1]) == 'invalid'
    assert (bad / 'meta.json').read_bytes() == before
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    assert [m['dir'] for m in common.all_results()] == [str(good)]


def test_run_page_keeps_bad_metadata_visible_and_other_scenes_usable(tmp_path, monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default();sc.create_scenario('另一场景', source=sc.DEFAULT)
    bad = write_run(sc.RUNS_DIR, sc.DEFAULT, 'bad', [])
    before = (bad / 'meta.json').read_bytes()
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=60).run()
    assert not at.exception
    assert next(x for x in at.button if x.label == '▶ 开始运行').disabled
    assert any('元数据损坏' in x.value or '损坏的运行元数据' in x.value for x in at.error)
    next(x for x in at.selectbox if x.label == '当前场景').select('另一场景').run()
    assert not at.exception
    assert not next(x for x in at.button if x.label == '▶ 开始运行').disabled
    assert (bad / 'meta.json').read_bytes() == before


def test_launch_guard_blocks_same_scene_before_new_directory_or_spawn(tmp_path, monkeypatch):
    from smm import scenario as sc
    import common
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios');monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default();write_run(sc.RUNS_DIR, sc.DEFAULT, 'bad', None)
    monkeypatch.setattr(common.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not spawn'))
    before = list(sc.RUNS_DIR.rglob('*'))
    with pytest.raises(Exception, match='元数据损坏'):
        common.start_run(sc.DEFAULT, 'full', 1, 1)
    assert [p for p in sc.RUNS_DIR.rglob('*') if p.name != '.launch.lock'] == before


@pytest.fixture
def archive():
    paths = sorted((ROOT / 'outputs').glob('full_*_hydro_final_id_off_*/summary.json'))
    if not paths:
        pytest.skip('latest completed archive not distributed')
    return paths[0].parent


@pytest.mark.parametrize('damage', ['diagnostics', 'provenance'])
def test_real_result_optional_damage_does_not_block_final_settlement(archive, tmp_path, monkeypatch, damage):
    import common
    from smm import scenario as sc
    run = tmp_path / 'archive';shutil.copytree(archive, run)
    path = run / ('stage_diagnostics.json' if damage == 'diagnostics' else 'run_provenance.json')
    path.write_text('{')
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios');monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(common, 'all_results', lambda: [dict(dir=str(run), scenario='审查', mode='full', status='done')])
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run().switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    assert at.warning and any(x.value == '按资源类型' for x in at.subheader)
    assert path.read_text() == '{'


def test_real_comparison_missing_da_storage_still_shows_cash_settlement(archive, tmp_path, monkeypatch):
    import common
    from smm import scenario as sc
    run = tmp_path / 'archive';shutil.copytree(archive, run)
    paths = list(run.glob('**/da_storage.csv'));assert paths
    for path in paths:path.unlink()
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios');monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(common, 'all_results', lambda: [dict(dir=str(p), scenario=p.name, mode='full', status='done') for p in (run, archive)])
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run().switch_page('pages/4_场景对比.py').run()
    assert not at.exception, [x.message for x in at.exception]
    assert any('未保存相应日前储能末态' in x.value for x in at.info)


def test_cli_frozen_bid_location_used_before_legacy_versions(tmp_path):
    from market_views import frozen_bids
    snap = tmp_path / 'scenario_snapshot';snap.mkdir()
    bids = {'version': 4, 'units': {'G1': {'segments': [{'price': 77.}]}}}
    (snap / 'resolved_bids.json').write_text(json.dumps(bids))
    assert frozen_bids(tmp_path) == bids
    effective = {'version': 5, 'units': {'G1': {'segments': [{'price': 88.}]}}}
    (tmp_path / 'resolved_bids.json').write_text(json.dumps(effective))
    assert frozen_bids(tmp_path) == effective


def test_saved_line_limits_missing_does_not_claim_zero_network_congestion(tmp_path):
    result = line_result(tmp_path)
    result['rt_lines']['rate_mw'] = float('nan')
    validate_line_scope(result)
    assert pd.isna(congestion_period_count(line_loading(result, 'rt')))


def test_real_missing_congested_line_fails_closed_and_selection_recovers(archive, tmp_path, monkeypatch):
    import common
    from smm import scenario as sc
    original = common.get_result(dict(dir=str(archive), mode='full'))
    broken = deepcopy(original)
    removed = broken['rt_lines'].line.iloc[0]
    broken['rt_lines'] = broken['rt_lines'].loc[broken['rt_lines'].line != removed].copy()
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios');monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(common, 'all_results', lambda: [dict(dir=p, scenario=p, mode='full', status='done') for p in ('damaged', str(archive))])
    monkeypatch.setattr(common, 'get_result', lambda meta: deepcopy(broken if meta['dir'] == 'damaged' else original))
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run().switch_page('pages/3_结果.py').run()
    assert not at.exception and any('本次结果缺失或损坏' in x.value for x in at.error)
    assert not at.metric
    at.selectbox(key='_result_sel_widget').set_value(str(archive)).run()
    assert not at.exception, [x.message for x in at.exception]
    assert at.metric and not any('本次结果缺失或损坏' in x.value for x in at.error)
