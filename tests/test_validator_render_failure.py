"""Requested UI verification participates in the overall CLI exit status."""
import importlib.util
from pathlib import Path
import sys
import pandas as pd
import pytest
from smm import pipeline
ROOT=Path(pipeline.__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from result_navigation import MAIN_SECTIONS
spec=importlib.util.spec_from_file_location('render_validator_probe',ROOT/'scripts/validate_runs.py')
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)


@pytest.mark.parametrize('rendered', [
    {'渲染异常数':1,'三大块渲染':False}, {'渲染异常数':0,'三大块渲染':False},
    {'渲染异常数':0}, {'三大块渲染':True}, {},
    {'渲染异常数':'0','三大块渲染':True}, {'渲染异常数':float('nan'),'三大块渲染':True},
])
def test_requested_failed_or_incomplete_render_fails_acceptance(monkeypatch,tmp_path,rendered):
    monkeypatch.setattr(validator,'metrics',lambda d:{})
    monkeypatch.setattr(validator,'checks',lambda d:{'验收通过':True,'说明':'data verified'})
    monkeypatch.setattr(validator,'render_check',lambda d:rendered)
    out=tmp_path/'result.csv'
    monkeypatch.setattr(sys,'argv',['validate_runs.py','run','--render','--out',str(out)])
    assert validator.main()==1
    row=pd.read_csv(out).iloc[0]
    assert not row['验收通过'] and not row['渲染通过']


def test_batch_continues_and_cannot_hide_one_render_failure(monkeypatch,tmp_path):
    seen=[]
    monkeypatch.setattr(validator,'metrics',lambda d:{})
    monkeypatch.setattr(validator,'checks',lambda d:{'验收通过':True})
    def render(d):
        seen.append(d.name)
        return {'渲染异常数':1 if d.name=='bad' else 0,'三大块渲染':d.name!='bad'}
    monkeypatch.setattr(validator,'render_check',render)
    monkeypatch.setattr(sys,'argv',['validate_runs.py','bad','good','--render'])
    assert validator.main()==1 and seen==['bad','good']


def test_unrequested_render_is_not_invented_and_successful_render_passes(monkeypatch):
    monkeypatch.setattr(validator,'metrics',lambda d:{})
    monkeypatch.setattr(validator,'checks',lambda d:{'验收通过':True})
    seen=[]
    monkeypatch.setattr(validator,'render_check',lambda d:seen.append(d) or {'渲染异常数':0,'三大块渲染':True})
    monkeypatch.setattr(sys,'argv',['validate_runs.py','good'])
    assert validator.main()==0 and not seen
    monkeypatch.setattr(sys,'argv',['validate_runs.py','good','--render'])
    assert validator.main()==0 and len(seen)==1


@pytest.mark.parametrize('missing', [None, *MAIN_SECTIONS])
def test_render_check_uses_current_shared_main_sections(monkeypatch, tmp_path, missing):
    from types import SimpleNamespace
    from streamlit.testing.v1 import AppTest
    import common
    from result_navigation import MAIN_SECTIONS
    original = common.all_results
    labels = [label for label in MAIN_SECTIONS if label != missing]
    # Include nested tabs, which must not be confused with the main stage blocks.
    labels += ['实时执行与前瞻', '日内轮次与交接', '电能量结算']
    at = SimpleNamespace(exception=[], tabs=[SimpleNamespace(label=label) for label in labels],
                         get=lambda kind: [])
    at.run = lambda: at
    monkeypatch.setattr(AppTest, 'from_file', lambda *args, **kwargs: at)
    result = validator.render_check(tmp_path)
    assert result['渲染异常数'] == 0
    assert result['三大块渲染'] is (missing is None)
    assert common.all_results is original
