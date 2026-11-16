"""Small adversarial gate tests, no solver calls."""
import json,sys,hashlib
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_restore_runtime.preflight import check
from gd_restore_runtime.historical_store import HistoricalStore

def test_config_tamper(tmp_path):
 p=tmp_path/'c';p.write_text('{}')
 with pytest.raises(ValueError,match='changed'):check(p,'0'*64)
def test_unknown_schema(tmp_path):
 p=tmp_path/'c';p.write_text('{"schema":"unknown"}')
 with pytest.raises(ValueError,match='schema'):check(p,hashlib.sha256(p.read_bytes()).hexdigest())
def test_unknown_override(tmp_path):
 p=tmp_path/'c';p.write_text('{"schema":"gd.restore.q03_two_steps.v1","mip_gap":0.9}')
 with pytest.raises(ValueError,match='override'):check(p,hashlib.sha256(p.read_bytes()).hexdigest())
def test_store_versions_and_tamper(tmp_path):
 a=tmp_path/'a';a.write_bytes(b'a');h=hashlib.sha256(b'a').hexdigest();b=tmp_path/'b';b.write_bytes(b'b');h2=hashlib.sha256(b'b').hexdigest();p=tmp_path/'m';p.write_text(json.dumps({'map':{'/old/mutable':{h:{'path':str(a),'bytes':1},h2:{'path':str(b),'bytes':1}}}}));s=HistoricalStore(p,hashlib.sha256(p.read_bytes()).hexdigest())
 with pytest.raises(ValueError,match='ambiguous'):s.bytes('/old/mutable')
 assert s.bytes('/old/mutable',h)==b'a'
 a.write_bytes(b'x')
 with pytest.raises(ValueError,match='changed'):s.bytes('/old/mutable',h)
def test_unsupported_platform(monkeypatch,tmp_path):
 monkeypatch.setattr(sys,'platform','win32')
 with pytest.raises(ValueError,match='Linux'):check(tmp_path/'missing','0'*64)

@pytest.mark.parametrize('changes,reason',[
 ({'policy':'local_only'},'ACK required'),
 ({'execution_attempt_id':'../overwrite'},'attempt'),
 ({'transport':'local_copy'},'coordinator'),
 ({'timeout_s':0},'timeout'),
])
def test_unsupported_execution_settings(tmp_path,changes,reason):
 c={'schema':'gd.restore.q03_two_steps.v1','policy':'cloud_required','cadence':'quarter','require_download_ack':True,'transport':'dot_library_coordinator','coordinator_operated':True,'timeout_s':3600,'execution_attempt_id':'new_attempt'};c.update(changes)
 p=tmp_path/'c';p.write_text(json.dumps(c))
 with pytest.raises(ValueError,match=reason):check(p,hashlib.sha256(p.read_bytes()).hexdigest())

def test_runtime_shadow_rejected(tmp_path):
 from gd_restore_runtime.preflight import verify_runtime_module
 original=tmp_path/'original';shadow=tmp_path/'shadow';original.mkdir();shadow.mkdir();a=original/'engine.py';b=shadow/'engine.py';a.write_bytes(b'good');b.write_bytes(b'good');pins={str(a):hashlib.sha256(a.read_bytes()).hexdigest()}
 verify_runtime_module(a,original,pins)
 with pytest.raises(ValueError,match='outside'):verify_runtime_module(b,original,pins)
 a.write_bytes(b'evil')
 with pytest.raises(ValueError,match='changed'):verify_runtime_module(a,original,pins)
def test_direct_runner_owner_rejected(tmp_path):
 from gd_restore_runtime.preflight import verify_runner_owner
 report=tmp_path/'report';report.write_text('{}');h=hashlib.sha256(report.read_bytes()).hexdigest();(tmp_path/'OWNER.json').write_text(json.dumps({'pid':123,'config_sha256':'fixed','preflight_sha256':h}))
 verify_runner_owner(tmp_path,'fixed',report,h,{},123)
 with pytest.raises(ValueError,match='owner'):verify_runner_owner(tmp_path,'fixed',report,h,{},456)
 with pytest.raises(ValueError,match='owner'):verify_runner_owner(tmp_path,'changed',report,h,{},123)
