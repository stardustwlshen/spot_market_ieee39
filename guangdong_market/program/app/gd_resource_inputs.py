"""Editable physical declarations, applied before both fresh DA and RT initialization."""
from copy import deepcopy
import json, math, hashlib
from pathlib import Path
BATTERY_FIELDS=('energy_capacity_mwh','energy_min_mwh','energy_max_mwh','initial_energy_mwh','terminal_energy_mwh','charge_max_mw','discharge_max_mw')
PUMPED_FIELDS=('vup0','vup_end_bid','v_total','vup_min','vup_max','vdn_min','vdn_max','unit_gen_min','unit_gen_max','unit_pump')
def read_case(source):
 from gd_provincial_inputs import codec
 return codec()[0]((Path(source)/'seed/case.json').read_bytes())
def parameters(source):
 c=read_case(source)
 return {'battery':{g:{k:z[k] for k in BATTERY_FIELDS}for g,z in c['batteries'].items()},'pumped':{g:{k:z['parameters'][k]for k in PUMPED_FIELDS}for g,z in c['battery_contract']['storage']['pumped_storage']['inventory'].items()}}
def finite(z):
 if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in z.values()):raise ValueError('参数必须是非负有限数')
def apply_resource_edits(case,edits):
 # Validate on copy so invalid submission never partially mutates source.
 c=deepcopy(case)
 if set(edits)-{'battery','pumped'}:raise ValueError('不支持的资源类型')
 for g,changes in edits.get('battery',{}).items():
  if g not in c['batteries'] or set(changes)-set(BATTERY_FIELDS):raise ValueError('未知储能或不支持的参数')
  d=c['batteries'][g];old=deepcopy(d);d.update(changes);finite({k:d[k]for k in BATTERY_FIELDS})
  lo,hi=d['energy_min_mwh'],d['energy_max_mwh']
  if not 0<=lo<hi<=d['energy_capacity_mwh']:raise ValueError('储能电量须满足 0≤下限<上限≤额定容量')
  if not all(lo<=d[k]<=hi for k in ('initial_energy_mwh','terminal_energy_mwh')):raise ValueError('初始电量和日末目标须在电量上下限内')
  if min(d['charge_max_mw'],d['discharge_max_mw'])<=0:raise ValueError('充放电功率上限须为正数')
  if not -d['charge_max_mw']<=d['initial_power_mw']<=d['discharge_max_mw']:raise ValueError('初始功率超出新容量')
  for side in ('charge','discharge'):
   # Preserve existing prices/relative segment widths, explicitly resize MW blocks.
   key=side+'_max_mw';curve=d[side+'_curve'];curve['breakpoints_mw']=tuple(x*d[key]/old[key]for x in curve['breakpoints_mw'])
 for g,changes in edits.get('pumped',{}).items():
  inv=c['battery_contract']['storage']['pumped_storage']['inventory']
  if g not in inv or set(changes)-set(PUMPED_FIELDS):raise ValueError('未知抽蓄或不支持的参数')
  z=inv[g];p=z['parameters'];p.update(changes);finite({k:p[k]for k in PUMPED_FIELDS})
  if any(mode!='off' for mode in z['modes'].values()):raise ValueError('目前仅支持停机初态抽蓄的参数编辑')
  if not 0<p['unit_gen_min']<=p['unit_gen_max'] or min(p['unit_gen_max'],p['unit_pump'])<=0:raise ValueError('抽蓄单机功率范围不合理')
  if p.get('variable_speed') and p['unit_pump_min']>p['unit_pump']:raise ValueError('抽水功率低于变速机组最小功率')
  if not 0<=p['vup_min']<p['vup_max']<=p['v_total'] or not 0<=p['vdn_min']<p['vdn_max']<=p['v_total']:raise ValueError('水库上下限须在总水量内')
  for k in ('vup0','vup_end_bid'):
   if not p['vup_min']<=p[k]<=p['vup_max'] or not p['vdn_min']<=p['v_total']-p[k]<=p['vdn_max']:raise ValueError('初始/日末上库水量或对应下库水量越界')
  z['upper_m3']=p['vup0']*10000;z['lower_m3']=(p['v_total']-p['vup0'])*10000
 bc=c['battery_contract'];payload=json.dumps(dict(batteries=c['batteries'],pumped_storage=bc['storage']['pumped_storage']),sort_keys=True,allow_nan=False)
 bc['sources'][bc['storage']['battery_parameters_source_id']]['artifact']=payload
 bc['storage']['inventory_sha256']=hashlib.sha256(payload.encode()).hexdigest()
 case.clear();case.update(c);return case
