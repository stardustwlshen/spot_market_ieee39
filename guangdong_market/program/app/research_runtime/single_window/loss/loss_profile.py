"""Numerical loss feedback interface only; no ownership/solver/acceptance authority."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import json,hashlib,math,importlib.util
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,msg):
 if not x:raise ValueError(msg)
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def make_profile(*,ac_directory,evidence_manifest,evidence_manifest_sha256):
 root=Path(ac_directory).resolve();mf=Path(evidence_manifest).resolve();need(sha(mf)==evidence_manifest_sha256,'evidence inventory drift');inv=read(mf)['files']
 def checked(p):
  entries=[x for x in inv if str(p).endswith('/'+x['path'])];need(len(entries)==1 and entries[0]['sha256']==sha(p),'AC evidence changed: '+str(p));return read(p)
 binding=checked(root/'SOURCE_BINDING.json');summary=checked(root/'SUMMARY.json');need(summary['coverage']==48 and summary['converged']==48,'complete converged48 required')
 for f,h in binding['pins'].items():need(sha(f)==h,'upstream source drift')
 points=[];pins={str(mf):sha(mf),str(root/'SOURCE_BINDING.json'):sha(root/'SOURCE_BINDING.json')}
 for row in summary['results']:
  d=root/f"T{row['global_tick']:03}";m=checked(d/'MAPPING.json');a=checked(d/'AC_RESULT.json');p=checked(d/'PAYLOAD.json')
  need(a['ac_converged']and a['input_sha256']==digest(m['ac_input']) and m['source_payload_sha256']==digest(p),'AC exact input binding')
  need(p['loss_proxy_mw']==0,'only first iteration from zero-loss model')
  need(all(b['gs_mw']==0 for b in m['ac_input']['buses']),'unallocated real shunt')
  branches={b['id']:b for b in m['ac_input']['branches']};need(set(branches)=={b['id']for b in a['branches']},'branch inventory')
  loss={str(int(b['id'])+1):0. for b in m['ac_input']['buses']}
  for z in a['branches']:
   mw=math.fsum([z['from_p_mw'],z['to_p_mw']]);need(math.isfinite(mw)and mw>=-1e-8,'unexpected negative real loss')
   for side in ('from_bus','to_bus'):loss[str(int(branches[z['id']][side])+1)]+=mw/2
  need(abs(math.fsum(loss.values())-row['loss_MW'])<1e-7,'loss total mismatch')
  points.append(dict(block=row['stage'],local_tick=row['local_tick'],global_tick=row['global_tick'],start=p['start'],end=p['end'],node_loss_mw=loss,total_loss_mw=math.fsum(loss.values()),gross_load=deepcopy(p['gross_load']),source_AC_sha256=sha(d/'AC_RESULT.json')))
  for f in ['MAPPING.json','AC_RESULT.json','PAYLOAD.json']:pins[str(d/f)]=sha(d/f)
 for block in ('near','far'):
  rows=[x for x in points if x['block']==block];need([x['local_tick']for x in rows]==list(range(24)),'complete ordered24 stage')
 need(points[24]['global_tick']==points[23]['global_tick']+1 and points[24]['start']==points[23]['end'],'near far time seam')
 return dict(schema='gd.curve.loss.next_iteration.v1',iteration=1,max_candidate_iterations=1,points=points,source_pins=pins,basis='model-derived next-iteration loss estimate; not measured demand',actual_arrival_at=None,original_seed_unchanged=True,physical_AC_load_unchanged=True,control_or_voltage_fix=False,native_execution_allowed=False,release=False)
class StageLossOverlay:
 def __init__(self,*,program_root,profile):
  self.source_path=Path(__file__).resolve();self.source_sha=sha(self.source_path);self.profile=deepcopy(profile);self.seal=digest(profile);self.used=set();path=Path(program_root)/'app/loss_feedback.py';spec=importlib.util.spec_from_file_location('_existing_loss_rows',path);self.helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.helper);self.helper_path=path;self.helper_sha=sha(path)
 def apply(self,template,names,case,*,block,phase):
  need(digest(self.profile)==self.seal and self.profile['iteration']==1,'profile drift/iteration')
  need(block in ('near','far') and phase in ('SCUC','SCED'),'explicit stage/phase');need((block,phase)not in self.used,'one candidate application only')
  need(sha(self.helper_path)==self.helper_sha,'loss helper drift')
  for p,h in self.profile['source_pins'].items():need(sha(p)==h,'source changed')
  points=[x for x in self.profile['points']if x['block']==block];need(len(points)==24,'stage coverage')
  start=datetime.fromisoformat(case['contract']['domain_start']);need(case['contract']['stage']['block']==block and case['contract']['stage']['count']==24,'wrong stage')
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 changed')
  q=deepcopy(template);v={s.replace("'",''):i for i,s in enumerate(names['variables'])};changed=[];nodes=case['energy_inputs']['buses']
  for t,point in enumerate(points):
   need(point['start']==(start+timedelta(minutes=5*t)).isoformat()and point['global_tick']==case['contract']['stage']['start_tick']+t,'point mapping')
   for node in nodes:need(abs(case['energy_inputs']['load_mw'][node,t]-point['gross_load'][str(int(node)-1)]['p_mw'])<1e-8,'gross physical load drift')
   cols=[v[f'renewable_power[{node},{t}]']for node in nodes];matches=[]
   for name,row in zip(names['constraints'],q['rows']):
    if name.startswith('reserves[')and all(dict(row['terms']).get(j)==1 for j in cols):matches.append((name,row))
   pos=[n for n,r in matches if r['lb']is not None and r['ub']is None];neg=[n for n,r in matches if r['ub']is not None and r['lb']is None];need(len(pos)==len(neg)==1,'explicit reserve row structure')
   q,changes=self.helper.apply_loss_rows(q,names,point['node_loss_mw'],tick=t,positive_row=pos[0],negative_row=neg[0]);changed+=changes
  indexes={names['constraints'].index(x['name'])for x in changed};need(len(indexes)==24*41,'expected39 nodal +2 reserve rows per cell')
  need(q['variables']==template['variables']and q['objective']==template['objective'],'bounds/objective drift');need(all(q['rows'][i]==r for i,r in enumerate(template['rows'])if i not in indexes),'nonloss row drift');self.used.add((block,phase))
  receipt=dict(block=block,phase=phase,iteration=1,input_model_digest=digest(template),output_model_digest=digest(q),profile_digest=self.seal,changed_rows=changed,native_calls=0,native_execution_allowed=False,host_registration=False,far_requires_new_near_predicted_boundary=block=='far',gross_load_unchanged=True,full_AC_recheck_required=True,PRICE_allowed=False)
  return q,receipt
