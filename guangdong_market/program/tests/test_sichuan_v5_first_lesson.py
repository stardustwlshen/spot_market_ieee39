"""A new student can use a saved day without freezing bids or running a solver."""
import base64
import gzip
import json
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROGRAM / 'app'))
from sichuan_v5_dadu_da import list_days
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


def test_first_visit_and_deep_links(monkeypatch):
    import sichuan_v5_dadu_da_view as view
    def unexpected_solve(*args, **kwargs):
        raise AssertionError('reading a saved day attempted to run the solver')
    monkeypatch.setattr(view, 'run_day', unexpected_solve)
    app = AppTest.from_file(str(PROGRAM / 'app/四川.py'),default_timeout=40).run()
    assert not app.exception and not app.error
    assert app.radio(key='dadu_workspace').value == '入门体验'
    app.button(key='da_lesson_file').click().run()
    assert app.radio(key='dadu_workspace').value == '机组申报'
    assert app.button(key='dadu_freeze')
    for key, expected in [('da','日前预出清'),('filing','机组申报'),('learn','入门体验')]:
        page = AppTest.from_file(str(PROGRAM / 'app/四川.py'),default_timeout=40)
        page.query_params.update(stage=key, scenario='dry_extreme')
        page.run()
        assert not page.exception and not page.error
        assert page.radio(key='dadu_workspace').value == expected
        assert page.radio(key='da_season').value == 'dry'
        assert page.radio(key='da_error_level').value == 'extreme'
