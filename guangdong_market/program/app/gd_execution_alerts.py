"""Read executed-hour anomalies, not window-end plans. Never change penalties."""
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib,re

def checked(path,pins):
 p=Path(path);expected=pins.get(str(p))
 if not expected or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('异常结果来源校验失败：'+p.name)
 return json.loads(p.read_text())

def collect(pairs):
 from gd_unit_economics import executed_events
 network=[];targets=[];seen=set()
 for h,export in pairs:
  hp=Path(export['path']);expected=export['sha256']
  if hashlib.sha256(hp.read_bytes()).hexdigest()!=expected:raise ValueError('小时结果发生变化')
  ep=hp.with_name(hp.name.replace('HOUR','EXECUTION_H'))
  e=checked(ep,h['source_pins']);executed_events(h,e)
  origin=datetime.fromisoformat(e['origin'])
  for key,value in e['values'].items():
   m=re.fullmatch(r'(line_over|line_under|section_over|section_under)\[(.+),(\d+)\]',key)
   if not m or float(value)<=1e-6:continue
   kind,g,t=m.groups();t=int(t)
   if t not in e['cells']:continue
   signature=(kind,g,t)
   if signature in seen:raise ValueError('重复已执行越限记录')
   seen.add(signature);start=origin+timedelta(minutes=15*t)
   network.append({'对象':g,'类型':'线路'if kind.startswith('line')else'断面','开始':start.isoformat(),'结束':(start+timedelta(minutes=15)).isoformat(),'方向':'上限'if kind.endswith('over')else'下限','越限（MW）':float(value)})
  if 95 in e['cells']:
   paths=[Path(p)for p in e['source_pins']if p.endswith('/W95/prepared/RT_SOFT_TERMINAL.json')]
   if len(paths)!=1:raise ValueError('缺少日末软目标来源，不能推定为达标')
   report=checked(paths[0],e['source_pins'])
   for g,z in report['resources'].items():
    actual=float(e['values'][f'battery.energy[{g},95]']);target=float(z['target_mwh']);lo=float(z['lower_reachable_target_mwh']);hi=float(z['upper_reachable_target_mwh'])
    targets.append({'机组':g,'时刻':z['target_at'],'日末目标（MWh）':target,'实际日末电量（MWh）':actual,'目标差值（MWh）':actual-target,'允许下界（MWh）':lo,'允许上界（MWh）':hi,'低于软下界（MWh）':max(0.,lo-actual),'高于软上界（MWh）':max(0.,actual-hi)})
 return {'network':network,'targets':targets,'has_target_violation':any(r['低于软下界（MWh）']>1e-6 or r['高于软上界（MWh）']>1e-6 for r in targets)}

def render(pairs):
 import streamlit as st
 data=collect(pairs)
 st.subheader('已执行结果异常')
 if data['network']:
  times=len({(r['开始'],r['结束']) for r in data['network']})
  st.warning(f"线路／断面越限涉及 {times} 个已执行时段，最大 {max(r['越限（MW）'] for r in data['network']):.2f} MW。已计入优化罚值，不能视为安全运行。")
  st.dataframe(data['network'],hide_index=True)
 else:st.caption('已完成小时的执行结果中，未发现线路或断面越限。')
 if data['targets']:
  if data['has_target_violation']:st.warning('储能日末电量未全部落在软目标范围内。下表区分“相对申报目标差值”与“超出容差后的缺额”，两者不能混用。')
  st.dataframe(data['targets'],hide_index=True)
  st.caption('软目标罚值只用于优化，不计为实际发电成本。求解完成不表示日末目标满足；未改物理电量上下限。')
 else:st.caption('尚无完整24:00执行结果，暂不判断日末目标是否满足。')
 return data
