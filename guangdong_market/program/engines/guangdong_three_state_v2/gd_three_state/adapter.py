"""Source-pinned installed adapter. Pure by default. Distinct three-state research runs."""
from pathlib import Path
import sys,json,hashlib,importlib.util,math,os
from copy import deepcopy
from datetime import datetime
B=Path(__file__).resolve().parent;D=B/'deps';P=D/'guangdong_rt_three_state_program_v1_20261112';I=D/'guangdong_rt_three_state_integration_v1_20261112'
sys.path[:0]=[str(P),str(I)]
from evidence_io import save,load,write_json,read_bytes
h=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def modules():
 for rel,sha in read(B/'DEPENDENCIES.json').items():need(h(D/rel)==sha,'installed dependency changed: '+rel)
 spec=importlib.util.spec_from_file_location('_trusted_three_state_runner',P/'program.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def pins():return {str(p.resolve()):h(p)for p in [B/'adapter.py',B/'DEPENDENCIES.json',*[D/r for r in read(B/'DEPENDENCIES.json')]]}
def parent_refs(parent):
 p=Path(parent).resolve();a=read(p/'ARTIFACTS.json');need(a.get('status')=='accepted','accepted parent required');refs={}
 for label,z in {**a['inputs'],**a['stages']['da_sced']}.items():
  if isinstance(z,dict)and'path'in z:
   f=(p/z['path']).resolve();need(f.is_relative_to(p)and h(f)==z['sha256'],'parent artifact mismatch');refs[str(f)]=z['sha256']
 need(read(p/a['stages']['da_sced']['acceptance']['path'])['status']=='accepted','DA-SCED acceptance required')
 for f,sha in read(p/'RUN.json')['source_pins'].items():need(h(f)==sha,'parent source changed')
 return p,a,refs

def seed(parent,out,parameters=None,degenerate=False):
 modules();out=Path(out).resolve();need(not out.exists(),'fresh run only');need(bool(parameters)!=bool(degenerate),'choose explicit parameter JSON OR explicit degenerate research')
 p,a,refs=parent_refs(parent)
 from smm.gd_october_job_codec import loads
 from contract import from_uniform,dto
 case=loads((p/a['inputs']['case']['path']).read_bytes());params=read(parameters)if parameters else from_uniform(case)
 need(set(params['units'])==set(case['energy_inputs']['thermal']),'exact thermal class inventory')
 for g in params['units']:dto(g,params,case['energy_inputs'],case['curves'])
 need(all(set(case['reserve'][k].values())=={400}for k in('positive_mw','contingency_mw')),'this provincial branch preserves original400MW')
 out.mkdir();inputs={}
 for key,ref in [('case',a['inputs']['case']),('agc',a['inputs']['agc']),('policy',a['inputs']['policy']),('DA_names',a['stages']['da_sced']['full_names']),('DA_response',a['stages']['da_sced']['expanded_response'])]:inputs[key]=save(out/(key+'.json.gz'),read(p/ref['path']))
 inputs['parameters']=save(out/'parameters.json.gz',params)
 c=dict(schema='gd.rt.three_state.adapter.context.v2',run_id='three-adapter-'+h(p/'ARTIFACTS.json')[:12]+'-'+inputs['parameters']['raw_sha256'][:8],parent=str(p),parent_artifacts_sha256=h(p/'ARTIFACTS.json'),parent_pins=refs,source_pins=pins(),inputs=inputs,parameter_selection='explicit_degenerate_research'if degenerate else'explicit_supplied_research_contract',safety_release=False,formal=False,seed_executed_cells=0,seed_assumed_issued_cells=3)
 write_json(out/'CONTEXT.json',c);return dict(status='seeded',native_calls=0,run_id=c['run_id'])
def context(out):
 m=modules();o=Path(out).resolve();c=read(o/'CONTEXT.json');need(c['schema']=='gd.rt.three_state.adapter.context.v2','not an adapter three-state run');m.verify(c)
 for ref in c['inputs'].values():read_bytes(ref)
 return o,c,m

def build(out):
 o,c,engine=context(out)
 if(o/'PREPARED.json').exists():return preflight(out)
 need(not(o/'BUILD_STARTED.json').exists(),'partial build locked; inspect, no automatic retry');write_json(o/'BUILD_STARTED.json',dict(context_sha256=h(o/'CONTEXT.json')))
 from smm.gd_october_job_codec import loads
 from contract import dto
 from build import build as physical
 from rt_bridge import configure_rt,expected_prefix,binding
 from component5 import classify
 import pyomo.environ as p
 case=loads(json.dumps(load(c['inputs']['case'])).encode());agc=load(c['inputs']['agc']);params=load(c['inputs']['parameters']);policy=load(c['inputs']['policy'])
 for z in agc.values():
  for key in('up_mw','down_mw'):z[key]={int(t):v for t,v in z[key].items()}
 x=dict(zip(load(c['inputs']['DA_names'])['variables'],load(c['inputs']['DA_response'])['primal']));m=physical(case,agc,mode='three_state',parameters=params)
 for v in m.component_data_objects(p.Var):
  if v.name in x:v.set_value(x[v.name])
 for g in m.G:
  b=m.three[g].event;d=b._dto;starts={};stops={t for t in m.T if p.value(m.stop[g,t])>.5};age=d.initial_offline_ticks;off=int(d.initial_phase=='off');stable=1-off
  for t in b.B:
   if t:age=0 if t-len(d.shutdown.mean_mw)in stops else age+1 if off else 0
   if t<len(m.T)and p.value(m.start[g,t])>.5:starts[t]=classify(d,age)
   complete=sum(s+len(d.curves[k].mean_mw)==t for s,k in starts.items());off+=int(t-len(d.shutdown.mean_mw)in stops)-int(t in starts);stable+=complete-int(t in stops)
   b.off[t].set_value(off);b.stable[t].set_value(stable);b.offline_ticks[t].set_value(age)
   if t<len(m.T):
    b.start[t].set_value(int(t in starts));b.stop[t].set_value(int(t in stops))
    for k in b.K:b.kind[t,k].set_value(int(starts.get(t)==k))
  for t in b.T:
   for k in b.K:b.notice[t,k].set_value(int(starts.get(t+d.curves[k].notice_lead_ticks)==k))
 state=dict(schema='gd.rt.three_state.issued_prefix.v1',origin=case['contract']['day_start'],cutoff_tick=0,delivery_tick=3,DA_online={g:[bool(round(p.value(m.online[g,t])))for t in m.T]for g in m.G},**binding(case,params));state['fixed_values']={n:p.value(v)for n,v in expected_prefix(m,3,0,datetime.fromisoformat(state['origin'])).items()};configure_rt(m,case,params,policy,state)
 q,vs,cs=engine.serialize_model(m,engine.validate_options(dict(time_limit=300.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;values=[p.value(v)for v in vs];obj=q['objective']['constant']+math.fsum(a*values[j]for j,a in q['objective']['terms']);review=engine.audit(q,values,obj);need(review['max_primal_violation']<=1e-6 and review['max_integer_violation']<=1e-6,'selected DA trajectory not a feasible witness for supplied classes; no fabricated warmstart')
 for z,v in zip(q['variables'],values):z['start']=v
 q['warmstart']=True;refs=dict(template=save(o/'template.json.gz',q),names=save(o/'names.json.gz',dict(variables=[v.name for v in vs],constraints=[v.name for v in cs])),state=save(o/'state.json.gz',state),parameters=c['inputs']['parameters']);write_json(o/'PREPARED.json',dict(context_sha256=h(o/'CONTEXT.json'),refs=refs,witness=review));return dict(status='prepared',native_calls=0,witness=review)

def preflight(out):
 o,c,m=context(out);t=read(o/'PREPARED.json');need(t['context_sha256']==h(o/'CONTEXT.json'),'prepared context changed')
 for r in t['refs'].values():read_bytes(r)
 states={}
 for stage in('SCUC','SCED'):
  p=o/stage;states[stage]='not_started'if not p.exists()else'completed_needs_review'if(p/'COMPLETED.json').exists()else'unknown_locked'
 return dict(status='preflight',native_calls=0,stages=states,unknown_locked='unknown_locked'in states.values(),safety_release=False)

def accept(out,legacy=False):
 if legacy:
  o=Path(out).resolve();m=modules();c=read(o/'CONTEXT.json');m.verify(c);need(c['schema']=='gd.rt.three_state.program.v1','only registered legacy three-state branch');need(set(c['source_pins'].values()).issubset(set(read(B/'DEPENDENCIES.json').values())),'unregistered legacy producer source')
 else:o,c,m=context(out)
 t=read(o/'PREPARED.json');need(t['context_sha256']==h(o/'CONTEXT.json'),'ticket changed');q=load(t['refs']['template']);n=load(t['refs']['names']);uq,ur,ue=m.native_check(o,'SCUC',q);ua=m.checks(uq,ur,n,True);sq=m.fixed_sced(q,ur['primal']);sq['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);eq,er,ee=m.native_check(o,'SCED',sq);ea=m.checks(eq,er,n,False)
 need(all(abs(v-round(w))<=1e-8 for v,w,z in zip(er['primal'],ur['primal'],q['variables'])if z['kind']!='C'),'new integer drift')
 return dict(schema='gd.rt.three_state.adapter.acceptance_review.v2',run_id=c['run_id'],legacy_readonly=legacy,source_context_sha256=h(o/'CONTEXT.json'),SCUC_audit=ua,SCED_audit=ea,native_calls=0,safety_release=False,SCUC=ue,SCED=ee,refs=t['refs'])

def run(out,enabled=False):
 pf=preflight(out)
 if not enabled:return {**pf,'status':'dryrun','planned_SCUC_seconds':300,'planned_SCED_seconds':120}
 need(not pf['unknown_locked'],'unknown occurrence locked; no retry');o,c,m=context(out);t=read(o/'PREPARED.json');q=load(t['refs']['template']);n=load(t['refs']['names']);calls=0
 if pf['stages']['SCUC']=='not_started':m.native(o,'SCUC',q,P/'scuc_worker.py');calls+=1
 uq,ur,_=m.native_check(o,'SCUC',q);m.checks(uq,ur,n,True);sq=m.fixed_sced(q,ur['primal']);sq['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
 if pf['stages']['SCED']=='not_started':m.native(o,'SCED',sq,P/'lp_worker.py');calls+=1
 z=accept(o);target=o/'ADAPTER_ACCEPTANCE.json'
 if not target.exists():write_json(target,z)
 return dict(status='accepted',new_native_calls=calls,safety_release=False)

def handoff(out,confirmed=False):
 need(confirmed,'explicit counterfactual no-deviation research required');o,c,m=context(out);z=accept(o);old=load(z['refs']['state']);need(old['cutoff_tick']==0 and old['delivery_tick']==3,'this adapter supports first-window handoff only')
 values=dict(zip(load(z['refs']['names'])['variables'],load(z['SCED']['response'])['primal']));need(all(abs(values[k]-v)<=1e-7 for k,v in old['fixed_values'].items()),'issued prefix drift')
 state=deepcopy(old);state.update(cutoff_tick=3,delivery_tick=6);fixed={}
 from datetime import timedelta
 for name,value in values.items():
  if name.startswith('three['):
   tick=int(name.rsplit('[',1)[1].split(',',1)[0].rstrip(']'));limit=3 if '.notice[' in name else 6
   if tick<limit:fixed[name]=value
  elif name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if datetime.fromisoformat(hour)<datetime.fromisoformat(old['origin'])+timedelta(minutes=30):fixed[name]=value
  elif '['in name:
   token=name.rsplit(',',1)[-1].split('[')[-1].rstrip(']')
   if token.isdigit()and int(token)<6:fixed[name]=value
 state['fixed_values']=fixed;dest=o/'handoff_W1'
 if dest.exists():
  r=read(dest/'HANDOFF.json');need(r['context_sha256']==h(o/'CONTEXT.json') and load(r['state'])==state,'existing handoff mismatch');return dict(status='existing_typed_handoff_verified',native_calls=0)
 dest.mkdir();ref=save(dest/'state.json.gz',state);receipt=dict(schema='gd.rt.three_state.adapter.handoff.v2',run_id=c['run_id'],context_sha256=h(o/'CONTEXT.json'),state=ref,executed_cells=3,issued_cells=6,executed_source='initial assumed DA seed, not new RT execution',initial_origin_retained=True,phase_kind_notice_offline_clock_retained=True,prefix_drift=0,safety_release=False,formal=False,native_calls=0);write_json(dest/'HANDOFF.json',receipt);return receipt

def migration(old,out):
 out=Path(out).resolve();need(not out.exists(),'fresh readonly migration view');z=accept(old,legacy=True);out.mkdir();write_json(out/'MIGRATION_REVIEW.json',z);write_json(out/'VIEW.json',dict(schema='gd.rt.three_state.readonly_view.v2',old_run=str(Path(old).resolve()),old_context_sha256=z['source_context_sha256'],new_native_calls=0,not_adapter_native=True,resume_supported=False));return dict(status='explicit_readonly_view_verified',native_calls=0)

def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('action',choices=['seed','build','preflight','run','accept','handoff','migrate']);p.add_argument('--run',required=True);p.add_argument('--parent');p.add_argument('--parameters');p.add_argument('--degenerate-research',action='store_true');p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');p.add_argument('--legacy-run');a=p.parse_args()
 if a.action=='seed':z=seed(a.parent,a.run,a.parameters,a.degenerate_research)
 elif a.action=='build':z=build(a.run)
 elif a.action=='preflight':z=preflight(a.run)
 elif a.action=='run':z=run(a.run,a.execute_native)
 elif a.action=='accept':z=accept(a.run)
 elif a.action=='migrate':z=migration(a.legacy_run,a.run)
 else:z=handoff(a.run,a.simulate_no_deviation)
 print(json.dumps(z))
if __name__=='__main__':main()
