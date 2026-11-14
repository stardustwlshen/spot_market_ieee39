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
 # Same executed-hour ledger feeds unit economics without a second solve.
 # Missing actual service/cost inputs stay missing; bids are never inferred as costs.
 from gd_unit_economics import summarize, from_components
 try:
  hour=_checked_hour(session)
  if fields.get('unit_economics_components') is not None:
   out['unit_economics']=from_components(hour,**fields['unit_economics_components'])
  else:
   out['unit_economics']=summarize(hour,fields.get('unit_economics'))
 except (ValueError,TypeError,KeyError) as exc:out['unit_economics']={'status':'input_rejected','reason':str(exc),'formal_bill':None}
 return out

def _checked_hour(session):
 session.check()
 return dict(session.h,_hour_sha256=session.digest)
