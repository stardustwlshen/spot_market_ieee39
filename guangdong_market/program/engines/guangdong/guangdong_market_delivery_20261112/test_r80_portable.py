from pathlib import Path
import sys,json,subprocess,shutil
ROOT=Path(__file__).resolve().parent
def test_r80_relocated_import_build_dryrun(tmp_path):
 p=tmp_path/'copy';shutil.copytree(ROOT,p,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
 elsewhere=tmp_path/'cwd';elsewhere.mkdir()
 def call(*args):return subprocess.run([sys.executable,'-B',str(p/'market.py'),*args],cwd=elsewhere,capture_output=True,text=True)
 r=call('rt-small-fixture');assert r.returncode==0,r.stderr;assert json.loads(r.stdout)['schema']=='gd.research.r80.explicit_fixture.v1'
 r=call('prepare-rt-small','--output',str(tmp_path/'w0'));assert r.returncode==0,r.stdout+r.stderr
 x=json.loads(r.stdout);assert x['variables']==1361 and x['rows']==2439 and x['native_calls']==0
 r=call('run-rt-small','--output',str(tmp_path/'native'));assert r.returncode==0,r.stdout+r.stderr
 assert json.loads(r.stdout)['native_calls']==0 and not(tmp_path/'native').exists()
