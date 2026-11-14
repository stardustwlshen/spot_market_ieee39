import sys,copy,json,importlib.util
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import controller as c

def facts():
 r=dict(run_id='new',input_identity='i',source_identity='s');a=dict(r,status='accepted',stage='da_scuc');d=dict(returncode=0);z=dict(native_status='Optimal',status='optimal',has_primal=True,dual_valid=True);k=dict(passed=False);return r,a,d,z,k

def test_eligible_failed_strict_only():c.gates(*facts())
@pytest.mark.parametrize('field',['accepted','candidate','unknown','passed','foreign'])
def test_reject(field):
 r,a,d,z,k=facts();kwargs={}
 if field=='accepted':kwargs['sced_accepted']=True
 if field=='candidate':kwargs['candidate_exists']=True
 if field=='unknown':z['native_status']='Time limit reached'
 if field=='passed':k['passed']=True
 if field=='foreign':a['run_id']='other'
 with pytest.raises(ValueError):c.gates(r,a,d,z,k,**kwargs)

def test_accepted_run_preflight_zero_native(tmp_path,monkeypatch):
 p=tmp_path/'da_sced';p.mkdir();(p/'ACCEPTANCE.json').write_text('{}')
 monkeypatch.setattr(c.subprocess,'run',lambda *a,**k:pytest.fail('must reject before subprocess'))
 with pytest.raises(ValueError,match='already accepted'):c.preflight(tmp_path,tmp_path/'engine')

def test_context_portable_and_tamper(tmp_path):
 src=Path(__file__).parent/'runtime/context_guard.py';target=tmp_path/'context_guard.py';target.write_bytes(src.read_bytes());(tmp_path/'RUN.json').write_text('{}');(tmp_path/'module.py').write_text('pass')
 context=dict(run=str(tmp_path),engine=str(tmp_path),run_sha256=c.sha(tmp_path/'RUN.json'),runtime_pins={'module.py':c.sha(tmp_path/'module.py')},engine_pins={})
 (tmp_path/'context.json').write_text(json.dumps(context));spec=importlib.util.spec_from_file_location('portable_ctx',target);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);assert m.load_context()['run']==str(tmp_path)
 (tmp_path/'module.py').write_text('changed')
 with pytest.raises(ValueError,match='runtime changed'):m.load_context()
