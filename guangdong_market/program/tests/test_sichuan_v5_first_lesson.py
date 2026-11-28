"""A new student can use a saved day without freezing bids or running a solver."""
import base64
import gzip
import json
from pathlib import Path
import sys
import pytest

from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROGRAM / 'app'))
from sichuan_v5_dadu_da import list_days
from sichuan_v5_da_console import console_data
from sichuan_v5_dadu_lesson import lesson_html, packed_lesson


def test_lesson_uses_existing_result_and_escapes_label():
    root = PROGRAM / 'runs_sichuan_v5_consultation'
    name = list_days(root, 'wet_normal')[0]
    case = packed_lesson(root, name, '</script><unsafe>')
    snapshot = json.loads(gzip.decompress(base64.b64decode(case['packed'])))
    assert len(snapshot['da']) == 96 and len(snapshot['company']['offers']) == 19
    page = lesson_html([case])
    assert '</script><unsafe>' not in page
    assert '\\u003c/script>' in page


@pytest.mark.parametrize('scenario', ['wet_normal', 'wet_extreme', 'dry_normal', 'dry_extreme'])
def test_archived_workstation_water_and_power(scenario):
    root = PROGRAM / 'runs_sichuan_v5_consultation'
    name = list_days(root, scenario)[0]
    data = console_data(str(root), name, scenario)
    system = data['system']
    assert len(data['stations']) == 19 and len(data['grid']) == 96
    assert sum(s['model'] == 'reservoir' for s in data['stations']) == 4
    for t, grid in enumerate(data['grid']):
        total = sum(system[k][t] for k in ['hydro','thermal','fixed','renewable','storage'])
        assert total + system['shed'][t] - system['excess'][t] == pytest.approx(system['load'][t] + system['pump'][t], abs=1e-5)
        assert system['hydro'][t] == pytest.approx(sum(s['series']['sced'][t] for s in data['stations']))
        _, flow, limit = grid['base']
        assert abs(flow)/limit == pytest.approx(system['max_line_pu'][t])


def test_first_visit_and_deep_links(monkeypatch):
    import sichuan_v5_dadu_da_view as view
    def unexpected_solve(*args, **kwargs):
        raise AssertionError('reading a saved day attempted to run the solver')
    monkeypatch.setattr(view, 'run_day', unexpected_solve)
    app = AppTest.from_file(str(PROGRAM / 'app/四川.py'),default_timeout=40).run()
    assert not app.exception and not app.error
    assert app.radio(key='dadu_workspace').value == '运行总览'
    app.radio(key='dadu_workspace').set_value('机组申报').run()
    assert app.radio(key='dadu_workspace').value == '机组申报'
    assert app.button(key='dadu_freeze')
    for key, expected in [('da','日前预出清'),('filing','机组申报'),('ops','运行总览'),('learn','运行总览')]:
        page = AppTest.from_file(str(PROGRAM / 'app/四川.py'),default_timeout=40)
        page.query_params.update(stage=key, scenario='dry_extreme')
        page.run()
        assert not page.exception and not page.error
        assert page.radio(key='dadu_workspace').value == expected
        assert page.radio(key='da_season').value == 'dry'
        assert page.radio(key='da_error_level').value == 'extreme'
        if expected == '运行总览':
            assert page.query_params['stage'] in ('ops', ['ops'])
