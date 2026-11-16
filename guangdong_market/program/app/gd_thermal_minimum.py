"""Explicit linear-template research reparameterization, never infer unknown curves."""
from copy import deepcopy
import math

def resize_minimum(d,curve,no_load,new_min,new_max=None):
 old=d['min_mw'][0];maximum=d['max_mw'][0];target_max=maximum if new_max is None else new_max
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 need(math.isfinite(new_min) and 0<new_min<target_max,'最小出力须大于0且小于最大出力')
 need(old>0 and len(set(d['min_mw'].values()))==1 and len(set(d['max_mw'].values()))==1,'分时容量/最小出力暂不支持自动重建')
 need(d['bid_base_mw']==old,'报价基段起点与最小出力不一致')
 need(len(set(d['base_bid_yuan_per_h'].values()))==1,'分时基段报价暂不支持重建')
 need(curve.get('basis')=='research','非研究启停轨迹不能自动重建')
 rate=d['base_bid_yuan_per_h'][0]/old
 need(math.isfinite(rate) and math.isfinite(no_load) and no_load>=0,'基段单价或空载费无效')
 widths=[s['width_mw']for s in d['segments']]
 need(widths and all(math.isfinite(x)and x>0 for x in widths) and math.isclose(sum(widths),maximum-old,abs_tol=1e-7,rel_tol=1e-9),'原分段报价未完整覆盖可调区间')
 out=deepcopy(curve);scale=new_min/old
 for kind,sign in [('startup',1),('shutdown',-1)]:
  rows=out[kind];need(bool(rows),'启停轨迹为空')
  for i,row in enumerate(rows):
   begin=old*(i/len(rows) if sign==1 else 1-i/len(rows));end=begin+sign*old/len(rows)
   need(all(math.isclose(row[k],v,abs_tol=1e-7,rel_tol=1e-9)for k,v in [('begin',begin),('end',end),('mean',(begin+end)/2)]),'非线性或未知启停轨迹暂不支持重建')
   need(math.isclose(row['cost'],no_load+rate*row['mean'],abs_tol=1e-6,rel_tol=1e-9),'启停成本不符合原基段单价加空载费模板，不能推断')
   for k in ('begin','mean','end'):row[k]*=scale
   row['cost']=no_load+rate*row['mean']
   # Source seed is 5min; reject any unverified interval width instead of guessing.
   horizon=d.get('_minimum_edit_dt_h')
   need(horizon is not None and horizon>0,'缺少原始轨迹时间步长')
   ramp_key='ramp_up_mw_per_h' if sign==1 else 'ramp_down_mw_per_h'
   need(abs(row['end']-row['begin'])/horizon<=min(d[ramp_key].values())+1e-7,'调整后启停轨迹斜率超过声明爬坡能力')
 for key in ('startup_output_limit_mw','shutdown_output_limit_mw'):
  need(all(math.isclose(x,old,abs_tol=1e-9) for x in d[key].values()),'特殊启停出力限制暂不支持自动重建')
  d[key]={t:new_min for t in d[key]}
 d['max_mw']={t:target_max for t in d['max_mw']}
 d['min_mw']={t:new_min for t in d['min_mw']};d['bid_base_mw']=new_min
 d['base_bid_yuan_per_h']={t:rate*new_min for t in d['base_bid_yuan_per_h']}
 for seg in d['segments']:seg['width_mw']*=(target_max-new_min)/(maximum-old)
 out['source']+='; user Pmin edit: linear trajectory MW scaled, duration fixed, hourly cost=no-load+original base rate*new mean; research assumption'
 return out
