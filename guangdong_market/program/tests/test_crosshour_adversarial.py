"""Counterexamples: incomplete closure and self-attested completion must fail."""
from pathlib import Path
import hashlib,json,sys,tarfile,io
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_restore_crosshour_runtime.complete_economics import complete
from gd_restore_crosshour_runtime.replay_economics import read

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def package(tmp_path,proof,derived):
 out=tmp_path/'out';out.mkdir()
 for name,value in [('DERIVATION.json',proof),('DERIVED_SETTLEMENT_H2.json',derived)]: (out/name).write_text(json.dumps(value))
 (out/'PYTHON_GUARD.jsonl').write_text('{"kind":"started","pid":123}\n{"kind":"exited","pid":123}\n')
 archive=tmp_path/'bundle.tar.gz'
 with tarfile.open(archive,'w:gz')as t:
  for p in out.iterdir():t.add(p,arcname=p.name)
 ack=tmp_path/'ack.json';ack.write_text(json.dumps({'schema':'gd.restore.economics_download_verified.v1','library_file_id':'TEST_NOT_REAL','archive_sha256':sha(archive),'archive_bytes':archive.stat().st_size}))
 return out,archive,ack,sha(ack)

def fake_proof(tmp_path,pins):
 return {'schema':'gd.restore.derived_economics.v1','replay_code_pins':{str(p.resolve()):sha(p) for p in (Path(__file__).parents[1]/'app/gd_restore_crosshour_runtime').rglob('*.py')},'new_native_calls':0,'source_files_unchanged':True,'research_economics_derived':True,'source_pins':pins,'derived_sha256':hashlib.sha256(b'{}').hexdigest(),'config_sha256':'0'*64}

def test_empty_pins_self_attestation_rejected(tmp_path):
 args=package(tmp_path,fake_proof(tmp_path,{}),{})
 with pytest.raises(ValueError,match='empty source pins'):complete(*args)
 assert not (args[0]/'ECONOMICS_COMPLETE.json').exists()

def test_nonempty_arbitrary_pins_not_config_authority(tmp_path):
 p=tmp_path/'arbitrary.txt';p.write_text('foo')
 args=package(tmp_path,fake_proof(tmp_path,{str(p):sha(p)}),{})
 with pytest.raises(ValueError,match='independently selected config'):complete(*args)

def test_bad_dependency_bytes_rejected(tmp_path):
 p=tmp_path/'dependency.py';p.write_text('original=1');h=sha(p);p.write_text('changed=1')
 with pytest.raises(ValueError,match='SHA'):read(p,h)

from gd_restore_crosshour_runtime.replay_economics import verify_runtime_closure

def test_bridge_only_pin_rejects_nested_dependency(tmp_path):
 bridge=tmp_path/'gd_settlement_bridge.py';bridge.write_text('# bridge')
 nested=tmp_path/'vendor';nested.mkdir();dep=nested/'rule.py';dep.write_text('# actual dependency')
 with pytest.raises(ValueError,match='dependency pin absent'):verify_runtime_closure(tmp_path,{str(bridge):sha(bridge)})

def test_complete_closure_then_dependency_change(tmp_path):
 bridge=tmp_path/'gd_settlement_bridge.py';bridge.write_text('# bridge')
 dep=tmp_path/'cost.py';dep.write_text('# cost')
 pins={str(p):sha(p) for p in (bridge,dep)}
 verify_runtime_closure(tmp_path,pins)
 dep.write_text('# substituted cost')
 with pytest.raises(ValueError,match='SHA'):verify_runtime_closure(tmp_path,pins)

def test_completion_cannot_select_its_own_config(tmp_path):
 p=tmp_path/'arbitrary.txt';p.write_text('foo')
 proof=fake_proof(tmp_path,{str(p):sha(p)});proof['config_path']=str(tmp_path/'different.json')
 args=package(tmp_path,proof,{})
 with pytest.raises(ValueError,match='config identity mismatch'):complete(*args,config_path=tmp_path/'expected.json',config_sha='0'*64)


def test_wrong_proof_schema(tmp_path):
 proof=fake_proof(tmp_path,{});proof['schema']='invented'
 args=package(tmp_path,proof,{})
 with pytest.raises(ValueError,match='derivation schema'):complete(*args)
