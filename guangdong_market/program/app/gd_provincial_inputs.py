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

def snapshot(source,target,*,load_factor=1.,thermal_bid_factors=None,thermal_ramp_factors=None,thermal_edits=None,resource_edits=None,forecast_levels=None,additions=None):
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
 for g,f in (thermal_bid_factors or {}).items():
  d=e['thermal'][g];d['base_bid_yuan_per_h']={k:v*f for k,v in d['base_bid_yuan_per_h'].items()}
  for seg in d['segments']:seg['price_yuan_per_mwh']={k:v*f for k,v in seg['price_yuan_per_mwh'].items()}
 for g,f in (thermal_ramp_factors or {}).items():
  for key in ('ramp_up_mw_per_h','ramp_down_mw_per_h'):e['thermal'][g][key]={k:v*f for k,v in e['thermal'][g][key].items()}
 apply_thermal_edits(case,thermal_edits or {})
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
 (out/'USER_EDITS.json').write_text(json.dumps(dict(source_pins=pins,load_factor=load_factor,thermal_bid_factors=thermal_bid_factors or {},thermal_ramp_factors=thermal_ramp_factors or {},thermal_edits=thermal_edits or {},resource_edits=resource_edits or {},additions=additions or [],forecast_levels=forecast_levels,scope='DA seed baseline; RT source-bound correlated scenarios when enabled',formal=False),indent=2))
 return str(out)


def thermal_parameters(source):
 loads,_=codec();case=loads((Path(source)/'seed/case.json').read_bytes())
 return {g:{'bus':d['bus'],'min_mw':d['min_mw'][0],'max_mw':d['max_mw'][0],**{k:d[k] for k in ('initial_online','initial_power_mw','initial_state_age_h','min_up_h','min_down_h')}}for g,d in case['energy_inputs']['thermal'].items()}


def apply_thermal_edits(case,edits):
 allowed={'initial_online','initial_power_mw','initial_state_age_h','min_up_h','min_down_h'}
 for g,changes in edits.items():
  if g not in case['energy_inputs']['thermal']:raise ValueError('未知机组：'+g)
  if set(changes)-allowed:raise ValueError('尚未支持的机组参数：'+','.join(sorted(set(changes)-allowed)))
  d=case['energy_inputs']['thermal'][g];new={**d,**changes};online=new['initial_online']
  if type(online) not in (int,bool) or online not in (0,1):raise ValueError(g+'初始状态必须为开机或停机')
  for k in allowed-{'initial_online'}:
   v=new[k]
   if not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:raise ValueError(g+'参数必须为非负有限数：'+k)
  power=new['initial_power_mw']
  if online and not d['min_mw'][0]<=power<=d['max_mw'][0]:raise ValueError(g+'初始开机出力须在最小、最大出力之间')
  if not online and power!=0:raise ValueError(g+'初始停机时出力必须为0')
  d.update(changes);d['initial_online']=int(online);d['initial_state_kind']='stable' if online else 'off'
 return case
