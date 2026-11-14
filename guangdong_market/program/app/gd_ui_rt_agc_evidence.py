"""Read-only presentation contracts. Never executes solver or imports run code."""
from pathlib import Path
from datetime import datetime,timedelta
import hashlib,json

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(x,msg):
 if not x:raise ValueError(msg)
def refs(z):
 if isinstance(z,dict):
  if set(('path','sha256')).issubset(z):need(Path(z['path']).is_file()and sha(z['path'])==z['sha256'],'文件缺失或哈希变化: '+z['path'])
  else:
   for v in z.values():refs(v)
 elif isinstance(z,list):
  for v in z:refs(v)
def pins(d):
 for p,h in d.items():need(Path(p).is_file()and sha(p)==h,'源文件/证据变化: '+p)

def rt_status(request_path,acceptance_path=None,receipt_path=None):
 r=read(request_path);need(r['schema']=='gd.research.generic_rt_recommit.v1','不是新的通用实时重新组合请求')
 need(r['mode']=='uniform_event_research','不支持的研究模式');s=r['state'];need(s['run_id']==r['run_id']and s['case_sha256']==r['case_sha256'],'初态跨 run/场景')
 need(s['executed_end_tick']==r['cutoff_tick']and s['issued_end_tick']==r['delivery_tick']==r['cutoff_tick']+3,'下发/执行边界错误')
 out=dict(kind='RT-SCUC→SCED',run_id=r['run_id'],window=r['window'],cutoff=r['cutoff'],status='待实际求解或验收',accepted_pair=False,executed_cells=s['executed_end_tick'],issued_cells=s['issued_end_tick'],frontier_basis='请求中的研究边界声明，非新增执行证明',notices=sum(v==1 for d in s['notice_values'].values()for v in d.values()),formal_complete=False)
 if acceptance_path:
  a=read(acceptance_path);need(a['schema']=='gd.research.generic_rt.accepted_pair.v1','错误验收合同');need(a['run_id']==r['run_id']and a['window']==r['window']and a['request_sha256']==digest(r),'验收跨 run/窗口/请求');pins(a['pins'])
  need(a['pins'].get(str(Path(request_path).resolve()))==sha(request_path),'请求未被验收绑定')
  for key in ('request_path','names_path','SCED_response_path'):need(str(Path(a[key]).resolve())in a['pins'],'验收路径未封存')
  need(a['SCUC_primal_integer_gap_passed']is True and a['SCED_full_KKT_passed']is True,'数学验收未通过')
  need(sha(a['SCED_response_path'])==a['SCED_response_sha256'],'SCED原始解不符')
  ed=read(a['SCED_response_path']);need(ed.get('backend')=='highs'and ed.get('native_status')=='Optimal'and ed.get('has_primal')is True,'SCED无有效原始解')
  need(any(p.endswith('response.json')and h==a['SCUC_response_sha256']for p,h in a['pins'].items()),'缺SCUC原始解绑定')
  out.update(status='实际 SCUC/SCED 数学验收记录及证据哈希匹配；非调度安全通过',accepted_pair=True,SCUC_audit=a['SCUC_audit'],SCED_audit=a['SCED_audit'])
  if receipt_path:
   e=read(receipt_path);need(e['run_id']==r['run_id']and e['acceptance_sha256']==sha(acceptance_path)and e['previous_receipt_sha256']==s['receipt_sha256']and e['accepted_SCED_response_sha256']==a['SCED_response_sha256'],'回执链跨 run 或解不符')
   need(e['executed_end_tick']==r['cutoff_tick']+3 and e['issued_end_tick']==r['delivery_tick']+3,'回执前沿错误')
   need(e['all_due_notices_issued']is True and e['tracking_deviation_mw']==0 and e['basis']=='simulated_execution_not_metered','非支持的模拟执行回执')
   need(datetime.fromisoformat(e['at'])==datetime.fromisoformat(r['origin'])+timedelta(minutes=5*e['executed_end_tick']),'回执时钟错误')
   out.update(executed_cells=e['executed_end_tick'],issued_cells=e['issued_end_tick'],frontier_basis='独立模拟执行回执已绑定；不是实测计量',receipt_verified=True)
 elif receipt_path:raise ValueError('不能在缺少对应验收时接受执行回执')
 out['issued_not_executed_cells']=out['issued_cells']-out['executed_cells'];return out

def agc_status(snapshot_path):
 z=read(snapshot_path);need(z['schema']=='gd.agc.joint.loop.v1','不是新调频安全闭环快照');c=z['context']
 need(c['identity']==digest({k:v for k,v in c.items()if k!='identity'}),'上下文变化');refs(c['parent_refs'])
 need(datetime.fromisoformat(c['source_available_at'])<=datetime.fromisoformat(c['as_of'])<=datetime.fromisoformat(c['hour_start'])-timedelta(minutes=30),'调频输入晚于清算截止')
 need(z['seal']==digest({k:z[k]for k in ('context','rounds','excluded','status','max_rounds')}),'闭环快照封存不符')
 need(z['payable']is False and z['formal_market_complete']is False,'研究快照不得声明正式完成/付款')
 rows=[]
 for i,r in enumerate(z['rounds']):
  b=r['binding'];need(b['context_identity']==c['identity']and b['parent_run_id']==c['parent_run_id']and b['iteration']==i and b['hour_start']==c['hour_start']and b['zone']==c['zone'],'轮次跨 run/小时/区域');need(b['parent_iteration_sha256']==(digest(z['rounds'][i-1])if i else None),'轮次链错误');pins(r['source_pins'])
  need(r['input_sha256']==digest(r['input']),'声明变化');q=r['clearing'];pr=r.get('physical_request')
  if pr:need(pr['binding']==b and pr['clearing_result_sha256']==digest(q),'物理请求/出清错配')
  evidence=r.get('physical_evidence');
  if evidence:
   need(evidence['binding']==b,'核验跨候选');refs(evidence['source_refs'])
  rows.append(dict(iteration=i,awards_MW=sum(q['awards_mw'].values()),shortage_MW=q['unmet_mw'],excluded=', '.join(r['excluded']),numerical_evidence_present=bool(evidence),full_hour_AC_covered=bool(evidence and evidence['full_hour_AC_covered']),unit_failures=list(evidence['unit_failures'])if evidence else [],global_unsafe_count=len(evidence['global_unsafe'])if evidence else None,global_pending_reasons=sorted({str(x.get('reason',x.get('cause','unknown')))for x in evidence['global_unsafe']})if evidence else []))
 return dict(kind='AGC安全闭环',run_id=c['parent_run_id'],hour=c['hour_start'],zone=c['zone'],status=z['status'],rows=rows,awards=[dict(iteration=i,unit=u,award_MW=v)for i,r in enumerate(z['rounds'])for u,v in r['clearing']['awards_mw'].items()],as_of=c['as_of'],source_available_at=c.get('source_available_at'),fixed_issued_frontier=c.get('fixed_issued_frontier'),excluded=z['excluded'],payable=False,formal_complete=False,scope='已封存研究流程状态；不重新执行数学/AC验收，不授予执行权')


def synthetic_demo_status(path):
 z=read(path);need(z['schema']=='gd.agc.synthetic.health_loop.result.v1','仅接受独立纯假设演示合同')
 need(z['hypothesis_only']is True and z['actual_health_evidence']is False and z['actual_SCED_AC_conclusions_changed']is False,'假设演示不得冒充实际健康证据')
 need(z['native_calls']==z['AC_calls']==0 and z['payable']is False and z['formal_market_complete']is False,'假设演示不得冒充真实求解/付款')
 need(z['original_target_mw']==50 and z['available_awarded_mw']+z['shortage_mw']==50,'原目标及缺额算术不符')
 return z
