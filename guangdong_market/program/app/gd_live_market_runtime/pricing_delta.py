"""Optional source-bound PRICE delta. Bounds are software support, not market rules."""
from pathlib import Path
import math,json,hashlib
DEFAULT=.1

def normalize(value=DEFAULT):
 if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
  raise ValueError('pricing_delta must be a finite research fraction in [0,1]; bool/string not accepted')
 return float(value)

def policy(value=DEFAULT):
 value=normalize(value)
 return dict(schema='gd.PRICE.research_delta.v1',value=value,unit='dimensionless',percent=value*100,official_numeric_verified=False,default_value=DEFAULT,default_basis='explicit research assumption, not authenticated Guangdong parameter',range_basis='implementation supports [0,1], not a regulatory allowed range',reference_basis='same-period RT-SCED user-selected reference; storage/current-mode mapping remains research',scope='new pipeline PRICE only; never rewrite historical accepted prices',recompute='create a new output with the changed value; existing prepared/native artifacts immutable')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def declare(value,*,branch_id,base_run,selected_DA,plan_path=None):
 z=dict(policy(value),branch_id=branch_id,base_run=str(Path(base_run).resolve()),selected_DA=str(Path(selected_DA).resolve()),case_sha256=sha(Path(base_run)/'seed/case.json'),DA_artifacts_sha256=sha(Path(selected_DA)/'ARTIFACTS.json'),selection_source='optional normal-entry advanced research input; default if unchanged',formal=False)
 if plan_path is not None:
  p=Path(plan_path).resolve();q=read(p)
  if q.get('pricing_delta_policy')!=policy(value):raise ValueError('PRICE delta differs from pipeline plan')
  z.update(plan_path=str(p),plan_sha256=sha(p))
 return z

def bind_source(z,path,expected_sha):
 p=Path(path).resolve()
 if sha(p)!=expected_sha:raise ValueError('pricing delta declaration changed; new run required')
 d=read(p);v=normalize(d.get('value'))
 for key,value in policy(v).items():
  if d.get(key)!=value:raise ValueError('invalid research delta metadata: '+key)
 if d.get('branch_id')!=z['branch_id'] or d.get('base_run')!=z['base_run'] or d.get('selected_DA')!=z['selected_DA']:raise ValueError('pricing delta from another run/DA')
 if d['case_sha256']!=sha(Path(z['base_run'])/'seed/case.json') or d['DA_artifacts_sha256']!=sha(Path(z['selected_DA'])/'ARTIFACTS.json'):raise ValueError('pricing delta input binding drift')
 if d.get('plan_path'):
  if sha(d['plan_path'])!=d['plan_sha256'] or read(d['plan_path']).get('pricing_delta_policy')!=policy(v):raise ValueError('pricing delta reviewed plan changed')
  z['source_pins'][d['plan_path']]=d['plan_sha256']
 z['source_pins'][str(p)]=expected_sha
 selection=dict(declaration_path=str(p),declaration_sha256=expected_sha,**d)
 z['pricing_delta_selection']=selection
 return selection

def from_source(z):
 d=z.get('pricing_delta_selection')
 if not isinstance(d,dict):raise ValueError('explicit source-bound research PRICE delta missing')
 p=d.get('declaration_path');h=d.get('declaration_sha256')
 if not p or z.get('source_pins',{}).get(p)!=h:raise ValueError('PRICE delta source pin missing')
 copy=dict(z);copy['source_pins']=dict(z['source_pins'])
 actual=bind_source(copy,p,h)
 if actual!=d:raise ValueError('PRICE delta selection changed in transport')
 return actual
