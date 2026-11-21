"""Exercise the actual default page, not only isolated UI components."""
import ast
import sys
from pathlib import Path


def test_actual_default_entry_and_invalid_source():
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'app'))
    ast.parse((root / 'app/gd_provincial_page.py').read_text())
    app = AppTest.from_file(str(root / 'app/主页.py'), default_timeout=60).run()
    assert not app.exception
    assert not app.error
    assert any(button.label == '保存新增机组' for button in app.button)
    app.text_input(key='prov_source').set_value(str(root / '__missing_source_test__'))
    app.run()
    assert not app.exception
    assert app.error
    assert app.button(key='prov_prepare').disabled


def test_saved_task_blocker_visible_next_to_prepare_and_refreshable(tmp_path):
    import json
    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parents[1]
    results = tmp_path / 'runs'
    attempt = results / 'old_market_day'
    attempt.mkdir(parents=True)
    (attempt / 'PROGRESS.json').write_text('{"status":"running","stage":2}')

    app = AppTest.from_file(str(root / 'app/主页.py'), default_timeout=60).run()
    assert not app.exception
    assert not app.button(key='prov_prepare').disabled
    app.text_input(key='prov_out').set_value(str(results)).run()
    assert not app.exception
    assert app.button(key='prov_prepare').disabled
    top = list(app.main.children.values())
    blocker = next(i for i, x in enumerate(top)
                   if x.type == 'warning' and '暂不能开始新计算' in x.value)
    prepare = top.index(app.button(key='prov_prepare'))
    assert blocker == prepare - 1
    archive = next(e for e in app.get('expander') if e.label == '找回已保存任务（只读）')
    assert archive.proto.expanded
    assert '日前 · old_market_day · 记录待核实' in app.selectbox(key='prov_saved_job_choice').options

    app.selectbox(key='prov_saved_job_choice').select_index(1).run()
    assert any('日前尚未确认准备完成' in m.value for m in app.error)
    assert str(attempt) in ' '.join(m.value for m in app.caption)

    # An explicit recorded nonzero wrapper exit allows a new attempt, but never starts one.
    (attempt / 'STEP_2.json').write_text(json.dumps({'returncode': 1}))
    (attempt / 'PROGRESS.json').write_text(json.dumps({
        'status': 'failed', 'stage': 2, 'error': '测试用退出记录',
    }))
    app.button(key='prov_saved_job_refresh').click().run()
    assert not app.exception
    assert not app.button(key='prov_prepare').disabled
    assert not any('暂不能开始新计算' in m.value for m in app.warning)
    assert list(results.iterdir()) == [attempt]


def test_saved_task_menu_distinguishes_same_named_realtime_attempts(tmp_path):
    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parents[1]
    results = tmp_path / 'runs'
    old = results / 'day_A' / 'live_market_sessions' / 'same_name'
    new = results / 'day_B' / 'live_market_sessions' / 'same_name'
    for task in (old, new):
        task.mkdir(parents=True)
    app = AppTest.from_file(str(root / 'app/主页.py'), default_timeout=60).run()
    app.text_input(key='prov_out').set_value(str(results)).run()
    assert not app.exception
    choices = app.selectbox(key='prov_saved_job_choice').options[1:]
    assert len(choices) == len(set(choices)) == 2
    for choice, task in zip(choices, (old, new)):
        assert '实时 · same_name · 记录待核实' in choice
        assert task.relative_to(results).as_posix() in choice
        app.selectbox(key='prov_saved_job_choice').select(choice).run()
        assert not app.exception
        assert any(x.value.startswith('正在查看：' + str(task) + '。') for x in app.caption)
