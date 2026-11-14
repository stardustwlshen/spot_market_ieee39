"""Small file-based run receipts; no old live permissions or implicit retries."""
from pathlib import Path
import json,hashlib,uuid,os
B=Path(__file__).resolve().parent;ROOT=B.parent;PACKAGE=ROOT/'guangdong_market_delivery_20261112'
def need(ok,msg):
 if not ok:raise ValueError(msg)
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(x,f,sort_keys=True,separators=(',',':'),allow_nan=False)
def replace(p,x):
 p=Path(p);t=p.with_suffix('.pending.tmp');write(t,x);os.replace(t,p)
def ref(root,p):
 p=Path(p).resolve();return dict(path=str(p.relative_to(Path(root).resolve())),sha256=sha(p))
def resolve(root,r):
 p=(Path(root)/r['path']).resolve();need(p.is_relative_to(Path(root).resolve()),'path escapes current run');need(sha(p)==r['sha256'],'run artifact changed: '+str(p));return p
def own_sources():
 # DA dependency identity only. Downstream receipts must bind their own stage manifests.
 files=[B/n for n in ('run_protocol.py','da_pipeline.py','da_native.py')]+list((B/'da_math').rglob('*.py'))+list((PACKAGE/'vendor').rglob('*.py'))
 return {str(p.resolve()):sha(p)for p in files if p.exists()}
def verify_sources(run):
 d=read(Path(run)/'RUN.json')
 for p,h in d['source_pins'].items():need(sha(p)==h,'source changed during run')
 need(digest(d['source_pins'])==d['source_identity'],'source identity');return d
