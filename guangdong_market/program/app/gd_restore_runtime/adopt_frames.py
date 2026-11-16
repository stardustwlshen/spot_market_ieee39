"""Narrow exact-byte historical frame adapter. Never reissues old native work."""
from pathlib import Path
from copy import deepcopy
import json,re,gzip
from historical_store import HistoricalStore
from import_prefix import sha,need

def adopt(x,store,out):
 from v6_host import e
 h=x.host;original=store.data['logical_original_output'];out=Path(out);out.mkdir(exist_ok=False);original_checked=h.session.program.engine.checked_pair;registered={};proofs=[]
 def write(p,z):p.parent.mkdir(parents=True,exist_ok=True);need(not p.exists(),'no overwrite');p.write_text(json.dumps(z,indent=2))
 def physical(logical,expected=None):
  digest,d=store._record(logical,expected);store.bytes(logical,digest);return Path(d['path'])
 def pins(p):return {str(f.resolve()):sha(f.read_bytes())for f in p.rglob('*')if f.is_file()}
 def checked(prepared,actual):
  key=(str(Path(prepared).resolve()),str(Path(actual).resolve()))
  if key not in registered:return original_checked(prepared,actual)
  logical=registered[key];summary=store.json(logical+'/prepared/SUMMARY.json');store.verify_pins(summary['source_pins'])
  names=store.json(logical+'/prepared/names.json');q=store.json(logical+'/actual/SCUC/request.json.gz');u=store.json(logical+'/actual/SCUC/response.json.gz');lp=store.json(logical+'/actual/SCED/request.json.gz');r=store.json(logical+'/actual/SCED/response.json.gz');template=store.json(logical+'/prepared/template.json')
  for k in ('rows','objective'):need(q[k]==template[k],'historical template mismatch '+k)
  need([{k:v for k,v in c.items()if k!='start'}for c in q['variables']]==[{k:v for k,v in c.items()if k!='start'}for c in template['variables']],'historical template bounds')
  for name,response in [('SCUC',u),('SCED',r)]:
   job=store.json(logical+'/actual/'+name+'/JOB.json');done=store.json(logical+'/actual/'+name+'/COMPLETED.json');store.bytes(job['worker'],job['worker_sha256']);need(done['worker_sha256']==job['worker_sha256'],'worker differs')
   for desc,expected in [(job['request'],logical+'/actual/'+name+'/request.json.gz'),(done['response'],logical+'/actual/'+name+'/response.json.gz')]:
    need(desc['path']==expected,'native logical path mismatch');b=store.bytes(expected,desc['stored_sha256']);need(len(b)==desc['stored_bytes'],'native length');raw=gzip.decompress(b);need(sha(raw)==desc['raw_sha256']and len(raw)==desc['raw_bytes'],'native raw identity')
   need(done['solve_entries']==response['optimize_calls']==response['transport_solve_entries']==1,'native counter')
  proof=e.verify_pair(q,u,lp,r,names);e.dual_adapter().validate_record(actual,proof)
  return names,r
 h.session.program.engine.checked_pair=checked
 for w in range(store.data['first_unexecuted_window']):
  logical=original+'/pipeline/dispatch/W'+str(w);dest=out/f'W{w}'
  for p,versions in store.data['map'].items():
   if p.startswith(logical+'/'):
    need(len(versions)==1,'ambiguous window object');b=store.bytes(p);f=dest/p[len(logical)+1:];f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
  prepared=dest/'prepared';actual=dest/'actual';registered[(str(prepared.resolve()),str(actual.resolve()))]=logical
  names,r=checked(prepared,actual);carry=store.json(logical+'/EXACT_CARRY.json');incoming=store.json(logical+'/prepared/CARRY.json');result=store.json(logical+'/RESULT.json');pair=store.json(logical+'/actual/ACCEPTED_PAIR.json');current=json.loads(Path(h.cp).read_bytes())
  for k in ('thermal','thermal_age_h','storage','pumped_storage','notice_values','cutoff_tick','delivery_tick'):need(incoming[k]==current[k],'incoming state changed '+k)
  need(result['branch_id']==h.branch_id and pair['run_id']==h.session.registry['run_id'],'historical run identity differs')
  for key in ('seed_case_sha256','seed_request_sha256','origin','day_declaration_sha256','pumped_storage_declaration_sha256'):need(incoming[key]==current[key]and carry[key]==incoming[key],'historical input identity differs '+key)
  need(carry['delivery_tick']==w+1 and carry['executed_end_exclusive']==w+1 and carry['issued_end_exclusive']==w+1,'first-cell boundary')
  values=dict(zip(names['variables'],r['primal']));need(not any(abs(v)>1e-8 for k,v in values.items()if k.startswith(('start[','stop[','shutdown['))),'event state unsupported')
  for g,state in carry['thermal'].items():
   need(state['phase']in ('stable','off','offline')and not state.get('remaining_curve'),'active thermal curve unsupported')
   need(abs(state['end_power_mw']-values[f'power[{g},0]'])<1e-6,'thermal first-cell power')
   need(abs(carry['thermal_age_h'][g]-incoming['thermal_age_h'][g]-.25)<1e-7,'thermal age inheritance')
  from gd_provincial15_model.pumped_base import derive_pumped_carry
  case=h.session.program.engine.loads(store.bytes(logical+'/prepared/derived_case.json'))
  need(derive_pumped_carry(case,r,names)==carry['pumped_storage'],'pumped first-cell full state')
  import math
  for g,state in carry['storage'].items():
   b=case['batteries'][g];eta=math.sqrt(b['roundtrip_efficiency']);charge=values[f'battery.charge[{g},0]'];discharge=values[f'battery.discharge[{g},0]'];previous=incoming['storage'][g]
   mode='charge'if values[f'battery.charge_mode[{g},0]']>.5 else'discharge'if values[f'battery.discharge_mode[{g},0]']>.5 else'idle'
   need(abs(state['initial_energy_mwh']-values[f'battery.energy[{g},0]'])<1e-6,'first-cell SOC')
   need(abs(state['initial_energy_mwh']-(previous['initial_energy_mwh']+.25*(eta*charge-discharge/eta)))<1e-6,'SOC dynamics')
   need(abs(state['initial_power_mw']-(discharge-charge))<1e-6 and state['initial_mode']==mode,'battery mode/power')
   age=previous['initial_mode_age_h']+.25 if previous['initial_mode']==mode else .25;need(abs(state['initial_mode_age_h']-age)<1e-8,'battery mode age')
   day=incoming['boundary_at'][:10];oldcycle=previous['cycle_budget'][day]['used_internal_mwh'];newcycle=state['cycle_budget'][day]['used_internal_mwh'];need(abs(newcycle-oldcycle-.25*(eta*charge+discharge/eta))<1e-6,'battery cycle inheritance')

  store.bytes(logical+'/EXACT_CARRY.json',result['handoff']['carry_sha256']);store.bytes(logical+'/prepared/SUMMARY.json',pair['prepared_sha256']);store.verify_pins(carry['source_pins'])
  live=deepcopy(carry);live['source_pins']={str(physical(p,d)):d for p,d in carry['source_pins'].items()}
  # Source locators change only in this explicitly derived live carry, never raw.
  phase=live.get('accepted_control_phase')
  if phase:phase['path']=str(physical(phase['path'],phase['sha256']))
  live['restoration']={'kind':'exact_physical_state_new_source_locations','original_carry_sha256':result['handoff']['carry_sha256'],'original_logical_path':logical+'/EXACT_CARRY.json','import_manifest_sha256':store.digest,'new_native_calls':0}
  cp=out/f'DERIVED_CARRY_W{w}.json';write(cp,live);digest=sha(cp.read_bytes());local=pins(dest);local.update(live['source_pins']);local[str(cp.resolve())]=digest
  ledger=deepcopy(h.hooks.ledgers[h.lid]);ledger.pop('ledger_sha256');need(not ledger['receipts']and not ledger['pending'],'nonempty event state unsupported');ledger['cutoff_tick']=w;ledger['ledger_sha256']=e.digest(ledger);need(ledger['ledger_sha256']==carry['event_ledger_sha256'],'event state identity');h.hooks.ledgers[ledger['ledger_sha256']]=ledger;h.lid=ledger['ledger_sha256'];h.hooks.carries[str(cp)]=digest;h.cp=cp;h.accepted[w]=(prepared,actual,local);h.window=w+1
  result=deepcopy(result);result['handoff']['carry_path']=str(cp);result['handoff']['carry_sha256']=digest;result['historical_import']={'original_result_sha256':sha(store.bytes(logical+'/RESULT.json')),'new_native_calls':0};h.results.append(result)
  shifted={}
  for k,v in zip(names['variables'],r['primal']):
   m=re.search(r'(\d+)\]$',k)
   if m:k=k[:m.start(1)]+str(w+int(m[1]))+']'
   shifted[k]=v
  delta={k:v for k,v in shifted.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<w+1}
  report=dict(window=w,prepared=str(prepared),actual=str(actual),SCED_response_sha256=store.json(logical+'/actual/SCED/COMPLETED.json')['response']['raw_sha256'],historical_accepted_math_source=True,strict_pair_replayed=True,carry_registered=True,**{k:pair[k]for k in ('SCED_response_kind','dual_certificate_sha256','native_dual_passed')})
  data=dict(prepared=str(prepared),actual=str(actual),window_values=shifted,prefix_delta=delta,out=str(cp),carry=live,pins=local,report=report);x.prices.source._records.append({'data':data,'digest':e.digest(data)})
  proofs.append({'window':w,'original_carry_sha256':sha(store.bytes(logical+'/EXACT_CARRY.json')),'derived_carry_sha256':digest,'physical_fields_identical':True,'new_native_calls':0})
 for w in range(h.window):
  view=h.output/f'W{w}';view.mkdir(exist_ok=False);(view/'EXACT_CARRY.json').write_bytes((out/f'DERIVED_CARRY_W{w}.json').read_bytes())
 write(out/'ADOPTION.json',{'prefix':proofs,'new_native_calls':0,'source_closure_manifest_sha256':store.digest})
 return registered

def verify_historical_accounting(x,store,out):
 """Authenticate H0 once as historical accounting, not newly earned income."""
 from scoped_kkt import metrics
 out=Path(out);root=store.data['logical_original_output'];branch=x.host.branch_id
 checks=[]
 for w in range(4):
  q=root+f'/pipeline/price/q{w}';a=store.json(q+'/ACCEPTED.json');store.verify_pins(a['source_pins']);req=store.json(q+'/prepared/native_once/request.json');response=store.json(q+'/prepared/native_once/response.json');names=store.json(q+'/prepared/names.json');done=store.json(q+'/prepared/native_once/COMPLETED.json');identity=store.json(q+'/prepared/native_once/IDENTITY.json')
  store.bytes(q+'/prepared/native_once/response.json',done['response_sha256']);store.bytes(q+'/prepared/native_once/request.json',identity['request_sha256']);need({k:v for k,v in req.items()if k!='deadline'}==store.json(q+'/prepared/template.json'),'PRICE matrix identity')
  kkt=metrics(req,response,names,angle_tolerance=1e-8);need(kkt['passed'],'historical PRICE KKT')
  need(a['context']['run_id']==branch and a['context']['day_quarter']==w,'PRICE logical identity')
  rawsha=store.json(root+f'/pipeline/dispatch/W{w}/actual/SCED/COMPLETED.json')['response']['raw_sha256'];need(a['context']['SCED_response_sha256']==rawsha,'PRICE same SCED')
  for bus,value in a['raw_prices'].items():
   sensitivity=a['price_sensitivity'][bus];row=sensitivity['balance_row'];column=names['variables'].index(sensitivity['injection_anchor']);coeff=dict(req['rows'][row]['terms'])[column];need(abs(coeff*response['dual'][row]/.25-value)<1e-5,'PRICE dual reconstruction')
  checks.append({'quarter':w,'KKT_passed':True,'price_sha256':sha(store.bytes(q+'/ACCEPTED.json'))})
 hour=store.json(root+'/pipeline/price/HOUR0.json');store.verify_pins(hour['source_pins']);need(hour['run_id']==branch and hour['input_binding']['hour']==0 and hour['executed_cells']==4,'historical H0 identity')
 # Preserve original H0 bytes, with a separate lineage declaring no new settlement.
 target=x.prices.output/'HOUR0.json';need(not target.exists(),'H0 already present');target.write_bytes(store.bytes(root+'/pipeline/price/HOUR0.json'));x.prices.execution[0]=(target,sha(target.read_bytes()))
 for stem in ('SETTLEMENT_H0.json','EXECUTION_H0.json'):
  original=root+'/pipeline/price/'+stem
  if original in store.data['map']:(x.prices.output/stem).write_bytes(store.bytes(original))
 report={'schema':'gd.historical_hour_import.v1','hour':0,'quarters':checks,'original_hour_sha256':sha(target.read_bytes()),'source_logical_path':root+'/pipeline/price/HOUR0.json','import_manifest_sha256':store.digest,'new_settlement_applications':0,'historical_hour_count':1,'new_native_calls':0}
 (out/'H0_IMPORT.json').write_text(json.dumps(report,indent=2));return report

def verify_rebased_agc(x,store,out):
 original=store.data['logical_original_output']+'/pipeline/dispatch/agc/H01.json';old=store.json(original);new=json.loads((x.host.agc.root/'H01.json').read_bytes());a=deepcopy(old);b=deepcopy(new)
 for key,digest in a['verified_plan']['source_pins'].items():store.bytes(key,digest)
 for key,digest in b['verified_plan']['source_pins'].items():need(sha(Path(key).read_bytes())==digest,'new AGC source changed')
 # All source matrices and responses are byte-identical; only physical paths differ.
 oldhashes=sorted(a['verified_plan'].pop('source_pins').values());newhashes=sorted(b['verified_plan'].pop('source_pins').values());need(oldhashes==newhashes,'AGC source bytes differ')
 a['context'].pop('plan_sha256');b['context'].pop('plan_sha256');a.pop('context_sha256');b.pop('context_sha256');need(a==b,'AGC decision/time/parameters changed')
 proof={'schema':'gd.agc.exact_source_relocation.v1','original_receipt_sha256':sha(store.bytes(original)),'derived_receipt_sha256':sha((x.host.agc.root/'H01.json').read_bytes()),'all_nonprovenance_fields_identical':True,'native_calls':0}
 (Path(out)/'AGC_H1_RELOCATION.json').write_text(json.dumps(proof,indent=2));return proof
