"""Full same-source near/far static AC gate. No market optimization or execution."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import sys,json,gzip,hashlib,math,time,traceback
read=lambda p:json.loads(gzip.decompress(Path(p).read_bytes())if str(p).endswith('.gz')else Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 with Path(p).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
def bind_reactive_profile(profile_path,safety_source,*,reuse=None):
 profile_path=Path(profile_path).resolve();profile=read(profile_path)
 if profile.get('schema')!='gd.reactive_asset_profile.v1' or not isinstance(profile.get('assets'),list):raise ValueError('explicit optional reactive profile schema/assets required')
 if reuse is not None:raise ValueError('reactive profile cannot reuse an old AC gate; fresh same-source check required')
 assets=profile['assets'];pins={str(profile_path):sha(profile_path),str(Path(safety_source)/'reactive_assets.py'):sha(Path(safety_source)/'reactive_assets.py')}
 for entry in assets:
  if not isinstance(entry,dict) or set(entry)!={'spec','source'}:raise ValueError('reactive asset entry schema')
  references=[entry['source']]
  if entry['spec'].get('loss_accounting') is not None:references.append(entry['spec']['loss_accounting'])
  for reference in references:
   if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:raise ValueError('reactive source reference schema')
   target=(profile_path.parent/reference['path']).resolve()
   if not target.is_relative_to(profile_path.parent) or not target.is_file() or sha(target)!=reference['sha256']:raise ValueError('reactive source missing/changed/outside profile directory')
   pins[str(target)]=reference['sha256']
 return assets,pins,profile_path.parent

def require_safety_module_sources(source_root,modules):
 for module,name in modules:
  if Path(module.__file__).resolve()!=Path(source_root)/name:raise ValueError('cached safety module belongs to different source root: '+name)

def verify_source_pins(pins):
 for path,expected in pins.items():
  if not Path(path).is_file() or sha(path)!=expected:raise ValueError('source changed after AC: '+str(path))

def support_signature(case):
 from event_path import pack
 e=case['energy_inputs']
 return digest(pack({'grid':{k:e[k]for k in ('buses','lines','sections','reference_buses')},'thermal':{g:{'bus':z['bus'],**{k:sorted(set(z[k].values()))for k in ('min_mw','max_mw','ramp_up_mw_per_h','ramp_down_mw_per_h')}}for g,z in e['thermal'].items()},'battery':{g:{k:z[k]for k in ('bus','energy_capacity_mwh','roundtrip_efficiency','energy_min_mwh','energy_max_mwh','charge_max_mw','discharge_max_mw')}for g,z in case['batteries'].items()}}))
def finalize(out,summary):
 feedback=[]
 evidence=Path(summary.get('reused_directory',out))
 for row in summary['results']:
  folder=evidence/f"T{row['global_tick']:03}"
  cap=read(folder/'CAPACITY.json')if(folder/'CAPACITY.json').exists()else {}
  feedback.append(dict(global_tick=row['global_tick'],stage=row['stage'],loss_mw=row.get('loss_MW'),violations=row.get('violations'),thermal_AGC=cap.get('thermal'),error=row.get('error'),safe=row.get('safe',False)))
 coverage=len(summary['results']);physical_pass=coverage==48 and summary['safe']==48 and summary['unsupported']==0
 z=dict(status='static_AC_passed'if physical_pass else'blocked_unsafe',coverage=coverage,safe=summary['safe'],converged=summary['converged'],new_AC=summary['new_AC'],price_allowed=physical_pass,execution=False,publication=False,formal_security=False,feedback=feedback,unknown_clocks=True,reused=bool(summary.get('reused_directory')),summary_path=str(out/'SUMMARY.json'))
 if any(row.get('reactive_normal_use_blocked',False) for row in summary['results']):
  z.update(price_allowed=False,reactive_diagnostic_only=True,reactive_reason='explicit research reactive capability cannot authorize normal price or execution')
  if z['status']=='static_AC_passed':z['status']='blocked_research_reactive'
 save(out/'GATE.json',z);return z
def reuse_checked(out,pairs,pins,reuse):
 old=Path(reuse['directory']).resolve();manifest=Path(reuse['manifest']).resolve()
 if sha(manifest)!=reuse['manifest_sha256']:raise ValueError('reuse manifest identity')
 inventory={x['path']:x for x in read(manifest)['files']}
 for p in old.rglob('*'):
  if not p.is_file():continue
  matches=[v for k,v in inventory.items()if str(p).endswith('/'+k)]
  if len(matches)!=1 or sha(p)!=matches[0]['sha256']:raise ValueError('reuse payload drift '+str(p))
 oldpins=read(old/'SOURCE_BINDING.json')['pins']
 for stage,p,a,acc in pairs:
  marker='/REAL_REGISTERED_STAGE_NATIVE/'if stage=='near'else'/REAL_STAGE_SINGLE_WINDOW/window/'
  for file in [p/'derived_case.json',p/'names.json',p/'BOUNDARY.json',p/'agc.json',a/'SCED/response.json.gz',a/'SCUC/response.json.gz',acc]:
   suffix='/'+file.name
   if '/SCED/'in str(file):suffix='/SCED/'+file.name
   if '/SCUC/'in str(file):suffix='/SCUC/'+file.name
   values=[h for path,h in oldpins.items()if marker in path and path.endswith(suffix)]
   if len(values)!=1 or sha(file)!=values[0]:raise ValueError('different source cannot reuse AC: '+str(file))
 # AC physical mapper/network source bytes must also match, not only market primal.
 for p in (SA/'ac_adapter.py',SA/'ac_security.py',SA/'bridge.py',SA/'data/IEEE39_RESEARCH_INPUT.json',SA/'data/SUPPORTED_SIGNATURE.json'):
  values=[h for path,h in oldpins.items()if path.endswith('/'+p.name)]
  if len(values)!=1 or sha(p)!=values[0]:raise ValueError('AC implementation/physical source drift')
 summary=read(old/'SUMMARY.json');summary.update(new_AC=0,reused_AC=48,reused_directory=str(old));save(out/'SOURCE_BINDING.json',dict(pins=pins,reuse_manifest_sha256=sha(manifest),reuse_directory=str(old)));save(out/'SUMMARY.json',summary);return finalize(out,summary)
def validate_curve(*,program_root,near_prepared,near_actual,far_window,output,reuse=None,reactive_profile=None):
 global W,SA,loads,map_candidate,digest,check_ac,postcheck
 W=Path(program_root).resolve();SA=W/'app/lifecycle_runtime/guangdong_safety_session_v1_20261113'
 sys.path[:0]=[str(SA),str(SA.parent/'guangdong_fixed_event_entry_v1_20261113'),str(W/'engines/guangdong/guangdong_after_reset_r23_compressed_20261112/repo/src')]
 from smm.gd_october_job_codec import loads
 from ac_adapter import map_candidate,digest
 from ac_security import check_ac
 from bridge import postcheck
 import ac_adapter as adapter_module,ac_security as security_module,bridge as bridge_module
 require_safety_module_sources(SA,((adapter_module,'ac_adapter.py'),(security_module,'ac_security.py'),(bridge_module,'bridge.py')))
 near=Path(near_prepared).resolve();actual=Path(near_actual).resolve();far=Path(far_window).resolve();out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
 pairs=[('near',near,actual,actual/'ACCEPTED_PAIR.json'),('far',far/'far/prepared',far/'far/actual',far/'FAR_ACCEPTED.json')]
 pins={str(p):sha(p)for p in [Path(__file__),SA/'ac_adapter.py',SA/'ac_security.py',SA/'bridge.py',SA/'data/IEEE39_RESEARCH_INPUT.json',SA/'data/SUPPORTED_SIGNATURE.json',far/'AGC_CANDIDATES.json',far/'PREDICTED.json']}
 reactive_assets=None
 if reactive_profile is not None:
  reactive_assets,reactive_pins,reactive_source_root=bind_reactive_profile(reactive_profile,SA,reuse=reuse);pins.update(reactive_pins)
 groups=[]
 for stage,p,a,acc in pairs:
  z=read(acc)
  if stage=='near':assert z['native_dual_passed'] is True and z['SCED_response_sha256']==sha(a/'SCED/response.json.gz')
  else:
   assert z['SCED']['KKT']['passed'] is True and z['execution_allowed'] is False
   for path,h in z['pins'].items():assert sha(path)==h
  for f in [p/'derived_case.json',p/'names.json',p/'BOUNDARY.json',p/'agc.json',a/'SCED/response.json.gz',a/'SCUC/response.json.gz',acc]:pins[str(f)]=sha(f)
  case=loads((p/'derived_case.json').read_bytes())
  # Old static signature encodes 48 copies of constant line limits. Only
  # signature comparison is normalized; actual 24-cell physical case is unchanged.
  static=deepcopy(case)
  for line in static['energy_inputs']['lines'].values():
   for key in ('lower_mw','upper_mw'):
    values=line[key];assert set(values)==set(range(24)) and len(set(values.values()))==1
    line[key]={i:values[0]for i in range(48)}
  assert support_signature(static)==read(SA/'data/SUPPORTED_SIGNATURE.json')['sha256']
  names=read(p/'names.json')['variables'];raw=read(a/'SCED/response.json.gz');assert len(names)==len(raw['primal']);v={k.replace("'",''):x for k,x in zip(names,raw['primal'])}
  assert len(case['energy_inputs']['horizon']['periods'])==24 and all(abs(x['duration_h']-1/12)<1e-12 for x in case['energy_inputs']['horizon']['periods'])
  for g in case['energy_inputs']['thermal']:
   assert all(v[f'stable[{g},{t}]']>.5 and v[f'online[{g},{t}]']>.5 and abs(v[f'start[{g},{t}]'])<1e-8 and abs(v[f'stop[{g},{t}]'])<1e-8 for t in range(24))
  loss_points=None
  if (p/'LOSS_PROFILE.json').exists():
   profile=read(p/'LOSS_PROFILE.json');receipt=read(p/'LOSS_OVERLAY.json');need_profile=digest(profile)==receipt['profile_digest'] and digest(read(p/'template.json'))==receipt['output_model_digest']
   if not need_profile:raise ValueError('candidate loss profile/template mismatch')
   loss_points=[x for x in profile['points']if x['block']==stage]
   if len(loss_points)!=24:raise ValueError('candidate loss coverage')
   for f in (p/'LOSS_PROFILE.json',p/'LOSS_OVERLAY.json'):pins[str(f)]=sha(f)
  groups.append((stage,case,v,read(p/'agc.json'),read(p/'BOUNDARY.json'),loss_points))
 n,f=groups
 for g in n[1]['energy_inputs']['thermal']:assert abs(n[2][f'power[{g},23]']-f[4]['thermal'][g]['end_power_mw'])<1e-8
 for g in n[1]['batteries']:assert abs(n[2][f'battery.energy[{g},23]']-f[1]['batteries'][g]['initial_energy_mwh'])<1e-8

 if reuse is not None:return reuse_checked(out,pairs,pins,reuse)
 binding={'schema':'gd.single_window.static_ac.sources.v1','pins':pins,'global_ticks':[groups[0][1]['contract']['stage']['start_tick'],groups[1][1]['contract']['stage']['start_tick']+23],'actual_executed_end_exclusive':groups[0][1]['contract']['stage']['executed_end_exclusive'],'issued_end_exclusive':groups[0][1]['contract']['stage']['issued_end_exclusive'],'forecast_only':True,'executed':False,'near_far_boundary_checked':True,'arrival_clocks':'unknown; adapter logical cutoff is research replay only','accepted_KKT_original_not_recomputed':True,'original_GT_VG_unchanged':1.0636,'original_VG_limit':1.06}
 save(out/'SOURCE_BINDING.json',binding);mh=sha(out/'SOURCE_BINDING.json');results=[];calls=0;ts=time.perf_counter();benchmark=read(SA/'data/IEEE39_RESEARCH_INPUT.json');bg={g['bus']:g for g in benchmark['generators']}
 for stage,case,v,ag,boundary,loss_points in groups:
  E=case['energy_inputs'];origin=datetime.fromisoformat(case['contract']['domain_start'])
  for t in range(24):
   global_tick=case['contract']['stage']['start_tick']+t;row={'stage':stage,'local_tick':t,'global_tick':global_tick,'executed':False};folder=out/f'T{global_tick:03}';folder.mkdir()
   try:
    ac=deepcopy(benchmark);start=origin+timedelta(minutes=t*5);end=start+timedelta(minutes=5);resources=[];plan={};controls={};gens=[];capacity={};load={};fixed=[]
    def prow(p):return {'p_mean_mw':p,'p_endpoint_mw':p,'interval_start_seconds':start.timestamp(),'interval_end_seconds':end.timestamp()}
    for b in ac['buses']:
     node=str(int(b['id'])+1);p=E['load_mw'][node,t];assert b['pd_mw']!=0 or p==0;load[b['id']]={'p_mw':p,'q_mvar':p*b['qd_mvar']/b['pd_mw']if b['pd_mw']else 0.}
     if E['physical_tie_mw'][node,t]:fixed.append(dict(id='fixed:'+node,bus=b['id'],p_mw=E['physical_tie_mw'][node,t],q_mvar=0.))
    for u,z in E['thermal'].items():
     bus=str(int(z['bus'])-1);g=deepcopy(bg[bus]);mw=v[f'power[{u},{t}]'];g.update(id=u,pg_mw=mw,qg_mvar=0.,pmin_mw=z['min_mw'][t],pmax_mw=z['max_mw'][t]);gens.append(g);up=ag[u]['up_mw'][str(t)];dn=ag[u]['down_mw'][str(t)];prev=boundary['thermal'][u]['end_power_mw']if t==0 else v[f'power[{u},{t-1}]']
     r=dict(id=u,bus=bus,kind='thermal',online=True,phase='stable',agc_up_mw=up,agc_down_mw=dn,ramp_up_mw_per_h=z['ramp_up_mw_per_h'][t],ramp_down_mw_per_h=z['ramp_down_mw_per_h'][t],previous_endpoint_mw=prev,endpoint_semantics='explicit_stable_piecewise_constant',reserve_eligible=case['reserve']['contingency_eligible'][u],positive_cap_mw=g['pmax_mw']-g['pmin_mw']-up-dn,accident_cap_mw=z['ramp_up_mw_per_h'][t]/6)
     resources.append(r);plan[u]=prow(mw);controls[u]=dict(mode='VG',vg_pu=g['vg_pu']);capacity[u]=dict(raw_pmax_mw=g['pmax_mw'],raw_pmin_mw=g['pmin_mw'],scheduled_p_mw=mw,agc_up_reserved_mw=up,agc_down_reserved_mw=dn,positive_cap_mw=r['positive_cap_mw'],accident_cap_mw=r['accident_cap_mw'])
    vre={'W1':('30','wind'),'W2':('37','wind'),'PV1':('33','pv'),'PV2':('4','pv'),'PV3':('8','pv')};assert all(abs(v[f'renewable_power[{node},{t}]'])<1e-8 for node in E['buses']if node not in {a for a,b in vre.values()})
    for u,(node,kind)in vre.items():resources.append(dict(id=u,bus=str(int(node)-1),kind=kind,online=True,reserve_eligible=False));plan[u]=prow(v[f'renewable_power[{node},{t}]']);controls[u]=dict(mode='PQ',q_mvar=0.)
    for u,z in case['batteries'].items():
     ch=v[f'battery.charge[{u},{t}]'];dis=v[f'battery.discharge[{u},{t}]'];eta=math.sqrt(z['roundtrip_efficiency']);prev=z['initial_energy_mwh']if t==0 else v[f'battery.energy[{u},{t-1}]'];r=dict(id=u,bus=str(int(z['bus'])-1),kind='battery',online=z['availability'][t],reserve_eligible=False,soc=dict(previous_end_at=start.isoformat(),start_mwh=prev,previous_end_mwh=prev,end_mwh=v[f'battery.energy[{u},{t}]'],min_mwh=z['energy_min_mwh'],max_mwh=z['energy_max_mwh'],eta_charge=eta,eta_discharge=eta,charge_max_mw=z['charge_max_mw'],discharge_max_mw=z['discharge_max_mw']))
     resources.append(r);plan[u]=dict(prow(dis-ch),charge_mw=ch,discharge_mw=dis);controls[u]=dict(mode='PQ',q_mvar=0.)
    ac['generators']=gens;ac['provenance']='Original IEEE39 static benchmark; original GT VG1.0636 retained; research Q/P loads and inverter Q0; no telemetry';ac['operating_point_kind']='research_snapshot'
    d=dict(schema='gd.explicit_sced_ac_candidate.v1',start=start.isoformat(),end=end.isoformat(),information_cutoff=case['contract']['cutoff'],data_available_at=case['contract']['cutoff'],power_semantics='interval_mean_research_snapshot',physical_load_excludes_loss_proxy=True,network=ac,resources=resources,plan=plan,controls=controls,gross_load=load,fixed_injections=fixed,slack_resource='C1',reserve_requirements=dict(positive_mw=400.,accident_mw=400.),loss_proxy_mw=0.,allow_original_VG_diagnostic=True,capability=dict(mode='original_Q0',qualification_changed=False,source_statement='Original inverter Q0, no new design; logical replay clocks only'))
    if loss_points is not None:
     point=loss_points[t]
     if point['global_tick']!=global_tick or point['start']!=start.isoformat() or point['gross_load']!=load:raise ValueError('candidate loss time/gross load mismatch')
     d['loss_proxy_mw']=point['total_loss_mw']
    if reactive_assets is not None:d['reactive_assets']=deepcopy(reactive_assets)
    save(folder/'PAYLOAD.json',d);m=map_candidate(d,dict(payload_sha256=digest(d),SCED_accepted=True,full_domain_constraints_checked=True,sources=[dict(path=str(out/'SOURCE_BINDING.json'),sha256=mh)]),W,**({'reactive_source_root':reactive_source_root}if reactive_assets is not None else{}));m['thermal_capacity']=capacity;save(folder/'MAPPING.json',m)
    if 'reactive_assets' in m:
     row['reactive_normal_use_blocked']=m['reactive_normal_use_blocked'];row['reactive_gates']=m['reactive_assets']['gates']
    assert calls<48;calls+=1;save(folder/'AC_STARTED.json',dict(input_sha256=digest(m['ac_input']),call_number=calls));clock=time.perf_counter();a=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10);row['NR_seconds']=time.perf_counter()-clock;save(folder/'AC_RESULT.json',a);cap=postcheck(m,a);save(folder/'CAPACITY.json',cap)
    row.update(status=a['status'],ac_converged=a['ac_converged'],safe=cap['research_safe'],violations=a.get('violations'),AGC_preserved=cap.get('synthetic_AGC50_headroom_preserved'),positive_available_MW=cap.get('positive_available_mw'),accident_available_MW=cap.get('accident_available_mw'))
    if a['ac_converged']:row.update(loss_MW=math.fsum(x['from_p_mw']+x['to_p_mw']for x in a['branches']),slack=[g for g in a['generators']if g['slack']])
   except Exception as ex:row.update(status='unsupported_or_check_error',safe=False,error=str(ex),traceback=traceback.format_exc())
   save(folder/'SUMMARY.json',row);results.append(row)
 verify_source_pins(pins)
 summary=dict(kind='near24_far24_prediction_static_AC',coverage=48,results=results,new_HiGHS=0,new_PRICE=0,new_AC=calls,elapsed_seconds=time.perf_counter()-ts,converged=sum(x.get('ac_converged',False)for x in results),safe=sum(x.get('safe',False)for x in results),unsupported=sum(x['status']=='unsupported_or_check_error'for x in results),execution_advancement=0,release=False,N_1=False,dynamic=False,actual_clocks_unknown=True,source_binding_sha256=mh)
 save(out/'SUMMARY.json',summary);return finalize(out,summary)
