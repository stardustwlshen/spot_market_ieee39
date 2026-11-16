"""Verify separately downloaded economic archive before claiming durable completion."""
from pathlib import Path
import argparse,json,tarfile,tempfile,subprocess,sys,os
from .replay_economics import read,digest,require

def complete(output,archive,ack,ack_sha,config_path=None,config_sha=None):
 output=Path(output);a=json.loads(read(ack,ack_sha))
 require(isinstance(a,dict),'ACK must be object')
 require(a.get('schema')=='gd.restore.economics_download_verified.v1' and bool(a.get('library_file_id')),'independent Library ACK required')
 archive=Path(archive);raw=read(archive,a['archive_sha256']);require(len(raw)==a['archive_bytes'],'archive size')
 expected={'DERIVED_SETTLEMENT_H2.json','DERIVATION.json','PYTHON_GUARD.jsonl'}
 with tarfile.open(archive) as t:
  members=t.getmembers();require(len(members)==3 and {m.name for m in members}==expected and all(m.isfile() for m in members),'archive inventory')
  for name in sorted(expected):require(t.extractfile(name).read()==(output/name).read_bytes(),'download differs: '+name)
 proof=json.loads((output/'DERIVATION.json').read_bytes())
 require(proof.get('schema')=='gd.restore.derived_economics.v1','unsupported derivation schema')
 require(proof['new_native_calls']==0 and proof['source_files_unchanged'] and proof['research_economics_derived'],'derivation incomplete')
 require(digest((output/'DERIVED_SETTLEMENT_H2.json').read_bytes())==proof['derived_sha256'],'derived hash')
 codepins=proof.get('replay_code_pins',{})
 required={str(p.resolve()) for p in Path(__file__).parent.rglob('*.py')}
 require(required and set(codepins)==required,'replay code pins incomplete')
 for p,h in codepins.items():read(p,h)
 require(isinstance(proof.get('source_pins'),dict) and proof['source_pins'],'empty source pins')
 require(config_path and config_sha,'independently selected config required')
 require(str(Path(config_path).resolve())==proof.get('config_path') and config_sha==proof['config_sha256'],'config identity mismatch')
 c=json.loads(read(config_path,config_sha))
 require(c['source_pins']==proof['source_pins'],'config/source pins mismatch')
 require(Path(c['output']).resolve()==output.resolve(),'config output mismatch')
 for path,h in proof['source_pins'].items():read(path,h)
 guard=[json.loads(line) for line in (output/'PYTHON_GUARD.jsonl').read_text().splitlines()]
 require(len(guard)==2 and guard[0]['kind']=='started' and guard[1]['kind']=='exited' and guard[0]['pid']==guard[1]['pid'],'guard evidence incomplete')
 # Recompute from pinned inputs in a fresh guarded process; never trust success flags.
 with tempfile.TemporaryDirectory(prefix='economics_verify_') as tmp:
  tmp=Path(tmp);verify=dict(c,output=str(tmp/'verified'));cp=tmp/'config.json';cp.write_text(json.dumps(verify))
  env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
  subprocess.run([sys.executable,'-B','-m','gd_restore_crosshour_runtime.replay_economics','--config',str(cp),'--config-sha',digest(cp.read_bytes())],env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  require(json.loads((tmp/'verified/DERIVED_SETTLEMENT_H2.json').read_bytes())==json.loads((output/'DERIVED_SETTLEMENT_H2.json').read_bytes()),'independent economic recomputation mismatch')
 completion={'schema':'gd.restore.economics_complete.v1','economics_derived_verified':True,'formal_profit_verified':False,'new_native_calls':0,'derived_sha256':proof['derived_sha256'],'config_sha256':proof['config_sha256'],'ack_sha256':ack_sha,'library_file_id':a['library_file_id'],'archive_sha256':a['archive_sha256']}
 with (output/'ECONOMICS_COMPLETE.json').open('x')as f:json.dump(completion,f,indent=2)
 return completion

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--archive',required=True);p.add_argument('--ack',required=True);p.add_argument('--ack-sha',required=True);p.add_argument('--config',required=True);p.add_argument('--config-sha',required=True);a=p.parse_args();print(json.dumps(complete(a.output,a.archive,a.ack,a.ack_sha,a.config,a.config_sha)))
if __name__=='__main__':main()
