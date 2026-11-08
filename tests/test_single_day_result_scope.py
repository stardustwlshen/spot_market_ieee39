"""Single-day routes and result scope, with no solver or user-archive writes."""
import hashlib
import json
from pathlib import Path
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from result_catalog import is_single_day_result
from run_record_actions import has_saved_result


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    import common
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    sc.ensure_default()
    return sc


def app():
    return AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=60).run()


def labels(at):
    return [getattr(e, 'label', '') for e in at]


@pytest.mark.parametrize('marker', ['series_root', 'study_root', 'day_commit', 'parent_identity'])
def test_scope_uses_provenance_not_title(tmp_path, marker):
    directory = tmp_path / 'ordinary-title'; directory.mkdir()
    meta = dict(dir=str(directory), scenario='普通标题', mode='full', status='done')
    if marker in ('series_root', 'study_root'):
        meta[marker] = str(tmp_path)
    elif marker == 'day_commit':
        (directory / 'day_commit.json').write_text('{}', encoding='utf-8')
    else:
        (directory.parent / 'series_identity.json').write_text('{}', encoding='utf-8')
    (directory / 'summary.json').write_text('{}', encoding='utf-8')
    before = snapshot(tmp_path)
    assert not is_single_day_result(meta)
    assert not has_saved_result(meta)
    assert snapshot(tmp_path) == before


def test_title_and_directory_name_do_not_determine_scope(tmp_path):
    directory = tmp_path / 'day_2026-07-15'; directory.mkdir()
    meta = dict(dir=str(directory), scenario='连续变量研究', mode='da', status='done')
    assert is_single_day_result(meta)


def test_catalog_does_not_scan_studies_or_touch_archives(scene, tmp_path, monkeypatch):
    import common
    from smm import series_ui_adapter as adapter
    records = []
    for name, status in [('day', 'done'), ('active', 'running')]:
        path = scene.RUNS_DIR / '默认' / name; path.mkdir(parents=True)
        meta = dict(scenario='默认', mode='full', status=status, started='2026-11-07')
        (path / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
        if status == 'done': records.append(str(path))
    cli = tmp_path / 'outputs/full_2026-07-15_MKT_wet'; cli.mkdir(parents=True)
    (cli / 'summary.json').write_text('{}', encoding='utf-8'); records.append(str(cli))
    copied = tmp_path / 'outputs/full_2026-07-16_MKT_copy'; copied.mkdir()
    (copied / 'summary.json').write_text('{}', encoding='utf-8')
    (copied / 'day_commit.json').write_text('{}', encoding='utf-8')
    study = tmp_path / 'series_tasks/study/run/day_2026-07-15'; study.mkdir(parents=True)
    (study / 'day_commit.json').write_text('{}', encoding='utf-8')
    before = snapshot(tmp_path)
    monkeypatch.setattr(adapter, 'completed_day_metadata', lambda *_: pytest.fail('study scanner reached'))
    assert [r['dir'] for r in common.all_results()] == records
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('page', [None, 'pages/2_运行.py', 'pages/3_结果.py',
                                  'pages/4_场景对比.py', 'pages/5_出清任务.py'])
def test_visible_navigation_and_legacy_route_have_no_study_controls(scene, tmp_path, monkeypatch, page):
    import series_workspace_ui
    monkeypatch.setattr(series_workspace_ui, 'render_series_workspace',
                        lambda: pytest.fail('legacy workspace rendered'))
    before = snapshot(tmp_path)
    at = app()
    if page:
        at.switch_page(page).run()
    assert not at.exception, [e.message for e in at.exception]
    assert not any('多日' in label or '连续出清' in label for label in labels(at.sidebar))
    assert not any(e.label == '模拟几天' for e in at.number_input)
    assert not any((getattr(e, 'key', '') or '').startswith('series_') for e in at)
    assert not any(e.label in ('创建模拟', '开始连续模拟', '开始模拟') for e in at.button)
    if page == 'pages/5_出清任务.py':
        assert any('请从单日模拟开始' in e.value for e in at.info)
        assert not at.number_input and not at.date_input and not at.selectbox
        assert any(e.label == '前往单日模拟' for e in at.get('page_link'))
        at.switch_page('pages/6_单日模拟.py').run()
        assert not at.exception
    # Default quote initialization is not a run; preserved study data stays untouched.
    assert not list(tmp_path.glob('series_tasks/*'))
    assert not scene.RUNS_DIR.exists()
    for name, digest in before.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest


def test_result_page_discards_stale_study_selection(scene, tmp_path, monkeypatch):
    import common
    study = dict(dir=str(tmp_path / 'study/day_2026-07-15'), scenario='连续出清研究',
                 series_root=str(tmp_path / 'study'), mode='full', status='done')
    day = dict(dir=str(tmp_path / 'single'), scenario='单日核查', mode='da', status='done')
    seen = []
    def saved(meta):
        seen.append(meta)
        raise OSError('fixture deliberately contains no numerical output')
    monkeypatch.setattr(common, 'all_results', lambda: [study, day])
    monkeypatch.setattr(common, 'get_result', saved)
    at = app(); at.session_state['result_sel'] = study['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert at.session_state['result_sel'] == day['dir']
    assert at.selectbox(key='_result_sel_widget').value == day['dir']
    assert len(at.selectbox(key='_result_sel_widget').options) == 1
    assert seen == [day]
    assert not any('连续出清' in option for e in at.selectbox for option in e.options)
    assert not any(e.label == '该日来源与接续凭据' for e in at.expander)
    at.switch_page('pages/2_运行.py').run()
    assert not at.exception
    at.switch_page('pages/3_结果.py').run()
    assert at.session_state['result_sel'] == day['dir']


def test_only_studies_leave_empty_result_and_comparison_pages(scene, tmp_path, monkeypatch):
    import common
    studies = [dict(dir=str(tmp_path / f'study/day_{i}'), series_root=str(tmp_path / 'study'),
                    scenario='连续出清研究', mode='full', status='done') for i in (1, 2)]
    monkeypatch.setattr(common, 'all_results', lambda: studies)
    monkeypatch.setattr(common, 'get_result', lambda *_: pytest.fail('study result loaded'))
    at = app(); at.session_state['result_sel'] = studies[0]['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception and not at.selectbox
    assert any('还没有完成的运行结果' in e.value for e in at.info)
    assert 'result_sel' not in at.session_state
    at.switch_page('pages/4_场景对比.py').run()
    assert not at.exception and not at.selectbox
    assert any('至少需要两个完成的结果' in e.value for e in at.info)


@pytest.mark.parametrize('mode,summary,status,expected', [
    ('da', 'summary.json', 'done', True),
    ('da', 'da/summary.json', 'done', True),
    ('full', 'summary.json', 'done', True),
    ('full', 'da/summary.json', 'done', False),
    ('da', 'da/summary.json', 'running', False),
    ('full', 'summary.json', 'stopped', False),
    ('full', 'summary.json', 'failed', False),
])
def test_saved_result_action_handles_da_and_completed_scope(tmp_path, monkeypatch, mode, summary, status, expected):
    file = tmp_path / summary; file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('{}', encoding='utf-8')
    meta = dict(dir=str(tmp_path), mode=mode, status=status, periods=8)
    before = snapshot(tmp_path)
    assert has_saved_result(meta) is expected
    monkeypatch.setattr(st, 'switch_page', lambda target: st.session_state.update(destination=target))
    code = f"from run_record_actions import render_open_saved_result\nrender_open_saved_result({meta!r})"
    at = AppTest.from_string(code).run()
    assert not at.exception
    assert bool(at.button) is expected
    if expected:
        assert at.button[0].label == '查看本次结果'  # No complete-day claim for DA/debug scope.
        at.button[0].click().run()
        assert at.session_state['result_sel'] == str(tmp_path)
        assert at.session_state['destination'] == 'pages/3_结果.py'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('scope', ['full', 'da-root', 'da-subdirectory'])
def test_saved_single_day_stages_remain_available(scene, tmp_path, monkeypatch, scope):
    """Replay stored numerical evidence; never launch a solver or invent RT data."""
    import os
    import shutil
    import common
    evidence = os.environ.get('IEEE39_SINGLE_DAY_SCOPE_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_SINGLE_DAY_SCOPE_EVIDENCE to a saved full-day archive with ID')
    evidence = Path(evidence)
    assert (evidence / 'summary.json').is_file()
    before = snapshot(evidence)
    summary = json.loads((evidence / 'summary.json').read_text(encoding='utf-8'))
    assert summary.get('completed_scope') == 'full_day'
    directory = evidence
    mode = 'full'
    if scope != 'full':
        # A test fixture containing only the actual archived DA phase.
        directory = tmp_path / scope
        target = directory / 'da' if scope == 'da-subdirectory' else directory
        target.mkdir(parents=True)
        da_source = evidence / 'da' if (evidence / 'da').is_dir() else evidence
        for source in da_source.glob('da_*.csv'):
            shutil.copyfile(source, target / source.name)
        (target / 'summary.json').write_text(json.dumps(summary['da_summary']), encoding='utf-8')
        mode = 'da'
    record = dict(dir=str(directory), scenario='单日阶段回放', mode=mode, status='done')
    stale = dict(dir=str(tmp_path / 'old/day_2026-07-15'), scenario='连续出清研究',
                 series_root=str(tmp_path / 'old'), mode='full', status='done')
    monkeypatch.setattr(common, 'all_results', lambda: [stale, record])
    at = app(); at.session_state['result_sel'] = stale['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [e.message for e in at.exception]
    assert at.selectbox(key='_result_sel_widget').value == record['dir']
    assert not any('连续出清' in option for e in at.selectbox for option in e.options)
    assert not any(e.label == '该日来源与接续凭据' for e in at.expander)
    assert not any('本次结果缺失或损坏' in e.value for e in at.error)
    sections = ['系统总览', '机组分析', '价格分析', '网络与安全']
    assert [t.label for t in at.tabs] == sections
    if scope == 'full':
        assert at.radio(key='advanced_stage').options == ['DA', 'ID', 'RT']
        assert '日内参考（不结算）' in at.radio(key='price_stage').options
        assert {'实时窗口与前瞻约束', '结算恒等式与资金核对'} <= {e.label for e in at.expander}
        saved = common.get_result(record)
        assert not saved['id_rounds_summary'].empty
        assert not saved['rt_prices'].empty
        assert not saved['settlement_detail'].empty
        for stage in ['ID', 'RT', 'DA']:
            at.radio(key='advanced_stage').set_value(stage).run()
            assert not at.exception, [e.message for e in at.exception]
            assert [t.label for t in at.tabs] == sections
            if stage == 'ID':
                assert '日内轮次与交接' in {e.label for e in at.expander}
    else:
        assert at.radio(key='advanced_stage').options == ['DA']
        for key in ['overview_stage', 'price_stage', 'network_stage']:
            assert len(at.radio(key=key).options) == 1
        assert '实时窗口与前瞻约束' not in {e.label for e in at.expander}
        assert any('仅日前计划' in e.value for e in at.caption)
    assert has_saved_result(record)
    assert snapshot(evidence) == before
