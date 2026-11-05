"""Requested UI verification participates in the overall CLI exit status."""
import importlib.util
from pathlib import Path
import sys
import pandas as pd
import pytest
from smm import pipeline
ROOT=Path(pipeline.__file__).resolve().parents[2]
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
