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
