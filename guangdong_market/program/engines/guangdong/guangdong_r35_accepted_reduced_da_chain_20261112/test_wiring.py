"""Small fake-transport negative wiring checks only. No source/solver reproduction."""
from pathlib import Path
from copy import deepcopy
import pytest
import common

def case(monkeypatch,status='optimal',native='Optimal',gap=.0005,restricted=False):
 monkeypatch.setattr(common,'verified_worker_override',lambda native:{'fixture_only':True})
 q={'variables':[{'kind':'B','lb':0,'ub':1}for _ in range(2160)],'rows':[],'objective':{},'options':{}}
 if restricted:q['research_restriction']={'global_bound_valid':False}
 a={'native_request_sha256':'Q'};meta={'artifact_hashes':{}}
 def read(p):
  s=str(p)
  if s.endswith('PURE_PREPARATION.json'):return meta
  if s.endswith('template.json'):return q
  return a
 monkeypatch.setattr(common,'read',read)
 monkeypatch.setattr(common,'sha',lambda p:'524cdd007fd6a3071bd5d434cf8351209f33fcd770657898837cc8fda8e31814')
 monkeypatch.setattr(common,'load_event',lambda p:{'request':q,'response':{'status':status,'native_status':native},'pins':{'request':{'sha256':'Q'}}})
 monkeypatch.setattr(common,'proof_files',lambda p:{'test':1});monkeypatch.setattr(common,'proof_bundle',lambda p:p)
 monkeypatch.setattr(common,'assess_scuc',lambda *args:{'accepted':gap<=.001})
 return q

def test_accepted_transport_route(monkeypatch):
 case(monkeypatch);assert common.accepted_r34(Path('new'),Path('audit'))[1]['accepted']
@pytest.mark.parametrize('status,native',[('maxTimeLimit','Time limit reached'),('optimal','Time limit reached')])
def test_reject_time_limit_even_low_gap(monkeypatch,status,native):
 case(monkeypatch,status,native)
 with pytest.raises(ValueError,match='actual Optimal'):common.accepted_r34(Path('new'),Path('audit'))
def test_reject_excess_gap(monkeypatch):
 case(monkeypatch,gap=.0035)
 with pytest.raises(ValueError,match='0.1%'):common.accepted_r34(Path('new'),Path('audit'))
def test_reject_restricted_bound(monkeypatch):
 case(monkeypatch,restricted=True)
 with pytest.raises(ValueError,match='restricted'):common.accepted_r34(Path('new'),Path('audit'))
def test_reject_fixed_global_decision(monkeypatch):
 q=case(monkeypatch);q['variables'][0]['ub']=0
 with pytest.raises(ValueError,match='remain free'):common.accepted_r34(Path('new'),Path('audit'))
