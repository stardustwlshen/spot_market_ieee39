"""Selected accepted DA -> original last48 research domain. Zero optimizer calls."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import sys,json,hashlib,math,importlib.util
B=Path(__file__).resolve().parent
MANIFEST='c8b3e53419eb178545a87f3e4854d0631c5217df126cdde1360f411b8cbf18d7'
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
_CACHE={}
def bootstrap(engine_root=None):
 root=Path(engine_root).resolve()if engine_root else B.parent.parent/'engines/guangdong_short_unified_v6'
 need(sha(root/'FROZEN_MANIFEST.json')==MANIFEST,'fixed v6 manifest required')
 for n,h in read(root/'FROZEN_MANIFEST.json')['files'].items():need(sha(root/n)==h,'fixed v6 source changed')
 rt=root/'rt';vendor=rt/'guangdong_rt_recommit_program_v1_20261112/vendor';deps=[B,rt/'guangdong_rt_short_midnight_v1_20261112',vendor/'guangdong_after_reset_r23_compressed_20261112/repo/src',vendor/'guangdong_pipeline_20261112/da_math']
 sys.path[:0]=[str(p)for p in deps]
 for name in ('builder','thermal_physical','battery_short','control_source'):
  if name in sys.modules:need(Path(sys.modules[name].__file__).resolve()==B/(name+'.py'),'tail mode requires isolated subprocess: '+name)
 return root
def _source(da,engine_root=None):
 root=bootstrap(engine_root);da=Path(da).resolve()
 spec=importlib.util.spec_from_file_location('_tail_selected_da_validator',root/'rt/guangdong_rt_recommit_program_v32_20261112/interface_validators.py');validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
 pins=validator.selected_da(da)
 a=read(da/'ARTIFACTS.json')
 def ref(r):
  p=(da/r['path']).resolve();need(p.is_relative_to(da)and sha(p)==r['sha256'],'selected artifact identity');pins[str(p)]=sha(p);return p
 st=a['stages']['da_sced'];uc=a['stages']['da_scuc'];casepath=ref(a['inputs']['case']);namesp=ref(st['full_names']);yp=ref(st['expanded_response']);qp=ref(st['full_template']);agcp=ref(st['AGC_DECLARATION']);polp=ref(st['policy']);ucn=ref(uc['full_names']);ucx=ref(uc['full_primal']);ucq=ref(uc['full_template']);uca=read(ref(uc['acceptance']));need(uca['status']=='accepted','DA SCUC not accepted')
 for item in uca['artifacts'].values():ref(item)
 key=(str(da),sha(da/'ARTIFACTS.json'),sha(yp),sha(qp))
 if key in _CACHE:
  old=_CACHE[key]
  for p,h in old['pins'].items():need(sha(p)==h,'cached selected source changed')
  return old
 from smm.gd_october_job_codec import loads
 from smm.gd_r23_equivalent_compression import audit
 from scoped_kkt import metrics
 case=loads(casepath.read_bytes());names=read(namesp);y=read(yp);q=read(qp)
 need(len(case['energy_inputs']['horizon']['periods'])==576,'complete576-cell accepted extension required')
 need(len(names['variables'])==len(y['primal'])and len(set(names['variables']))==len(names['variables']),'full source names/witness')
 physical=audit(q,y['primal'],integers=True);kkt=metrics(q,y,names,angle_tolerance=1e-8);need(kkt['passed'],'selected full DA KKT failed')
 ucv=read(ucx);ucphysical=audit(read(ucq),ucv,integers=True);uv=dict(zip(read(ucn)['variables'],ucv));v=dict(zip(names['variables'],y['primal']))
 need(all(abs(v[k]-uv[k])<=1e-6 for k in v if k.startswith(('start[','stop[','online[','stable[','off['))),'DA SCED thermal integers not fixed to same SCUC')
 need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'original400 required')
 origin=case['contract']['day_start'];dt=datetime.fromisoformat(origin);need(dt.utcoffset()==timedelta(hours=8)and dt.hour==dt.minute==dt.second==0,'UTC+8 midnight source required')
 agc=read(agcp);need(set(agc)==set(case['energy_inputs']['thermal']),'original thermal AGC inventory')
 for g in agc:
  for side in ('up_mw','down_mw'):need(set(agc[g][side])=={str(t)for t in range(576)},'complete source AGC timeline')
 events=[]
 for g in case['energy_inputs']['thermal']:
  for t in range(528,576):
   if v[f'stop[{g},{t}]']>.5:events.append(dict(unit=g,begin_tick=t,curve=case['curves'][g]['shutdown']))
 targets={}
 for u,z in case['batteries'].items():
  seconds=(datetime.fromisoformat(z['terminal_at'])-dt).total_seconds();need(seconds%300==0,'original terminal not aligned to5min');t=int(seconds/300)-1
  if t<528:
   need(0<=t<576 and abs(v[f'battery.energy[{u},{t}]']-z['terminal_energy_mwh'])<=1e-7,'original historical SOC target not met');targets[u]=dict(tick=t,actual=v[f'battery.energy[{u},{t}]'],target=z['terminal_energy_mwh'],satisfied=True)
 s=dict(da=str(da),case=case,values=v,origin=origin,agc=agc,events=events,targets=targets,pins=pins,casepath=str(casepath),agcpath=str(agcp),policy=str(polp),namespath=str(namesp),responsepath=str(yp),acceptance=str(ref(st['acceptance'])),physical=physical,KKT=kkt,SCUC_physical=ucphysical,engine_root=str(root))
 _CACHE[key]=s;return s
def inspect_source(da,*,engine_root=None):
 s=_source(da,engine_root);start=datetime.fromisoformat(s['origin'])+timedelta(minutes=528*5)
 return dict(schema='gd.selected_DA_tail48.plan.v1',selected_DA=s['da'],source_kind='accepted_576cell_DA_research_extension_NOT_official_DA98_or_live_RT',domain_start=start.isoformat(),domain_end=(start+timedelta(hours=4)).isoformat(),cutoff=(start-timedelta(minutes=15)).isoformat(),boundary_tick=527,existing_shutdown_events=s['events'],original_past_targets=s['targets'],original_AGC_preserved=True,AGC_tail_totals={str(t):sum(a['up_mw'][str(t)]for a in s['agc'].values())for t in (528,552,570,575)},reserve_each_mw=400,source_pins=s['pins'],full_DA_physical=s['physical'],full_DA_KKT=s['KKT'],native_calls=0,formal=False,safety_release=False)
def prepare(da,output,*,declaration_name,research=False,engine_root=None):
 need(research is True and isinstance(declaration_name,str)and declaration_name.strip(),'explicit named research permission required; official import rejected')
 s=_source(da,engine_root);out=Path(output).resolve();need(not out.exists(),'new output only; no retry/overwrite');out.mkdir(parents=True);seed=out/'seed';seed.mkdir()
 c=s['case'];v=s['values'];origin=datetime.fromisoformat(s['origin']);begin=origin+timedelta(minutes=528*5);cut=begin-timedelta(minutes=15)
 for name,src in [('case.json',s['casepath']),('policy.json',s['policy']),('agc.json',s['agcpath'])]:(seed/name).write_bytes(Path(src).read_bytes())
 days={}
 for t in range(576):
  date=(origin+timedelta(minutes=5*t)).date().isoformat()
  if date not in days:days[date]={'artifact':{'thermal_online':{g:[]for g in c['energy_inputs']['thermal']}}}
  for g in c['energy_inputs']['thermal']:days[date]['artifact']['thermal_online'][g].append(v[f'online[{g},{t}]']>.5)
 permission=dict(schema='gd.selected_DA.simulated_stop_permission.v1',research=True,declaration_name=declaration_name,source=s['responsepath'],source_sha256=sha(s['responsepath']),as_of=cut.isoformat(),existing_shutdown_permissions=s['events'],official_published_event=False,safety_release=False,actual_dispatch_authorized=False,publication_scope='48h research extension, NOT official DA98',days=days)
 write(out/'SIMULATED_PERMISSION.json',permission)
 req=dict(schema='gd.selected_DA_tail48.seed.v1',origin=s['origin'],run_id='selected-DA-tail-'+sha(s['responsepath'])[:16],preorigin_notices={g:{}for g in c['energy_inputs']['thermal']},case_sha256=sha(seed/'case.json'),source_kind='same accepted DA no-deviation research projection',source_pins=s['pins'])
 write(seed/'request.json',req);write(seed/'WARMSTART_SOURCE.json',dict(names=s['namespath'],names_sha256=sha(s['namespath']),response=s['responsepath'],response_sha256=sha(s['responsepath']),basis='selected accepted DA only, not new RT optimum'))
 thermal={};ages={};notices={}
 from builder import end_power
 for g,z in c['energy_inputs']['thermal'].items():
  # Full original event history determines connection age, active residual curve and endpoint.
  transitions=[];active=None;completed_at_boundary=[]
  for t in range(528):
   for kind,stem in [('startup','start'),('shutdown','stop')]:
    if v[f'{stem}[{g},{t}]']>.5:
     rows=c['curves'][g][kind];transitions.append((t if kind=='startup'else t+len(rows),kind))
     if t+len(rows)>528:active=(kind,t,rows)
     if t+len(rows)==528:completed_at_boundary.append(kind)
  anchors=[(t,k)for t,k in transitions if t<=528];age=(528-max(t for t,k in anchors))/12 if anchors else z['initial_state_age_h']+44
  if active:phase,t,rows=active;remaining=deepcopy(rows[528-t:]);event_id=f'{g}:{phase}:{t}';elapsed=528-t
  else:
   phase=('stable'if completed_at_boundary[-1]=='startup'else'off')if completed_at_boundary else('stable'if v[f'stable[{g},527]']>.5 else'off');remaining=[];event_id=None;elapsed=0
  thermal[g]=dict(end_power_mw=end_power(c,v,g,527),phase=phase,remaining_curve=remaining,event_id=event_id,event_elapsed_ticks=elapsed,residual_up_ticks=max(0,round(z['min_up_h']*12-age*12))if phase!='off'else 0,residual_down_ticks=max(0,round(z['min_down_h']*12-age*12))if phase=='off'else 0,available_at=cut.isoformat(),source='same accepted DA trajectory through527, explicit no-deviation prediction, not telemetry',min_up_basis='startup_begin',min_down_basis='shutdown_end')
  ages[g]=age;lead=round(c['curves'][g]['notice_h']*12);notices[g]={str(t-lead):1 for t in range(576)if v[f'start[{g},{t}]']>.5 and 0<=t-lead<525}
 storage={}
 for u,z in c['batteries'].items():
  mode=lambda t:'charge'if v[f'battery.charge_mode[{u},{t}]']>.5 else'discharge'if v[f'battery.discharge_mode[{u},{t}]']>.5 else'idle'
  md=mode(527);ticks=0
  for t in range(527,-1,-1):
   if mode(t)!=md:break
   ticks+=1
  age=ticks/12+(z['initial_mode_age_h']if ticks==528 and z['initial_mode']==md else 0);eta=math.sqrt(z['roundtrip_efficiency']);budget={}
  for date,b in z['cycle_budget'].items():
   ts=[t for t in range(528)if(origin+timedelta(minutes=5*t)).date().isoformat()==date]
   budget[date]=dict(b,used_internal_mwh=b['used_internal_mwh']+math.fsum((eta*v[f'battery.charge[{u},{t}]']+v[f'battery.discharge[{u},{t}]']/eta)/12 for t in ts))
  storage[u]=dict(initial_energy_mwh=v[f'battery.energy[{u},527]'],initial_power_mw=v[f'battery.discharge[{u},527]']-v[f'battery.charge[{u},527]'],initial_mode=md,initial_mode_age_h=age,initial_hour_direction=0,cycle_budget=budget)
 carry=dict(schema='gd.short.carry.research.v2',origin=s['origin'],cutoff_tick=525,delivery_tick=528,cutoff=cut.isoformat(),boundary_at=begin.isoformat(),executed_end_exclusive=525,issued_end_exclusive=528,mode='source_DA_no_deviation_predicted_research_boundary_not_observed',predicted_issued_prefix=True,day_declaration_path=str(out/'SIMULATED_PERMISSION.json'),day_declaration_sha256=sha(out/'SIMULATED_PERMISSION.json'),seed_case_sha256=sha(seed/'case.json'),seed_request_sha256=sha(seed/'request.json'),thermal=thermal,thermal_age_h=ages,storage=storage,notice_values=notices,notice_requirements={g:dict(executed_startup_requirements=[],preserved_issued_notices=[],receipts_created=0)for g in thermal},boundary_numeric_normalization=[],energy_boundary_numeric_normalization=[],formal=False,safety_release=False,source_pins={**s['pins'],str(out/'SIMULATED_PERMISSION.json'):sha(out/'SIMULATED_PERMISSION.json'),**{str(p):sha(p)for p in B.glob('*.py')}})
 write(out/'INITIAL_CARRY.json',carry)
 from control_source import configure
 configure(s,permission)
 import builder
 summary=builder.build(seed,out/'INITIAL_CARRY.json',out/'prepared')
 result=dict(selected_DA=s['da'],seed=str(seed),carry=str(out/'INITIAL_CARRY.json'),prepared=str(out/'prepared'),source_pins=carry['source_pins'],summary=summary,source_review=inspect_source(da,engine_root=engine_root),declaration_name=declaration_name,native_calls=0,formal=False,safety_release=False)
 write(out/'SOURCE_REVIEW.json',result);return result
