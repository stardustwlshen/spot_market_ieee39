"""Template-based research resource additions; no inferred operating qualification."""
from copy import deepcopy
import re
from gd_resource_registry import initialize_registry,thermal_types
from gd_resource_inputs import apply_resource_edits

def add(case,agc,request,*,kind,template,resource_id,bus):
 c,a,r=deepcopy(case),deepcopy(agc),deepcopy(request)
 e=c['energy_inputs'];inv=c['battery_contract']['storage']['pumped_storage']['inventory']
 existing=set(e['thermal'])|set(c['batteries'])|set(inv)
 if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,30}',resource_id)or resource_id in existing:raise ValueError('机组编号须唯一且仅含字母数字下划线')
 if str(bus)not in map(str,e['buses']):raise ValueError('节点不存在')
 if kind in ('coal','gas'):
  reg=initialize_registry(c)
  if template not in e['thermal']or thermal_types(c)[template]!=kind:raise ValueError('模板类型不匹配')
  d=e['thermal'][resource_id]=deepcopy(e['thermal'][template]);d.update(bus=str(bus),initial_online=0,initial_power_mw=0.,initial_state_age_h=24.,initial_state_kind='off')
  d['source']='user-declared synthetic '+kind+' clone of '+template
  c['curves'][resource_id]=deepcopy(c['curves'][template]);c['no_load_cost'][resource_id]=c['no_load_cost'][template]
  for k in ('primary_fraction','contingency_eligible'):c['reserve'][k][resource_id]=c['reserve'][k][template]
  a[resource_id]=deepcopy(a[template]);a[resource_id].update(qualification='no_award',source='new synthetic resource; AGC qualification not supplied; no award')
  for k in ('up_mw','down_mw'):a[resource_id][k]={t:0. for t in a[resource_id][k]}
  r['preorigin_notices'][resource_id]={t:0 for t in r['preorigin_notices'][template]}
  reg['thermal'][resource_id]=dict(technology=kind,bus=str(bus),source='user-declared synthetic resource cloned from '+template)
 elif kind=='battery':
  if template not in c['batteries']:raise ValueError('储能模板不存在')
  c['batteries'][resource_id]=deepcopy(c['batteries'][template]);c['batteries'][resource_id]['bus']=str(bus)
 elif kind=='pumped':
  if template not in inv:raise ValueError('抽蓄模板不存在')
  inv[resource_id]=deepcopy(inv[template]);z=inv[resource_id]
  if any(m!='off'for m in z['modes'].values()):raise ValueError('抽蓄模板须初始停机')
  z['parameters'].update(id=resource_id,bus=int(bus));z['modes']={resource_id+'_'+str(i+1):'off'for i in range(z['parameters']['n_units'])}
 else:raise ValueError('当前仅支持煤机、燃机、储能、抽蓄；风光是节点聚合，不能伪造独立机组')
 apply_resource_edits(c,{})
 return c,a,r
