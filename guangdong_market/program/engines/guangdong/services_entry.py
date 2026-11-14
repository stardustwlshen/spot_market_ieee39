"""Pure service interfaces, isolated process; no missing actual data manufactured."""
from pathlib import Path
import sys,json,argparse,importlib.util,dataclasses
from decimal import Decimal
from datetime import datetime
B=Path(__file__).resolve().parent
ROUTES={'energy':'guangdong_r63_settlement_adapter_20261112/adapter.py','agc-safety':'guangdong_r77_agc_safety_gate_20261112/gate.py','storage-agc':'guangdong_r78_storage_agc_competition_20261112/storage.py','mixed-agc':'guangdong_r79_mixed_agc_projection_20261112/mixed.py'}
def load(path):
 spec=importlib.util.spec_from_file_location('delivery_service',B/path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m

def main():
 p=argparse.ArgumentParser();p.add_argument('module',choices=list(ROUTES)+['reserve','agc-payment']);p.add_argument('input');p.add_argument('--output');a=p.parse_args();d=json.loads(Path(a.input).read_bytes())
 if a.module in ROUTES:r=load(ROUTES[a.module]).run(d)
 elif a.module=='reserve':
  m=load('guangdong_r54_spinning_reserve_accounting_20261112/r54_reserve.py');s=dict(d['scope']);s['start']=datetime.fromisoformat(s['start']);s['end']=datetime.fromisoformat(s['end'])
  for k in ('lower_requirement_mw','rate_yuan_per_MWh_service'):
   if k in s and s[k]is not None:s[k]=Decimal(str(s[k]))
  units=[]
  for z in d['units']:
   u=dict(z)
   for k in ('rated_mw','declared_upper_mw','actual_mw','nuclear_normal_lower_mw'):
    if k in u and u[k]is not None:u[k]=Decimal(str(u[k]))
   units.append(m.Unit(**u))
  r=m.calculate(m.Scope(**s),tuple(units))
 else:
  if any(d.get(k)is None for k in ('measured_mileage_mw','uniform_price_yuan_mw','settlement_performance')):
   r=dict(status='missing_actual_mileage_price_or_performance',formal_bill=None,payable=False);print(json.dumps(r));return
  sys.path.insert(0,str(B/'guangdong_after_reset_r23_compressed_20261112/repo/src'));r=load('guangdong_r27_agc_settlement_20261112/r27_agc.py').service_gross_statement(**d)
 def encode(x):
  if isinstance(x,Decimal):return str(x)
  if isinstance(x,datetime):return x.isoformat()
  if dataclasses.is_dataclass(x):return dataclasses.asdict(x)
  raise TypeError(type(x).__name__)
 text=json.dumps(r,default=encode,ensure_ascii=False,indent=2,allow_nan=False)
 if a.output:
  with open(a.output,'x')as f:f.write(text)
 else:print(text)
if __name__=='__main__':main()
