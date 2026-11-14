"""Explicit same-host D+1 research reference. Default zero calls; no JSON resume."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import sys,json,hashlib,subprocess,shutil,importlib.util,gzip,uuid
from reference_math import prepare_boundary,thermal_overlay,PROFILE
B=Path(__file__).resolve().parent;APP=B.parent;R=APP.parent/'engines/guangdong_short_unified_v6/rt'
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,msg):
 if not x:raise ValueError(msg)
def put(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True)
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
_KEY=object()
class ReferenceOverlay:
 def __init__(self,key,session):
  need(key is _KEY,'private reference capability');self._session=session
 def verify(self,host):
  s=self._session;need(host is s.host,'overlay belongs to another host');s.verify();need(s.accepted is not None,'reference not accepted')
  return deepcopy(s.accepted)
class ReferenceSession:
 def __init__(self,host,output,*,research_reference_revision=False):
  from unified import RollingHost
  need(isinstance(host,RollingHost),'registered RollingHost required, not JSON')
  need(research_reference_revision is True,'independent explicit reference-revision research selection required')
  need(getattr(host,'windows',None)==96,'reference revision requires explicit96-window host')
  self.host=host;self.out=Path(output).resolve();need(not self.out.exists(),'restart/adoption forbidden; inspect existing occurrence')
  self.out.mkdir(parents=True);self.branch_id='D1-reference-'+uuid.uuid4().hex;self.pins={str(f):sha(f)for f in B.glob('*.py')};
  for root in (R/'guangdong_rt_short_user_cli_v5_20261112',R/'guangdong_rt_short_midnight_v1_20261112',R/'guangdong_rt_recommit_program_v32_20261112',R/'guangdong_rt_recommit_program_v1_20261112/runtime',R/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm',APP/'lifecycle_runtime/guangdong_network_dual_certificate_v1_20261113'):
   self.pins.update({str(f):sha(f)for f in root.glob('*.py')})
  for f in (R/'guangdong_rt_short_physical_v1_20261112/run_actual39.py',B/'precision_worker.py'):self.pins[str(f)]=sha(f)
  host.session.registry['source_pins'].update(self.pins)
  self.prepared=False;self.attempted=False;self.accepted=None;self._token=object()
 def verify(self):
  h=self.host;h.session.verify_sources();need(h.hooks.carries.get(str(h.cp))==sha(h.cp),'unregistered/tampered live clock')
  for f,v in self.pins.items():need(sha(f)==v,'reference source/model drift: '+f)
 def prepare(self):
  need(not self.prepared,'single reference preparation');self.verify();h=self.host;c=read(h.cp)
  need(c['cutoff_tick']==282 and h.window==94,'prepare only after W93 acceptance at23:30, before W94')
  need(h.pending is None,'cannot revise while RT model pending')
  case,values,da=h.agc.da_plan();receipt=h.agc.produce(24,cutoff=c['cutoff']);h.accepted_source(93)
  need(receipt==h.agc.issued[24],'unregistered H24');need(receipt['context']['branch_id']==h.agc.context['run_id'],'H24 branch mismatch')
  origin=h.agc.origin.isoformat();boundary=prepare_boundary(case,values,receipt,origin=origin,case_sha256=h.agc.seed_sha)
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 reserve unchanged')
  self.pins.update(da['source_pins']);self.pins.update(receipt['verified_plan']['source_pins']);self.pins[str(h.agc.root/'H24.json')]=sha(h.agc.root/'H24.json')
  da_root=Path(h.agc.context['selected_DA']);art=read(da_root/'ARTIFACTS.json');st=art['stages']['da_scuc']
  for z in st.values():
   if isinstance(z,dict)and'path'in z:need(sha(da_root/z['path'])==z['sha256'],'DA SCUC source drift');self.pins[str(da_root/z['path'])]=z['sha256']
  scuc=dict(zip(read(da_root/st['full_names']['path'])['variables'],read(da_root/st['full_primal']['path'])))
  self.da_root=da_root;self.values=values;self.boundary=boundary;self.units=list(case['energy_inputs']['thermal'])
  materialize(self.out,h.session.run,values,scuc,receipt,boundary,self.pins)
  p=subprocess.run([sys.executable,str(B/'build_process.py'),str(self.out)],capture_output=True,text=True)
  (self.out/'BUILD.log').write_text(p.stdout+p.stderr);need(p.returncode==0,'reference pure build failed; preserve BUILD.log')
  self.pins.update({str(f):sha(f)for f in self.out.rglob('*')if f.is_file()});self.prepared=True;self.verify()
  return dict(profile=PROFILE,branch_id=self.branch_id,prepared=str(self.out/'prepared'),reference_native_budget=1,seconds=3600,threads=1,default_native_calls=0,formal=False,safety_release=False)
 def execute(self,*,execute_native=False):
  self.verify();need(self.prepared,'prepare required')
  if not execute_native:return dict(native_calls=0,prepared=str(self.out/'prepared'),requires_explicit_execution=True)
  need(execute_native is True and not self.attempted,'single explicit native occurrence; retries forbidden')
  need(self.host.window==94 and self.host.mode=='native','live native host at W94 required; read-only adoption not execution authority')
  self.attempted=True;put(self.out/'ATTEMPT.json',dict(branch_id=self.branch_id,reference_budget=1,automatic_retry=False))
  worker=B/'precision_worker.py';runner=R/'guangdong_rt_short_physical_v1_20261112/run_actual39.py'
  for f in (worker,runner):self.pins[str(f)]=sha(f)
  w=module('_reference_native_transport',runner);q=read(self.out/'prepared/REFERENCE_LP.json');n=read(self.out/'prepared/names.json');actual=self.out/'actual';actual.mkdir()
  request,response=w.native(actual,'SCED',q,worker,3600.);need({k:v for k,v in request.items()if k!='deadline'}=={k:v for k,v in q.items()if k!='deadline'},'worker altered registered reference model')
  derived=None
  try:review=w.checks(q,response,n,False)
  except Exception as ex:
   put(actual/'RAW_KKT_FAILURE.json',dict(reason=str(ex),native_response_unchanged=True));sys.path.insert(0,str(APP/'lifecycle_runtime/guangdong_network_dual_certificate_v1_20261113'));from network_certificate import certify
   certificate=certify(q,n,response);derived=certificate['derived_response'];need(derived is not None,'no valid separately derived certificate');review=w.checks(q,derived,n,False);put(actual/'DUAL_CERTIFICATE.json',certificate['certificate']);put(actual/'derived_response.json',derived)
  # Native raw remains intact. Capability is minted only from this session call.
  rv=dict(zip(n['variables'],(derived or response)['primal']));physical_path=self.da_root/'da_sced/physical_solution.json';physical=read(physical_path)['physical_cells'];agc=read(self.out/'prepared/agc.json');overlay=thermal_overlay(physical,rv,agc,self.units);target=self.out/'THERMAL_REFERENCE_OVERLAY.json';put(target,dict(physical_cells=overlay))
  self.pins[str(physical_path)]=sha(physical_path);self.pins.update({str(f):sha(f)for f in actual.rglob('*')if f.is_file()});self.pins[str(target)]=sha(target)
  self.accepted=dict(profile=PROFILE,branch_id=self.branch_id,path=str(target),sha256=sha(target),original_DA_physical_path=str(physical_path),original_DA_physical_sha256=sha(physical_path),source_pins=dict(self.pins),D0_unchanged=True,outside_reference_interval_unchanged=True,interval=[288,335],input_available_at=self.boundary['cutoff'],native_calls=1,raw_KKT_passed=derived is None,derived_certificate=None if derived is None else str(actual/'DUAL_CERTIFICATE.json'),review=review,formal_DA98=False,safety_release=False,publication=False)
  put(self.out/'ACCEPTED_REFERENCE.json',self.accepted);self.pins[str(self.out/'ACCEPTED_REFERENCE.json')]=sha(self.out/'ACCEPTED_REFERENCE.json');self.verify();return ReferenceOverlay(_KEY,self)
 def overlay(self):
  self.verify();need(self.accepted is not None,'no accepted reference');return ReferenceOverlay(_KEY,self)
def materialize(out,run,values,scuc,receipt,boundary,pins):
 """Pure math staging; confers NO capability, deliberately separate from host gate."""
 out=Path(out);run=Path(run);seed=out/'seed';seed.mkdir();
 for f in ('case.json','request.json','policy.json','WARMSTART_SOURCE.json'):shutil.copyfile(run/'seed'/f,seed/f)
 agc=read(run/'seed/agc.json')
 for g in agc:
  for side in ('up_mw','down_mw'):
   for t in range(288,576):agc[g][side][str(t)]=10. if t<300 else 0.
 put(seed/'agc.json',agc);date=boundary['boundary_at'][:10]
 declaration=dict(schema='independent_D1_reference_research_v1',input_available_at=boundary['cutoff'],official_publication=False,DA98_replacement=False,H25_and_later=boundary['unissued_following_hours'],days={date:{'artifact':{'thermal_online':{g:[values[f'online[{g},{t}]']>.5 for t in range(288,576)]for g in boundary['thermal']}}}})
 put(out/'REFERENCE_DECLARATION.json',declaration);carry=read(run/'INITIAL_CARRY.json');carry.update(cutoff_tick=282,delivery_tick=288,cutoff=boundary['cutoff'],boundary_at=boundary['boundary_at'],executed_end_exclusive=282,issued_end_exclusive=288,mode='original_DA_t287_predicted_boundary_not_actual_RT',day_declaration_path=str(out/'REFERENCE_DECLARATION.json'),day_declaration_sha256=sha(out/'REFERENCE_DECLARATION.json'),seed_case_sha256=sha(seed/'case.json'),seed_request_sha256=sha(seed/'request.json'))
 for g in boundary['thermal']:
  carry['thermal'][g].update(boundary['thermal'][g],event_id=None,event_elapsed_ticks=0,available_at=boundary['cutoff'],source='original accepted DA predicted t287, not future observed state');carry['notice_values'][g]={}
 carry['thermal_age_h']=boundary['thermal_age_h'];carry['storage']=boundary['storage'];carry['source_pins']=dict(pins);put(out/'INITIAL_CARRY.json',carry);put(out/'BUILD_INPUT.json',dict(values=values,scuc=scuc,boundary=boundary,declaration=declaration))
