from pathlib import Path
import sys
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from runtime_notices import runtime_status
from smm.runtime_identity import source_digest


def source(tmp_path):
    (tmp_path/'requirements-app.txt').write_text('')
    (tmp_path/'src').mkdir()
    (tmp_path/'src/a.py').write_text('x=1\n')
    return dict(IEEE39_LAUNCH_SOURCE_SHA256=source_digest(tmp_path),IEEE39_LAUNCH_ROOT=str(tmp_path),IEEE39_LAUNCH_INTERPRETER='/usr/bin/fakepython')


def test_matching_source_and_interpreter_do_not_claim_restart(tmp_path):
    env=source(tmp_path)
    result=runtime_status(tmp_path,environ=env,executable='/usr/bin/fakepython')
    assert result['version_recorded'] and not result['needs_restart']


def test_updated_source_is_detected_without_changing_files(tmp_path):
    env=source(tmp_path);p=tmp_path/'src/a.py';p.write_text('x=2\n')
    result=runtime_status(tmp_path,environ=env,executable='/usr/bin/fakepython')
    assert result['needs_restart'] and '代码' in result['issues'][0]
    assert p.read_text()=='x=2\n'


def test_manual_start_is_unknown_not_false_mismatch(tmp_path):
    result=runtime_status(tmp_path,environ={},executable='/usr/bin/fakepython')
    assert not result['version_recorded'] and not result['needs_restart']


def test_environment_mismatch_is_visible(tmp_path):
    env=source(tmp_path)
    result=runtime_status(tmp_path,environ=env,executable='/other/python')
    assert result['needs_restart'] and any('Python' in text for text in result['issues'])


def test_stale_runtime_banner_does_not_crash_or_delete_data(tmp_path,monkeypatch):
    env=source(tmp_path)
    for key,value in env.items():monkeypatch.setenv(key,value)
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom runtime_notices import render_runtime_notice\nrender_runtime_notice({str(tmp_path)!r})"
    at=AppTest.from_string(code).run()
    assert not at.exception and at.warning
    assert any('原场景与结果无需删除' in x.value for x in at.warning)
    assert at.session_state['runtime_needs_restart']
