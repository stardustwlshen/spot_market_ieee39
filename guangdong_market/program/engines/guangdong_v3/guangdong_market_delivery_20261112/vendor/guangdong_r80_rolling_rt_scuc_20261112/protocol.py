"""Causal source and immutable emitted-prefix handoff. No optimizer calls."""
from model import *
import json,hashlib
from copy import deepcopy
sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.highs_solver import serialize_model,validate_options
from smm.gd_r23_equivalent_compression import audit
B=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,sort_keys=True,separators=(',',':'),allow_nan=False)
def serialize(m):
 q,vs,cs=serialize_model(m,validate_options(dict(time_limit=60.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False)));q['preparation_elapsed_s']=0.
 return q,dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]),vs
def control_history(q,n,x,window):
 """First three issued physical cells; all prior commitments retained in absolute clock."""
 cutoff=3*window;end=6+3*window;fixed={}
 for j,name in enumerate(n['variables']):
  if '['not in name:continue
  indices=name.split('[',1)[1][:-1].split(',')
  if name.startswith('direction['):
   hour=indices[0];last=(ORIGIN+timedelta(minutes=5*(end-1))).strftime('%Y-%m-%dT%H')
   if hour<=last:fixed[name]=x[j]
   continue
  try:t=int(indices[-1]if name.startswith(('P[','Rpos[','Racc['))else indices[0])
  except ValueError:continue
  if name.startswith('fast.notice['):take=t<cutoff+3 # issued notice at cutoff plus sealed zero off-grid notice history
  elif name.startswith(('E[','U[')):take=t<=end
  else:take=t<end
  if take:fixed[name]=x[j]
 notices=[];starts=[]
 for j,name in enumerate(n['variables']):
  if x[j]>.5 and name.startswith('fast.notice['):
   t,k=name[12:-1].split(',')
   if int(t)<=cutoff:notices.append(dict(event_id='R80_FAST_NOTICE_'+t+'_'+k,notice_tick=int(t),kind=k,connection_tick=int(t)+dto().curves[k].notice_lead_ticks,ignition_tick=int(t),source='actual accepted SCUC/SCED integer event'))
  if x[j]>.5 and name.startswith('fast.kind['):
   t,k=name[10:-1].split(',')
   if int(t)<end:starts.append(dict(event_id='R80_FAST_CONNECT_'+t+'_'+k,connection_tick=int(t),kind=k,curve_cursor_at_next_delivery=end-int(t),curve_length=len(dto().curves[k].mean_mw)))
 return dict(schema='gd.research.r80.issued_prefix.v1',window=window,receipt_cutoff_tick=cutoff,executed_physical_end=cutoff,issued_physical_end=end,preparation_cells=3,issued_RT_cells=3*(window+1),executed_RT_cells=max(0,cutoff-3),fixed=fixed,notices=notices,started=starts,future_actuals_used=False)
def bind_history(m,history):
 need(history['window']==0 and m._window==1,'only sequential same-controller handoff')
 mapping={v.name:v for v in m.component_data_objects(p.Var)}
 for name,value in history['fixed'].items():
  need(name in mapping,'history variable missing');v=mapping[name]
  need((v.lb is None or value>=v.lb-1e-7)and(v.ub is None or value<=v.ub+1e-7),'history outside original numerical bounds');v.fix(value)
 # No new notifications before current receipt cutoff; never fabricate history.
 for t in range(3):
  for k in CLASSES:need(m.fast.notice[t,k].fixed,'past notification not sealed')
 return m

def fixed_sced(q,n,x):
 z=deepcopy(q)
 for v,value in zip(z['variables'],x):
  if v['kind']!='C':need(abs(value-round(value))<=1e-6,'SCUC integer tolerance');v.update(kind='C',lb=float(round(value)),ub=float(round(value)),start=None)
 z['dual']=True;return z

def save_prepared(path,q,n,context):
 path=Path(path);path.mkdir(parents=True,exist_ok=False);write(path/'template.json',q);write(path/'names.json',n);write(path/'CONTEXT.json',context)
 pins={str(x.resolve()):sha(x)for x in [B/'model.py',B/'protocol.py',ROOT/'guangdong_r68_three_state_builder_20261112/events5.py']}
 write(path/'READY.json',dict(schema='gd.research.r80.actual_request_ready.v1',native_calls=0,source_pins=pins,artifact_hashes={p.name:sha(p)for p in path.iterdir()},variables=len(q['variables']),rows=len(q['rows']),context=context))
