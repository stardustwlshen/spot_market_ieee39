"""Regression coverage for creating and selecting a base scenario in Streamlit."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = "\u9ed8\u8ba4"
CREATE = "\u53e6\u5efa\u65b0\u7248\u9ed8\u8ba4\u65b9\u6848"
NEW_NAME = "\u65b0\u65b9\u6848\u540d\u79f0"


def open_panel(tmp_path, sidebar=False):
    # Use actual scenario creation, but isolate all persistent user inputs.
    script = f"""import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(ROOT / 'app')!r})
import series_workspace_ui as ui
with patch.object(ui.sc, 'SCEN_DIR', Path({str(tmp_path / 'scenarios')!r})), patch.object(ui, 'ROOT', Path({str(tmp_path)!r})), patch.object(ui.st, 'page_link'):
    if {sidebar!r}:
        with ui.st.sidebar:
            ui.create_panel([object()])
    else:
        ui.create_panel([])
"""
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception
    return at


def click_create(at):
    next(button for button in at.button if button.label == CREATE).click().run()
    return at


def input_name(at, name):
    next(widget for widget in at.text_input if widget.label == NEW_NAME).set_value(name)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('sidebar', [False, True])
def test_create_selects_new_scenario_once_and_preserves_original(tmp_path, sidebar):
    at = open_panel(tmp_path, sidebar)
    old = tmp_path / 'scenarios' / DEFAULT
    before = files(old)
    input_name(at, 'new_research')
    click_create(at)
    assert not at.exception
    assert at.selectbox(key='series_create_source').value == 'new_research'
    created = tmp_path / 'scenarios/new_research'
    assert (created / 'scenario.json').exists()
    assert files(old) == before
    after = files(created)
    at.run()
    assert not at.exception
    assert at.selectbox(key='series_create_source').value == 'new_research'
    assert files(created) == after
    assert sorted(p.name for p in (tmp_path / 'scenarios').iterdir()) == ['new_research', DEFAULT]
    assert not (tmp_path / 'series_tasks').exists()


def test_duplicate_create_preserves_files_and_can_select_existing(tmp_path):
    at = open_panel(tmp_path)
    input_name(at, 'existing')
    click_create(at)
    assert not at.exception
    created = tmp_path / 'scenarios/existing'
    before = files(created)
    at.selectbox(key='series_create_source').select(DEFAULT).run()
    click_create(at)
    assert not at.exception
    assert at.error
    assert at.selectbox(key='series_create_source').value == DEFAULT
    assert files(created) == before
    at.selectbox(key='series_create_source').select('existing').run()
    assert not at.exception
    assert at.selectbox(key='series_create_source').value == 'existing'
    assert files(created) == before


def test_name_is_normalized_before_selection(tmp_path):
    at = open_panel(tmp_path)
    input_name(at, '  trimmed_name  ')
    click_create(at)
    assert not at.exception
    assert at.selectbox(key='series_create_source').value == 'trimmed_name'
    assert (tmp_path / 'scenarios/trimmed_name/scenario.json').exists()


def test_invalid_name_leaves_source_and_files_unchanged(tmp_path):
    at = open_panel(tmp_path)
    before = files(tmp_path / 'scenarios')
    input_name(at, '../bad_name')
    click_create(at)
    assert not at.exception
    assert at.error
    assert at.selectbox(key='series_create_source').value == DEFAULT
    assert files(tmp_path / 'scenarios') == before


def test_create_simulation_uses_the_new_selected_source(tmp_path):
    import json
    at = open_panel(tmp_path)
    input_name(at, 'selected_for_task')
    click_create(at)
    assert not at.exception
    next(button for button in at.button if button.label == "\u521b\u5efa\u6a21\u62df").click().run()
    assert not at.exception
    assert not at.error
    manifests = list((tmp_path / 'series_tasks').glob('*/manifest.json'))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding='utf-8'))
    assert Path(manifest['source']['path']) == tmp_path / 'scenarios/selected_for_task'
    assert not list((tmp_path / 'series_tasks').glob('*/launch.json'))
