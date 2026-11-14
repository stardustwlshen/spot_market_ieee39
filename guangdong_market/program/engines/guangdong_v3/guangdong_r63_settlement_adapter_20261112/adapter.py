"""JSON -> existing R36 typed arithmetic; never source authentication or a bill."""
from pathlib import Path
import sys,json,dataclasses,hashlib,argparse
from datetime import datetime,timedelta
from decimal import Decimal
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_r36_energy_settlement_20261112'))
import r36_energy as R
L=R.legacy
need=L.require

def stamp(s):return datetime.fromisoformat(s)
def evidence(d):
 need(isinstance(d,dict),'evidence object required');x=dict(d)
 for k in ('issued_at','available_at'):
  if x.get(k)is not None:x[k]=stamp(x[k])
 return L.Evidence(**x)
def value(d):
 need(isinstance(d,dict),'typed value required; unknown is not zero')
 return L.Value(None if d['value']is None else Decimal(str(d['value'])),d['unit'],evidence(d['evidence']))
def attestation(d):
 return None if d is None else L.Attestation(d['value'],evidence(d['evidence']))
def key(d):
 x=dict(d);x['start']=stamp(x['start']);x['end']=stamp(x['end']);k=L.Key(**x);k.validate();return k

def contract(d,k,channel):
 need(d['reference_market']in('DA','RT')and bool(d['reference_selection_source']),'contract-specific reference market/node selection source required')
 need(d['reference_price_kind']==('verified_published_settlement'if channel=='formal_data'else'synthetic_price_fixture'),'contract reference cannot be offer/raw solver dual')
 interval=d['reference_interval'];s,e=stamp(interval['start']),stamp(interval['end'])
 need(s==k.start.replace(minute=0) and e==s+timedelta(hours=1),'hourly reference interval required; no quarter reinterpretation')
 return L.Contract(d['contract_id'],d['reference_node_id'],value(d['quantity']),value(d['strike']),value(d['reference_price']),None if d.get('hourly_quantity')is None else value(d['hourly_quantity']))
def award(d,k,channel):
 need(d['price_kind']==('verified_published_settlement'if channel=='formal_data'else'synthetic_price_fixture'),'offer or raw model price is not settlement price')
 x={n:d[n]for n in ('market','run_id','pricing_run_id','version','publication_id','price_publication_status')};x.update(key=key(d['key']),price=value(d['price']),quantity=None if d.get('quantity')is None else value(d['quantity']))
 need(x['key']==k,'award account/node/interval mismatch');need(x['price_publication_status']=='final','provisional publication not accepted')
 return L.Award(**x)
def inputs(d):
 for p,h in json.loads((B/'PINS.json').read_text()).items():need(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,'retained R36 source changed')
 need(d['schema']=='gd.settlement.r63.ledger.v1','schema required');channel=d['channel'];need(channel in('simulation','formal_data'),'explicit channel')
 k=key(d['key']);asof=stamp(d['asof']);auth=d['authenticity']
 need(auth['status']in('synthetic_declared','externally_verified'),'explicit authenticity state/source needed');need(bool(auth['source']),'authenticity reference required')
 if channel=='formal_data':
  need(auth['status']=='externally_verified','formal_data requires caller-provided external authentication reference')
  need(d['meter_kind']=='metered','simulation/dispatch is not actual meter')
 else:need(auth['status']=='synthetic_declared'and d['meter_kind']in('synthetic_meter_fixture','simulated_executed'),'explicit simulated source')
 ev=evidence(d['account_mapping_evidence']);ev.validate(channel,asof)
 if channel=='formal_data':need(ev.status=='official','external account mapping evidence required')
 rows=d['contracts'];need(rows is not None,'contract inventory unknown')
 inv=evidence(d['contract_inventory_evidence']);inv.validate(channel,asof);need(inv.status!='missing','contract inventory evidence missing')
 if channel=='formal_data':need(inv.status=='official','official contract inventory evidence required')
 cs=tuple(contract(c,k,channel)for c in rows)
 if not cs:
  need(channel=='simulation'and d['known_empty_contracts']is True,'known empty contracts allowed only explicit research demonstration')
  # R36 requires a sourced zero row. This is a transparent internal identity,
  # not an invented commercial contract, and remains simulation/nonpayable.
  z=L.Value(Decimal(0),'MWh',inv);zp=L.Value(Decimal(0),'CNY/MWh',inv)
  cs=(L.Contract('__R63_SYNTHETIC_EMPTY_INVENTORY_ZERO__',k.node_id,z,zp,zp,z if k.end-k.start==timedelta(minutes=15)else None),)
 need(len({c.contract_id for c in cs})==len(cs),'duplicate contract ID')
 agency=d.get('agency_contracts');ac=None if agency is None else tuple(contract(c,k,channel)for c in agency)
 basis=d.get('basis')
 if basis is not None:
  basis=dict(basis);basis['validated']=attestation(basis['validated']);basis=L.SettlementBasis(**basis)
 out={n:d[n]for n in ('technology','participation','participation_source','agency_mode','settlement_class')}
 out.update(key=k,channel=channel,asof=asof,classification=attestation(d['classification']),applicability=attestation(d['applicability']),quarter_eligibility=attestation(d.get('quarter_eligibility')),agency_contracts=ac,contracts=cs,meter=value(d['meter']),da=award(d['da'],k,channel),rt=award(d['rt'],k,channel),basis=basis)
 for n in ('base','agency_export'):
  if n in d:out[n]=None if d[n]is None else value(d[n])
 return out

def settle(d):
 try:
  args=inputs(d);r=R.settle_energy(**args)
  return {'status':'energy_components_only','result':r,'formal_bill':None,'payable':False,'source_authentication_performed':False,'caller_authenticity':d['authenticity'],'empty_contract_internal_zero':not bool(d['contracts']),'rule_basis':'R36 retained formula; provincial2026 repost not newly authenticated; official general principles separately documented','native_calls':0}
 except (ValueError,KeyError,TypeError,ArithmeticError)as e:return {'status':'pending_input_or_scope','reason':str(e),'energy_component_subtotal':None,'formal_bill':None,'payable':False,'native_calls':0}
def run(d):
 if d.get('schema')=='gd.settlement.r63.aggregate_hour.v1':
  try:
   if d['kind']=='price':need(d['price_kind']==('verified_published_settlement'if d['channel']=='formal_data'else'synthetic_price_fixture'),'raw/candidate/offer is not settlement price')
   r=R.aggregate_hour(tuple((stamp(x['start']),value(x['value']))for x in d['quarters']),kind=d['kind'],channel=d['channel'],asof=stamp(d['asof']))
   return {'status':'aggregate_only','result':r,'formal_bill':None,'payable':False,'native_calls':0}
  except (ValueError,KeyError,TypeError,ArithmeticError)as e:return {'status':'pending_input_or_scope','reason':str(e),'formal_bill':None,'payable':False}
 if d.get('schema')=='gd.settlement.r63.storage_pair.v1':
  
  if any(d['discharge'].get(k)!=d['charge'].get(k)for k in ('channel','asof','participation')):return {'status':'pending_storage_pair','reason':'mixed channel/asof/participation','formal_bill':None,'payable':False}
  a,b=settle(d['discharge']),settle(d['charge'])
  if a['status']!='energy_components_only'or b['status']!='energy_components_only':return {'status':'pending_storage_pair','discharge':a,'charge':b,'formal_bill':None,'payable':False}
  try:r=R.paired_storage(a['result'],b['result'])
  except ValueError as e:return {'status':'pending_storage_pair','reason':str(e),'formal_bill':None,'payable':False}
  return {'status':'paired_energy_components_only','result':r,'discharge':a,'charge':b,'formal_bill':None,'payable':False,'native_calls':0}
 return settle(d)
def encode(x):
 if dataclasses.is_dataclass(x):return dataclasses.asdict(x)
 if isinstance(x,Decimal):return str(x)
 if isinstance(x,datetime):return x.isoformat()
 raise TypeError(type(x).__name__)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('--output',type=Path);a=ap.parse_args();r=run(json.loads(a.input.read_text()));s=json.dumps(r,default=encode,ensure_ascii=False,indent=2,allow_nan=False)
 if a.output:
  with a.output.open('x')as f:f.write(s)
 else:print(s)
 return 2 if r['status'].startswith('pending')else 0
if __name__=='__main__':raise SystemExit(main())
