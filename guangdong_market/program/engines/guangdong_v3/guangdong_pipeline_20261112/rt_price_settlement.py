"""Fresh-input independent quarter PRICE and simulation settlement adapters.
No solver calls. Frozen mathematics reused, never old prepared artifacts/results.
"""
from pathlib import Path
import importlib.util,json,hashlib,sys,math,dataclasses
from datetime import datetime,timedelta
from decimal import Decimal
ROOT=Path(__file__).resolve().parent.parent

def need(ok,msg):
 if not ok:raise ValueError(msg)
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def json_safe(value):
 """Exact accounting transport: Decimal as text; datetime ISO; no float conversion."""
 if isinstance(value,Decimal):
  need(value.is_finite(),'nonfinite Decimal');return str(value)
 if isinstance(value,datetime):return value.isoformat()
 if dataclasses.is_dataclass(value):return json_safe(dataclasses.asdict(value))
 if isinstance(value,dict):return {k:json_safe(v)for k,v in value.items()}
 if isinstance(value,(tuple,list)):return [json_safe(v)for v in value]
 if isinstance(value,float):need(math.isfinite(value),'nonfinite float')
 return value

def dumps_exact(value):
 return json.dumps(json_safe(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def validate_price_manifest(man,receipt):
 need(man['schema']=='gd.pipeline.rt_price_manifest.v1','explicit fresh PRICE manifest')
 need(man['run_id']==receipt['run_id'] and man['source_contract_sha256']==receipt['source_contract_sha256'],'manifest same execution run/source')
 rows=man['records'];need(type(rows)is list and len(rows)==96,'96 records required')
 windows=[x['window']for x in rows]
 need(all(type(w)is int and 0<=w<96 for w in windows),'window index must be integer0..95')
 need(len(set(windows))==96,'duplicate or missing windows')
 return rows

def validate_price_receipt(inp,receipt):
 need(receipt['stage']=='RT' and receipt['accepted']is True,'actual accepted current RT required')
 need(receipt['run_id']==inp['projection']['run_id'] and receipt['source_contract_sha256']==inp['contract_sha256'],'same fresh run/source')
 need(receipt['local_input_content_sha256']==digest(inp),'exact local24 input binding')
 need(receipt['window']==inp['window']['index'],'same actual window')
 need(receipt['event_id'] and receipt['request_sha256'] and receipt['response_sha256'],'native occurrence provenance')
 need(receipt.get('research_only')is True,'research branch only')

def build_rt_price(inp,receipt):
 """Return (payload,names,trace), never an RT execution state or live permit."""
 validate_price_receipt(inp,receipt)
 math_module=module('_pipeline_r69_price_math',ROOT/'guangdong_r69_tail_source_price_20261112/pricing_math.py')
 q,n,c_trace,base_q,base_n=math_module.build_local_price(inp)
 quarter=module('_pipeline_r74_quarter_transform',ROOT/'guangdong_r74_quarter_price_20261112/prepare.py')
 out,names,tr=quarter.transform(q,n,inp)
 return out,names,dict(schema='gd.pipeline.independent_quarter_PRICE.v1',source_receipt=receipt,input_content_sha256=digest(inp),C_boundary=c_trace,quarter_transform=tr,mathematical_domain_changed=True,source_transport_only_delta=False,RT_dual_used=False,RT_execution_state_modified=False,market_price=None,settlement_price=None,formal_bill=None,new_native_calls=0)

def validate_execution(source,cells,da,inputs,receipt):
 need(receipt['complete']is True and receipt['executed_cells']==288,'complete actual simulated execution required')
 need(len(cells)==len(da)==288 and len(inputs)==96,'288 execution/DA cells and96 actual local inputs')
 need(receipt['source_contract_content_sha256']==digest(source),'source content binding')
 need(receipt['executed_cells_content_sha256']==digest(cells)and receipt['DA_cells_content_sha256']==digest(da),'quantity provenance')
 day=datetime.fromisoformat(source['day_start'])
 for j,(c,d)in enumerate(zip(cells,da)):
  st=(day+timedelta(minutes=5*j)).isoformat();end=(day+timedelta(minutes=5*(j+1))).isoformat()
  need(c['start']==d['start']==st and c['end']==d['end']==end,'no execution gap/DA mismatch')
 for w,i in enumerate(inputs):
  need(i['window']['index']==w and i['projection']['run_id']==receipt['run_id'],'same current RT run')
  need(i['contract_sha256']==receipt['source_contract_sha256'],'same contract per input')
 return day

def bound_hourly_prices(da_raw,rt_records,*,day,source_receipt,price_limit_spec):
 """Caller supplies actual verified receipts. Missing/nonoptimal prices reject; no zero/fallback."""
 sys.path.insert(0,str(ROOT/'guangdong_r47_price_settlement_contract_20261112'))
 from r47_price import price_candidate,hourly_candidate
 nodes=set(da_raw);need(len(nodes)==39 and all(len(v)>=96 for v in da_raw.values()),'complete DA raw prices')
 need(len(rt_records)==96 and {r['window']for r in rt_records}==set(range(96)),'complete96 PRICE records')
 by={r['window']:r for r in rt_records};out={'DA':{},'RT':{}};trace=[]
 for w,r in by.items():
  need(r['status']=='verified_research_raw_price'and r['source_contract_sha256']==source_receipt['source_contract_sha256']and r['run_id']==source_receipt['run_id'],'same-run independently verified raw PRICE')
  need(r['RT_dual_used']is False and set(r['raw_prices'])==nodes and r['response_sha256'],'independent raw price provenance')
 for stage in ('DA','RT'):
  for node in sorted(nodes):
   qs=[]
   for w in range(96):
    rec=by[w];raw=da_raw[node][w]if stage=='DA'else rec['raw_prices'][node];need(type(raw)in(int,float)and math.isfinite(raw),'missing/nonfinite raw price')
    st=day+timedelta(minutes=15*w);h=source_receipt['DA_PRICE_response_sha256']if stage=='DA'else rec['response_sha256']
    qs.append(price_candidate(spec=price_limit_spec,status='success',run_id=source_receipt['run_id']+':'+stage,node=node,start=st,end=st+timedelta(minutes=15),stage=stage,raw_price=Decimal(str(raw)),evidence_id=stage+':'+h,evidence_sha256=h))
   out[stage][node]=[]
   for hour in range(24):
    z=hourly_candidate(qs[hour*4:hour*4+4]);out[stage][node].append(dict(kind='source_verified_bounded_research_price',channel='simulation',node=node,start=z['start'].isoformat(),stage=stage,value=z['bounded_hourly_candidate_price'],source_sha256=hashlib.sha256(str([(q['raw_price'],q['evidence_sha256'])for q in qs[hour*4:hour*4+4]]).encode()).hexdigest(),aggregation='quarter_clip_then_arithmetic_mean_four',official_publication=False,formal_bill=None))
   trace.extend(qs)
 return out,trace

def compute_settlement(source,cells,da,inputs,execution_receipt,hour_prices,*,explicit_synthetic_contract_assumptions):
 need(explicit_synthetic_contract_assumptions is True,'synthetic participation/known-empty contracts must be explicit; unknown is not zero')
 start=validate_execution(source,cells,da,inputs,execution_receipt)
 # Only the pure compute/assumptions functions are called. No fixed RUN/DA reader.
 candidates=list(ROOT.glob('guangdong_r75*/assemble_v2.py'));need(len(candidates)==1,'unique retained R75 arithmetic')
 import ast
 arithmetic=module('_pipeline_frozen_r75_arithmetic',candidates[0].parent/'arithmetic.py')
 tree=ast.parse(candidates[0].read_text());pure=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in ('assumptions','compute')],type_ignores=[])
 namespace=dict(arithmetic.__dict__);exec(compile(pure,str(candidates[0]),'exec'),namespace)
 result=namespace['compute'](None,source,cells,da,inputs,start,hour_prices)
 result.update(schema='gd.pipeline.simulated_energy.v1',fresh_execution_receipt=execution_receipt,retained_R75_compute_sha256=sha(candidates[0]),source_readers_used=False,formal_bill=None,payable=False)
 return result

def load_execution(run_root,rt_root,da_artifacts_path):
 """Read only this run's relative-hash artifacts and R67-shaped actual receipts."""
 root=Path(run_root).resolve();rt=Path(rt_root).resolve();pins={}
 def pin(p,expected=None):
  p=Path(p).resolve();need(p.is_relative_to(root),'artifact outside current run');h=sha(p);need(expected is None or h==expected,'artifact hash mismatch');pins[str(p)]=h;return json.loads(p.read_bytes())
 def ref(z):return pin(root/z['path'],z['sha256'])
 art=pin(da_artifacts_path);need(art['schema']=='gd.pipeline.da_artifacts.v1'and art['status']=='accepted','new accepted DA chain')
 source=pin(rt/'SOURCE_CONTRACT.json');contract_hash=sha(rt/'SOURCE_CONTRACT.json');identity=pin(rt/'SOURCE_IDENTITY.json');result=pin(rt/'RUN_RESULT.json');drain=pin(rt/'D96_DRAIN.json');report=result['report'];need(report['same_controller_full96_complete']is True and report['executed_RT_cells']==report['issued_RT_cells']==288 and drain['report']==report,'complete executed day')
 cells=[];inputs=[]
 for w in range(96):
  a=pin(rt/f'W{w}/accepted/ACCEPTANCE.json');need(a['window']==w and a['KKT']['passed']is True and a['issued_cells']==3,'actual RT accepted')
  need(a['source_contract_sha256']==contract_hash and a['source_identity_sha256']==identity['sha256'],'same source receipt')
  p=rt/f'W{w}/prepared/input.json';i=pin(p,a['local_RT_input_sha256']);inputs.append(i)
  commands=pin(rt/f'W{w}/accepted/issued_commands.json');need(len(commands)==3,'issued first3')
  for k,c in enumerate(commands):need(c['global_cell_index']==3*w+k and c['accepted_event']==a['event_id'],'accepted execution binding');cells.append(c['cell'])
 da=source['physical_cells'][:288];physical=ref(art['stages']['da_sced']['physical_solution']);need(len(physical['physical_cells'])>=288,'same-run DA physical')
 # Source producer must expose exact actual DA artifact binding, not a legacy permit.
 expected=art['stages']['da_sced']['physical_solution'];bindings=source['source_bindings'];need(bindings.get(str((root/expected['path']).resolve()))==expected['sha256'],'RT source binds new DA physical')
 receipt=dict(run_id=inputs[0]['projection']['run_id'],source_contract_sha256=contract_hash,source_contract_content_sha256=digest(source),executed_cells_content_sha256=digest(cells),DA_cells_content_sha256=digest(da),executed_cells=288,complete=True,DA_artifacts_sha256=sha(da_artifacts_path),DA_run_id=art['run_id'],DA_PRICE_response_sha256=art['stages']['da_price']['response']['sha256'])
 validate_execution(source,cells,da,inputs,receipt)
 for p,h in pins.items():need(sha(p)==h,'source changed during read')
 return dict(source=source,cells=cells,da=da,inputs=inputs,receipt=receipt,source_pins=pins)

def load_prices(run_root,da_artifacts_path,price_manifest_path,execution_bundle,*,price_limit_spec):
 """PRICE manifest: records each {window,receipt:{path,sha256},response:{path,sha256}}."""
 root=Path(run_root).resolve();pins={}
 def pinref(z):
  p=(root/z['path']).resolve();need(p.is_relative_to(root)and sha(p)==z['sha256'],'current-run price artifact binding');pins[str(p)]=z['sha256'];return json.loads(p.read_bytes())
 art=json.loads(Path(da_artifacts_path).read_bytes());need(sha(da_artifacts_path)==execution_bundle['receipt']['DA_artifacts_sha256'],'same DA artifacts')
 raw=pinref(art['stages']['da_price']['raw_prices'])['raw_unclipped_LMP'];pinref(art['stages']['da_price']['response']);pinref(art['stages']['da_price']['acceptance'])
 man=json.loads(Path(price_manifest_path).read_bytes());items=validate_price_manifest(man,execution_bundle['receipt']);records=[]
 for item in items:
  rec=pinref(item['receipt']);response=pinref(item['response']);w=item['window'];need(rec['window']==w and rec['response_sha256']==item['response']['sha256'],'same PRICE occurrence');need(response['status']=='optimal'and response['native_status']=='Optimal'and response['has_primal']and response['dual_valid'],'missing native PRICE')
  need(rec['local_input_content_sha256']==digest(execution_bundle['inputs'][w]),'PRICE exact actual local input');need(rec['KKT']['passed']is True,'independent PRICE KKT required');records.append(rec)
 out,trace=bound_hourly_prices(raw,records,day=datetime.fromisoformat(execution_bundle['source']['day_start']),source_receipt=execution_bundle['receipt'],price_limit_spec=price_limit_spec)
 for p,h in pins.items():need(sha(p)==h,'price artifact changed')
 return out,trace,pins
