"""Explicit editable research economics, bound to exact displayed ledger; zero solves."""
from pathlib import Path
from decimal import Decimal,localcontext
import json,hashlib,csv,io
from datetime import datetime
from gd_unit_economics import summarize,executed_events,number
FIELDS=('generation_CNY_MWh','executed_startup_cost_CNY','agc_CNY','reserve_CNY')

def scope(view,start=None):
 rows=[r for r in view.get('settlement_hour_rows',[]) if start is None or r['start']==start]
 if not rows:raise ValueError('没有已执行小时账目')
 # Validate before aggregation: otherwise a duplicate input row silently becomes
 # twice the energy/revenue while summarize() only sees the merged account.
 seen=set();intervals={};technologies={}
 for row in rows:
  account=(row['resource'],str(row['node']),row['direction'])
  if row['direction'] not in ('charge','discharge'):raise ValueError('未知账目方向')
  begin,end=map(datetime.fromisoformat,(row['start'],row['end']))
  if begin.utcoffset() is None or end.utcoffset() is None or end<=begin:raise ValueError('账目时段必须带时区且结束晚于开始')
  key=account+(begin,end)
  if key in seen:raise ValueError('重复的机组/节点/方向/时段账目')
  seen.add(key);intervals.setdefault(account,[]).append((begin,end))
  technology=row.get('technology');previous=technologies.setdefault(row['resource'],technology)
  if previous!=technology:raise ValueError('同一机组技术类型不一致')
 for periods in intervals.values():
  ordered=sorted(periods)
  if any(current[0]<previous[1] for previous,current in zip(ordered,ordered[1:])):raise ValueError('同一账户账目时段重叠')
 identity={'run_id':view['run_id'],'periods':sorted({(r['start'],r['end'])for r in rows}),'rows':rows}
 digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 return identity,digest

def defaults(view,start=None):
 selected,digest=scope(view,start);resources=sorted({r['resource']for r in selected['rows']})
 from gd_default_costs import defaults_for
 return {'schema':'gd.explicit.economics.v1','run_id':view['run_id'],'ledger_digest':digest,'periods':selected['periods'],'units':{r:{**{k:None for k in FIELDS},'source':'',**defaults_for(view,selected,r)}for r in resources}}

def validate(view,record,start=None):
 expected=defaults(view,start)
 for key in ('schema','run_id','ledger_digest'):
  if record.get(key)!=expected[key]:raise ValueError('输入不属于所选运行/账目范围')
 if record.get('periods')!=expected['periods'] and record.get('periods')!=[list(p)for p in expected['periods']]:raise ValueError('时段范围已改变')
 if set(record.get('units',{}))!=set(expected['units']):raise ValueError('机组清单不匹配')
 for resource,u in record['units'].items():
  for field in FIELDS:
   if u.get(field)is None:continue
   value=number(u[field])
   if field in FIELDS[:2] and value<0:raise ValueError('成本必须非负')
   if not u.get('source','').strip():raise ValueError(resource+'：填写数值时需注明来源')
 return record

def save(view,record,directory,start=None):
 validate(view,record,start);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
 target=directory/(record['ledger_digest']+'.json');temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False));temporary.replace(target);return target

def load(view,directory,start=None):
 base=defaults(view,start);p=Path(directory)/(base['ledger_digest']+'.json')
 return validate(view,json.loads(p.read_text()),start)if p.is_file()else base

def upload_csv(view,raw,start=None,base=None):
 result=json.loads(json.dumps(validate(view,base,start))) if base is not None else defaults(view,start);seen=set()
 for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
  unit=r.get('resource')
  if unit not in result['units'] or unit in seen:raise ValueError('CSV机组未知或重复')
  seen.add(unit);entry={'source':r.get('source','')}
  for k in FIELDS:entry[k]=None if not r.get(k,'').strip() else str(number(r[k]))
  result['units'][unit]=entry
 return validate(view,result,start)

def proven_zero_startup(view,start=None):
 """Only actual complete executed start AND stop flags can establish zero cost events."""
 selected,_=scope(view,start);periods=selected['periods'];by_period={};errors=[]
 for summary in view.get('execution_event_summaries',[]):
  period=(summary.get('start'),summary.get('end'))
  if period not in periods:continue
  if summary.get('run_id')!=view['run_id'] or view.get('source_manifest',{}).get(summary.get('source_path'))!=summary.get('source_sha256'):
   errors.append('执行事件摘要来源不匹配');continue
  if period in by_period:raise ValueError('重复的执行时段事件摘要')
  by_period[period]=summary
 for path,digest in view.get('source_manifest',{}).items():
  p=Path(path)
  if not p.name.startswith('EXECUTION_H') or p.suffix!='.json':continue
  try:
   raw=p.read_bytes()
   if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('执行来源摘要变化')
   e=json.loads(raw)
   for period in periods:
    if period in by_period:continue
    rows=[r for r in selected['rows'] if (r['start'],r['end'])==tuple(period)]
    try:z=executed_events({'run_id':view['run_id'],'rows':rows},e)
    except ValueError:continue
    by_period[tuple(period)]=z;break
  except (OSError,ValueError,KeyError)as exc:errors.append(str(exc))
 zeros=[]
 if len(by_period)==len(periods):
  resources={r['resource']for r in selected['rows']}
  for resource in resources:
   records=[next((r for r in z['rows']if r['resource']==resource),{})for z in by_period.values()]
   if all(r.get('start_count')==0 and r.get('stop_count')==0 and not r.get('missing') for r in records):zeros.append(resource)
 return {'resources':sorted(zeros),'covered_hours':len(by_period),'required_hours':len(periods),'errors':errors,'meaning':'完整已执行start/stop均0；不是报价成本推断'}

def calculate(view,record,start=None,zero_evidence=None,agc_mileage_ratio=10):
 validate(view,record,start);selected,_=scope(view,start);grouped={}
 with localcontext()as ctx:
  ctx.prec=80
  for r in selected['rows']:
   key=(r['resource'],r['node'],r['direction']);g=grouped.setdefault(key,dict(r,energy_amount_CNY=Decimal(0),simulated_executed_MWh=Decimal(0)))
   g['energy_amount_CNY']+=number(r['energy_amount_CNY']);g['simulated_executed_MWh']+=number(r['simulated_executed_MWh'])
  beginning=min(r['start']for r in selected['rows']);ending=max(r['end']for r in selected['rows'])
  rows=[dict(r,start=beginning,end=ending)for r in grouped.values()]
  inputs=dict(run_id=view['run_id'],start=beginning,end=ending,source='explicit persisted research inputs',costs={},services={})
  zeros=(zero_evidence or {}).get('resources',[])
  for resource,u in record['units'].items():
   startup=u.get('executed_startup_cost_CNY');source=u.get('source','')
   if startup is None and resource in zeros:startup=0;source+='; same-run complete executed event evidence'
   cost=dict(source=source)
   if u.get('generation_CNY_MWh')is not None:cost['generation_CNY_MWh']=u['generation_CNY_MWh']
   if startup is not None:cost['executed_startup_cost_CNY']=startup
   inputs['costs'][resource]=cost;inputs['services'][resource]={}
   for field,service in [('agc_CNY','agc'),('reserve_CNY','reserve')]:
    if u.get(field)is not None:inputs['services'][resource][service]=dict(amount_CNY=u[field],source=u['source'])
  # Auto-use a service only when every selected hour has an exact bound amount.
  for resource in record['units']:
   for service in ('agc','reserve'):
    if service in inputs['services'][resource]:continue
    amounts={}
    for item in view.get('bound_service_rows',[]):
     period=(item.get('start'),item.get('end'))
     if item.get('resource')!=resource or item.get('service')!=service or period not in selected['periods']:continue
     if item.get('run_id')!=view['run_id'] or view.get('source_manifest',{}).get(item.get('source_path'))!=item.get('source_sha256'):continue
     if period in amounts:raise ValueError('重复的辅助服务小时金额')
     amounts[period]=number(item['amount_CNY'])
    if len(amounts)==len(selected['periods']):inputs['services'][resource][service]=dict(amount_CNY=sum(amounts.values(),Decimal(0)),source='same-run same-hour bound service results')
  result=summarize({'run_id':view['run_id'],'rows':rows},inputs)
 from gd_service_estimates import estimate
 estimates=estimate(view,selected['periods'],record['units'],agc_mileage_ratio)
 for row in result['rows']:
  est=estimates.get(row['resource']);row['agc_estimate']=est
  row['estimated_energy_plus_agc_margin_CNY']=None if est is None or row['energy_contribution_CNY'] is None else row['energy_contribution_CNY']+number(est['amount_CNY'])
 result['ledger_digest']=record['ledger_digest'];result['covered_periods']=selected['periods'];result['input_grade']='用户明示研究输入；非正式结算';return result
