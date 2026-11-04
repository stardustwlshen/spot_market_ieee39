from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from run_record_actions import elapsed_for_display, has_saved_result


@pytest.mark.parametrize('status', ['starting', 'running', 'cancelling'])
def test_active_elapsed_is_labelled_wall_time_and_increases_without_writing(status):
    meta = {'status': status, 'started': '2026-11-04T05:00:00'}
    before = dict(meta)
    assert elapsed_for_display(meta, now=datetime(2026, 11, 4, 5, 0, 17)) == (17., True)
    assert elapsed_for_display(meta, now=datetime(2026, 11, 4, 5, 0, 29)) == (29., True)
    assert meta == before


def test_aware_time_and_terminal_runtime_are_not_conflated():
    meta = {'status': 'running', 'started': '2026-11-04T05:00:00Z'}
    assert elapsed_for_display(meta, now=datetime(2026, 11, 4, 5, 0, 4, tzinfo=timezone.utc)) == (4., True)
    assert elapsed_for_display({'status': 'done', 'elapsed_s': 10.25, 'started': meta['started']}) == (10.25, False)
    assert elapsed_for_display({'status': 'failed', 'started': meta['started']}) == (None, False)


@pytest.mark.parametrize('started', [None, '', 'bad', '2027-01-01T00:00:00', '2026-11-04T05:00:00Z'])
def test_missing_invalid_future_or_mixed_timezone_is_unknown(started):
    assert elapsed_for_display({'status': 'running', 'started': started},
                               now=datetime(2026, 11, 4, 5)) == (None, False)


@pytest.mark.parametrize('value', [None, True, -1, float('nan'), float('inf'), '10'])
def test_invalid_recorded_runtime_stays_unknown(value):
    assert elapsed_for_display({'status': 'done', 'elapsed_s': value}) == (None, False)


def test_explicit_result_action_preserves_files_and_other_scene_state(tmp_path, monkeypatch):
    result = tmp_path / 'result'; result.mkdir()
    (result / 'summary.json').write_text('{}')
    digest = hashlib.sha256((result / 'summary.json').read_bytes()).hexdigest()
    meta = {'status': 'done', 'dir': str(result)}
    monkeypatch.setattr(st, 'switch_page', lambda target: st.session_state.update(navigation_target=target))
    code = f'''import sys
sys.path.insert(0, {str(ROOT/'app')!r})
import streamlit as st
from run_record_actions import render_open_saved_result
st.session_state.setdefault('result_sel', 'old-history')
st.session_state.setdefault('scenario', 'keep-current-input')
render_open_saved_result({meta!r})
'''
    at = AppTest.from_string(code).run()
    assert not at.exception and at.session_state['result_sel'] == 'old-history'
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state['result_sel'] == str(result)
    assert at.session_state['navigation_target'] == 'pages/3_结果.py'
    assert at.session_state['scenario'] == 'keep-current-input'
    assert hashlib.sha256((result / 'summary.json').read_bytes()).hexdigest() == digest
    assert not has_saved_result({'status': 'running', 'dir': str(result)})
    assert not has_saved_result({'status': 'failed', 'dir': str(result)})
    assert not has_saved_result({'status': 'done', 'dir': str(tmp_path/'missing')})


def test_actual_run_card_button_overrides_old_history_then_keeps_selection(tmp_path, monkeypatch):
    evidence = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only archives')
    import common
    from smm import scenario
    paths = [Path(evidence) / name for name in
             ('final_c1f829a_baseline_full96', 'pv2152_dry_lowre_highload_full96')]
    watched = [path / name for path in paths for name in
               ('summary.json', 'profiles.csv', 'resolved_bids.json', 'rt_reserve_system.csv')]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    records = [dict(dir=str(path), scenario='默认', mode='full', status='done',
                    started='', bid_version=3) for path in paths]
    monkeypatch.setattr(common, 'all_results', lambda: [dict(m) for m in records])
    monkeypatch.setattr(scenario, 'list_runs', lambda: [dict(m) for m in records])
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    at.selectbox(key='_result_sel_widget').set_value(str(paths[1])).run()
    assert any('237.2283 MW' in x.value for x in at.error)
    at.switch_page('pages/2_运行.py').run()
    at.selectbox(key='run_sel').set_value(str(paths[0])).run()
    next(b for b in at.button if b.label == '查看本次结果').click().run()
    assert not at.exception, [x.value for x in at.exception]
    assert at.selectbox(key='_result_sel_widget').value == str(paths[0])
    assert not any('237.2283 MW' in x.value for x in at.error)
    # AppTest keeps the prior explicit switch_page request in _page_hash even
    # after application st.switch_page has rendered the destination correctly.
    # Synchronize its simulated browser route via the public API, then rerun.
    at.switch_page('pages/3_结果.py').run()
    at.run()
    assert at.selectbox(key='_result_sel_widget').value == str(paths[0])
    at.switch_page('pages/2_运行.py').run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception and at.selectbox(key='_result_sel_widget').value == str(paths[0])
    assert at.session_state['scenario'] == '默认'
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
