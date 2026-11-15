"""Live receipts and separate read-only research export arithmetic; no JSON authority."""
from pathlib import Path
import importlib.util,sys,hashlib
B=Path(__file__).resolve().parent

def load():
 name='_gd_installed_settlement_v2'
 if name not in sys.modules:
  spec=importlib.util.spec_from_file_location(name,B/'gd_settlement_runtime/settlement_session.py');m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
 return sys.modules[name]

def from_export(path,run_id,digest):
 # Digest/run checks do not constitute a live acceptance, nor a formal bill.
 return load().SettlementSession(path,expected_run_id=run_id,expected_hour_sha256=digest)

def from_live(prices,index):
 receipt=prices.hour_receipt(index)
 return from_export(receipt['path'],receipt['run_id'],receipt['sha256'])

def components(session,fields=None):
 fields=fields or {};out={}
 for name in ('energy','agc','reserve','non_da_renewable','operating_compensation'):
  try:out[name]=getattr(session,name)(fields.get(name,{}))
  except (ValueError,TypeError,KeyError)as exc:out[name]={'status':'input_rejected','reason':str(exc),'formal_bill':None,'payable':False}
 # Per-resource service inputs are calculated here, never manually entered totals.
 out['agc_by_resource']={}
 for resource,resource_fields in fields.get('agc_by_resource',{}).items():
  try:
   if resource not in {r['resource'] for r in _checked_hour(session)['rows']}:raise ValueError('unknown AGC resource')
   result=session.agc(resource_fields)
   if result.get('component')is not None:result['component']['resource']=resource
   out['agc_by_resource'][resource]=result
  except (ValueError,TypeError,KeyError)as exc:out['agc_by_resource'][resource]={'status':'input_rejected','reason':str(exc),'formal_bill':None,'payable':False}
 # Same executed-hour ledger feeds unit economics without a second solve.
 # Missing actual service/cost inputs stay missing; bids are never inferred as costs.
 from gd_unit_economics import summarize, from_components
 try:
  hour=_checked_hour(session)
  if fields.get('unit_economics_components') is not None:
   out['unit_economics']=from_components(hour,**fields['unit_economics_components'])
  elif fields.get('unit_economics') is not None:
   out['unit_economics']=summarize(hour,fields['unit_economics'])
  else:
   from gd_auto_economics import prepare,attach_estimates
   automatic=prepare(hour,fields.get('agc_mileage_per_capacity_hour',10),False if out['reserve'].get('component') is not None else fields.get('reserve_estimate_parameters'))
   out['automatic_economics_inputs']=automatic
   selected_costs={r:dict(c)for r,c in automatic['costs'].items()}
   for resource,override in (fields.get('costs') or {}).items():
    if resource not in selected_costs:raise ValueError('unknown cost override resource')
    selected_costs[resource].update(override)
   out['unit_economics']=from_components(hour,costs=selected_costs,agc_by_resource={k:v for k,v in out['agc_by_resource'].items() if v.get('component') is not None},reserve_component=out['reserve'] if out['reserve'].get('component') is not None else None,source='same-run calculated service components; cost assumptions explicit')
   out['unit_economics']=attach_estimates(out['unit_economics'],automatic)
 except (ValueError,TypeError,KeyError) as exc:out['unit_economics']={'status':'input_rejected','reason':str(exc),'formal_bill':None}
 return _json_amounts(out)

def _checked_hour(session):
 session.check()
 return dict(session.h,_hour_sha256=session.digest)


def _json_amounts(value):
 """Preserve exact money as decimal text at the JSON/export boundary."""
 from decimal import Decimal
 if isinstance(value,Decimal):
  if not value.is_finite():raise ValueError('nonfinite money cannot be exported')
  return str(value)
 if isinstance(value,dict):return {k:_json_amounts(v)for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [_json_amounts(v)for v in value]
 return value
