"""Explicit research scenario snapshots; edits feed both fresh DA and RT."""
from pathlib import Path
import sys,json,hashlib,shutil,math

def codec():
 p=Path(__file__).resolve().parents[1]/'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'
 sys.path.insert(0,str(p))
 from smm.gd_october_job_codec import loads,dumps
 return loads,dumps

def inventory(source):
 loads,_=codec();c=loads((Path(source)/'seed/case.json').read_bytes())
 return sorted(c['energy_inputs']['thermal'])

def snapshot(source,target,*,load_factor=1.,thermal_bid_factors=None,thermal_ramp_factors=None,thermal_edits=None,resource_edits=None,forecast_levels=None,additions=None,renewable_bids=None):
 from gd_provincial_prepare import inputs_identity
 src=Path(source).resolve();out=Path(target).resolve();pins=inputs_identity(src)
 loads,dumps=codec();case=loads((src/'seed/case.json').read_bytes())
 agc=json.loads((src/'seed/agc.json').read_text());request=json.loads((src/'seed/request.json').read_text());policy=json.loads((src/'seed/policy.json').read_text())
 from gd_resource_add import add
 for item in additions or []:
  case,agc,request=add(case,agc,request,**item)
  if item['kind'] in ('coal','gas'):
   template=item['template'];g=item['resource_id']
   policy['minimum_adjustable_prices'][g]={**policy['minimum_adjustable_prices'][template],'source':'user-added template clone: '+template+'; research bid, not actual offer'}
 e=case['energy_inputs']
 def positive(v):
  if not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError('倍率须为有限正数')
 positive(load_factor)
 for table in (thermal_bid_factors or {},thermal_ramp_factors or {}):
  for g,v in table.items():
   if g not in e['thermal']:raise ValueError('未知火电机组：'+g)
   positive(v)
 e['load_mw']={k:v*load_factor for k,v in e['load_mw'].items()}
 for g,f in (thermal_ramp_factors or {}).items():
  for key in ('ramp_up_mw_per_h','ramp_down_mw_per_h'):e['thermal'][g][key]={k:v*f for k,v in e['thermal'][g][key].items()}
 apply_thermal_edits(case,thermal_edits or {},agc=agc)
 for g,f in (thermal_bid_factors or {}).items():
  d=e['thermal'][g];d['base_bid_yuan_per_h']={k:v*f for k,v in d['base_bid_yuan_per_h'].items()}
  for seg in d['segments']:seg['price_yuan_per_mwh']={k:v*f for k,v in seg['price_yuan_per_mwh'].items()}
 apply_renewable_bids(case,renewable_bids or {})
 from gd_resource_inputs import apply_resource_edits
 apply_resource_edits(case,resource_edits or {})
 out.mkdir(parents=True,exist_ok=False);shutil.copytree(src/'seed',out/'seed');shutil.copy2(src/'INITIAL_CARRY.json',out/'INITIAL_CARRY.json')
 (out/'seed/case.json').write_bytes(dumps(case))
 for filename,data in [('agc.json',agc),('request.json',request),('policy.json',policy)]:(out/'seed'/filename).write_text(json.dumps(data))
 if forecast_levels is not None:
  from gd_forecast_inputs import create
  create(case,out/'seed',forecast_levels)
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 context=json.loads((src/'SHORT_CONTEXT.json').read_text());context['seed_pins']={str(p):sha(p) for p in [*sorted((out/'seed').glob('*.json')),out/'INITIAL_CARRY.json']}
 (out/'SHORT_CONTEXT.json').write_text(json.dumps(context))
 (out/'USER_EDITS.json').write_text(json.dumps(dict(source_pins=pins,load_factor=load_factor,thermal_bid_factors=thermal_bid_factors or {},thermal_ramp_factors=thermal_ramp_factors or {},thermal_edits=thermal_edits or {},resource_edits=resource_edits or {},additions=additions or [],forecast_levels=forecast_levels,renewable_bids=renewable_bids or {},scope='DA seed baseline; RT source-bound correlated scenarios when enabled',formal=False),indent=2))
 return str(out)


def thermal_parameters(source):
 loads,_=codec();case=loads((Path(source)/'seed/case.json').read_bytes())
 from gd_resource_registry import thermal_types
 types=thermal_types(case)
 return {g:{'technology':types[g],'bus':d['bus'],'min_mw':d['min_mw'][0],'max_mw':d['max_mw'][0],**{k:d[k] for k in ('initial_online','initial_power_mw','initial_state_age_h','min_up_h','min_down_h')}}for g,d in case['energy_inputs']['thermal'].items()}


def capacity_cost_preview(technology,old_capacity,new_capacity):
 from gd_default_costs import profile
 return {'before':profile(technology,old_capacity),'after':profile(technology,new_capacity)}


def validate_thermal_edits(source,edits,*,ramp_factors=None):
 loads,_=codec();root=Path(source)
 case=loads((root/'seed/case.json').read_bytes())
 for g,f in (ramp_factors or {}).items():
  if not math.isfinite(f) or f<=0:raise ValueError('爬坡倍率须为正有限数')
  for key in ('ramp_up_mw_per_h','ramp_down_mw_per_h'):case['energy_inputs']['thermal'][g][key]={t:v*f for t,v in case['energy_inputs']['thermal'][g][key].items()}
 return apply_thermal_edits(case,edits,agc=json.loads((root/'seed/agc.json').read_text()))


def apply_thermal_edits(case,edits,*,agc=None):
 """Maximum output alone retains Pmin, prices, ramps and transition curves.
 Minimum output edits require the verified linear research template.
 Incremental bid MW shares are retained in the new [Pmin,Pmax] range.
 Validate the complete batch before mutating the caller's case.
 """
 from copy import deepcopy
 allowed={'initial_online','initial_power_mw','initial_state_age_h','min_up_h','min_down_h','max_mw','min_mw'}
 pending={};pending_curves={}
 for g,changes in edits.items():
  if g not in case['energy_inputs']['thermal']:raise ValueError('未知机组：'+g)
  if set(changes)-allowed:raise ValueError('尚未支持的机组参数：'+','.join(sorted(set(changes)-allowed)))
  d=deepcopy(case['energy_inputs']['thermal'][g])
  for k,v in changes.items():
   if k!='initial_online' and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0):raise ValueError(g+'参数必须为非负有限数：'+k)
  changed_min='min_mw' in changes and changes['min_mw']!=d['min_mw'][0]
  if changed_min:
   from gd_thermal_minimum import resize_minimum
   h=case['energy_inputs']['horizon'];periods=h['periods'] if isinstance(h,dict) else h.periods
   durations={x['duration_h'] if isinstance(x,dict) else x.duration_h for x in periods}
   if len(durations)!=1:raise ValueError('原始时间步长不统一')
   d['_minimum_edit_dt_h']=next(iter(durations))
   pending_curves[g]=resize_minimum(d,case['curves'][g],case['no_load_cost'][g],changes['min_mw'],changes.get('max_mw'))
   d.pop('_minimum_edit_dt_h')
  capacity=changes.get('max_mw',d['max_mw'][0])
  if changed_min or ('max_mw' in changes and capacity!=d['max_mw'][0]):
   old=d['max_mw'][0];minimum=d['min_mw'][0]
   if len(set(d['max_mw'].values()))!=1 or len(set(d['min_mw'].values()))!=1:raise ValueError(g+'存在分时出力限制，暂不支持统一修改容量')
   if not capacity>minimum:raise ValueError(g+'最大出力必须大于最小出力')
   if d['bid_base_mw']!=minimum:raise ValueError(g+'报价起点与最小出力不一致，不能自动调整分段')
   widths=[z['width_mw'] for z in d['segments']]
   if not widths or any(not math.isfinite(v) or v<=0 for v in widths) or not math.isclose(sum(widths),old-minimum,abs_tol=1e-7,rel_tol=1e-9):raise ValueError(g+'原报价分段未完整覆盖出力区间')
   for key in ('startup_output_limit_mw','shutdown_output_limit_mw'):
    if any(v>capacity for v in d[key].values()):raise ValueError(g+'容量小于启停出力限制，未自动修改启停参数')
   for t,v in d['fixed_power_mw'].items():
    if v is None:continue
    status=d['status'][t]
    if not math.isfinite(v) or v<0 or v>capacity or (v>0 and (v<minimum or status in ('off','unavailable'))) or (v==0 and status=='on' and minimum>0):raise ValueError(g+'固定出力与容量或开停状态冲突')
   for kind in ('startup','shutdown'):
    if any(max(row[k] for k in ('begin','mean','end'))>capacity for row in pending_curves.get(g,case['curves'][g])[kind]):raise ValueError(g+'启停轨迹超过修改后的容量')
   if agc is not None:
    z=agc[g]
    if any(z['up_mw'][t]+z['down_mw'][t]>capacity-minimum+1e-9 for t in z['up_mw']):raise ValueError(g+'调频预留超过修改后的可调出力范围')
   ratio=(capacity-minimum)/(old-minimum)
   for seg in d['segments']:seg['width_mw']*=ratio
   d['max_mw']={t:float(capacity) for t in d['max_mw']}
  d.update({k:v for k,v in changes.items() if k not in ('max_mw','min_mw')});online=d['initial_online']
  if type(online) not in (int,bool) or online not in (0,1):raise ValueError(g+'初始状态必须为开机或停机')
  for k in allowed-{'initial_online','max_mw','min_mw'}:
   v=d[k]
   if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:raise ValueError(g+'参数必须为非负有限数：'+k)
  power=d['initial_power_mw']
  if online and not d['min_mw'][0]<=power<=d['max_mw'][0]:raise ValueError(g+'初始开机出力须在最小、最大出力之间')
  if not online and power!=0:raise ValueError(g+'初始停机时出力必须为0')
  d['initial_online']=int(online);d['initial_state_kind']='stable' if online else 'off';pending[g]=d
 case['energy_inputs']['thermal'].update(pending);case['curves'].update(pending_curves)
 return case


def renewable_parameters(source):
 loads,_=codec();case=loads((Path(source)/'seed/case.json').read_bytes());e=case['energy_inputs']
 buses=sorted({str(b) for (b,t),v in e['renewable_mw'].items() if v>0})
 return {b:{'bid':float(e['renewable_bid_yuan_per_mwh'][b,0]),'constant':len({float(v) for (bus,t),v in e['renewable_bid_yuan_per_mwh'].items() if str(bus)==b})==1} for b in buses}


def apply_renewable_bids(case,bids):
 """Node-aggregate daily price; no separate wind/solar plant identity invented."""
 e=case['energy_inputs'];valid={str(b) for (b,t),v in e['renewable_mw'].items() if v>0}
 for bus,value in bids.items():
  if bus not in valid:raise ValueError('未知或没有新能源出力的节点：'+str(bus))
  if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('新能源报价必须为有限数值')
  # Upper bid bound not certified by present rules; do not invent a cap.
  if value < -50:raise ValueError('新能源报价不能低于 -50 元/MWh')
 for key in e['renewable_bid_yuan_per_mwh']:
  if str(key[0]) in bids:e['renewable_bid_yuan_per_mwh'][key]=float(bids[str(key[0])])
 return case
