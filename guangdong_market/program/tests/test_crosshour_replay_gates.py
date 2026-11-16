from pathlib import Path
import sys,json,hashlib
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_restore_crosshour_runtime.replay_economics import replay,read

def save(tmp_path,c):
 p=tmp_path/'config.json';p.write_text(json.dumps(c));return p,hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.mark.parametrize('bad',[None,[],{}, {'schema':'gd.restore.q03_two_steps.v1'}])
def test_wrong_schema(tmp_path,bad):
 p,h=save(tmp_path,bad)
 with pytest.raises(ValueError,match='schema'):replay(p,h)

def test_sha_gate(tmp_path):
 p,h=save(tmp_path,{})
 with pytest.raises(ValueError,match='SHA'):replay(p,'0'*64)

def test_unknown_reader(tmp_path):
 p,h=save(tmp_path,{'schema':'gd.restore.q07_h2_economics_replay.v1','reader_sha256':'0'*64})
 with pytest.raises(ValueError,match='reader'):replay(p,h)

def test_other_hour(tmp_path):
 p,h=save(tmp_path,{'schema':'gd.restore.q07_h2_economics_replay.v1','reader_sha256':'92c3f4e0a7fe61d3992991aa7fb90a948755fb9d71282bef4a370fe1024baefa','hour':3,'cells':[12,13,14,15]})
 with pytest.raises(ValueError,match='H2'):replay(p,h)
from gd_restore_crosshour_runtime.complete_economics import complete
@pytest.mark.parametrize('value',[None,[],{}, {'schema':'wrong'}])
def test_completion_requires_ack(tmp_path,value):
 p,h=save(tmp_path,value)
 with pytest.raises(ValueError):complete(tmp_path,tmp_path/'absent.tar.gz',p,h)

def test_completion_no_archive_is_not_success(tmp_path):
 p,h=save(tmp_path,{'schema':'gd.restore.economics_download_verified.v1','library_file_id':'test','archive_sha256':'0'*64,'archive_bytes':1})
 with pytest.raises(FileNotFoundError):complete(tmp_path,tmp_path/'absent.tar.gz',p,h)
 assert not (tmp_path/'ECONOMICS_COMPLETE.json').exists()

def test_ack_bytes_must_match_local_derived(tmp_path):
 import tarfile,io
 out=tmp_path/'output';out.mkdir()
 (out/'DERIVATION.json').write_text('{}');(out/'DERIVED_SETTLEMENT_H2.json').write_text('{}')
 archive=tmp_path/'bundle.tar.gz'
 with tarfile.open(archive,'w:gz')as t:
  for name in ('DERIVATION.json','DERIVED_SETTLEMENT_H2.json','PYTHON_GUARD.jsonl'):
   b=b'{"different":true}';m=tarfile.TarInfo(name);m.size=len(b);t.addfile(m,io.BytesIO(b))
 p,h=save(tmp_path,{'schema':'gd.restore.economics_download_verified.v1','library_file_id':'test','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'archive_bytes':archive.stat().st_size})
 with pytest.raises(ValueError,match='differs'):complete(out,archive,p,h)
 assert not (out/'ECONOMICS_COMPLETE.json').exists()
