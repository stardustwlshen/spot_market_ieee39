"""PRICE research loss inheritance. No acceptance or execution authority."""
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib,math
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def quarter_losses(profile,case,first,origin):
 need(profile['schema']=='gd.curve.loss.next_iteration.v1' and profile['iteration']==1,'supported one loss iteration only')
 points=[p for p in profile['points'] if p['block']=='near'];need(len(points)==24,'near24 required');nodes=case['energy_inputs']['buses'];start=datetime.fromisoformat(origin)+timedelta(minutes=5*first)
 for t,p in enumerate(points):
  need(p['local_tick']==t and p['global_tick']==first+t,'loss interval mismatch')
  need(datetime.fromisoformat(p['start'])==start+timedelta(minutes=5*t) and datetime.fromisoformat(p['end'])==start+timedelta(minutes=5*(t+1)),'loss clock mismatch')
  need(set(p['node_loss_mw'])==set(nodes),'loss node inventory')
  for b in nodes:
   need(abs(case['energy_inputs']['load_mw'][b,first+t]-p['gross_load'][str(int(b)-1)]['p_mw'])<1e-8,'gross case load mismatch')
   need(math.isfinite(p['node_loss_mw'][b]) and p['node_loss_mw'][b]>=-1e-8,'invalid nodal loss')
  need(abs(math.fsum(p['node_loss_mw'].values())-p['total_loss_mw'])<1e-7,'loss total mismatch')
 return {b:[math.fsum(points[3*q+k]['node_loss_mw'][b] for k in range(3))/3 for q in range(8)] for b in nodes}
def inherit(*,near,profile_path,receipt_path,scuc,sced,case,first,origin):
 # Called ONLY after real session export_generated_pair + worker checks.
 near=Path(near).resolve();pp=near/'LOSS_PROFILE.json';rp=near/'LOSS_OVERLAY.json';present=pp.exists() or rp.exists()
 if not present:
  need(profile_path is None and receipt_path is None,'loss overlay not registered in accepted near');return None,None,{}
 need(profile_path is not None and receipt_path is not None,'accepted near has loss: explicit inheritance mandatory')
 need(Path(profile_path).resolve()==pp and Path(receipt_path).resolve()==rp,'use registered near loss artifacts only')
 profile=read(pp);receipt=read(rp);template=read(near/'template.json')
 need(receipt['block']=='near' and receipt['phase']=='SCUC' and receipt['iteration']==1,'near SCUC overlay receipt required')
 need(receipt['profile_digest']==digest(profile),'profile digest mismatch')
 need(receipt['output_model_digest']==digest(template) and all(scuc[k]==template[k] for k in ('rows','variables','objective')),'accepted model not receipt model')
 need(scuc['rows']==sced['rows'],'SCED dropped/changed loss rows')
 for p,h in profile['source_pins'].items():need(sha(p)==h,'loss source drift')
 mapped=quarter_losses(profile,case,first,origin)
 trace=dict(profile_digest=digest(profile),receipt_sha256=sha(rp),quarter_node_loss_mw=mapped,basis='model-derived branch-end half allocation; three-cell arithmetic mean; research only',actual_arrival_at=None,gross_load_unchanged=True,positive_headroom_and_accident_400_unchanged=True,negative_reserve_uses_effective_load=True,source_acceptance='existing session export_generated_pair; receipt is not independent authority')
 return mapped,trace,{str(pp):sha(pp),str(rp):sha(rp),**profile['source_pins']}

def verify_registered_artifacts(near,record):
 near=Path(near).resolve();pins=record['artifact_pins']
 for path,h in pins.items():need(sha(path)==h,'registered prepared artifact drift: '+str(path))
 for name in ('LOSS_PROFILE.json','LOSS_OVERLAY.json'):
  path=near/name
  if path.exists():need(str(path) in pins and sha(path)==pins[str(path)],'unregistered loss artifact: '+name)
 return True
