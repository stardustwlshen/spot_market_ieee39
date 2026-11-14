"""Read-only PRICE quality. No optimization, KKT replay or dispatch authority."""
from pathlib import Path
from datetime import datetime,timedelta
import hashlib,json,math,re

SCHEMA='gd.PRICE.quality.v1'
DISPLAY_TOL_MW=1e-7
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def unknown(reason):
 return dict(schema=SCHEMA,status='unknown',reason=reason,penalty_slacks_provided=False,
  nonzero_rows=None,physical_load_shedding='not_inferred_from_PRICE',safety_release=False,formal_bill=None)
def summarize(result,*,provenance=None):
 slacks=result.get('slacks')
 if not isinstance(slacks,dict) or not slacks:return unknown('未提供完整定价松弛字段；不能视为零。')
 rows=[];types={};context=result.get('context',{});start=context.get('start')
 for name,value in sorted(slacks.items()):
  if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ValueError('nonfinite PRICE slack')
  m=re.fullmatch(r'(PRICE_[^\[]+)\[(.*)\]',name)
  if not m:raise ValueError('unrecognized slack field '+name)
  tail=m[2].split(',');tick=int(tail[-1]);kind=m[1]
  stat=types.setdefault(kind,dict(fields=0,nonzero_fields=0,max_abs_MW=0.0));stat['fields']+=1;stat['max_abs_MW']=max(stat['max_abs_MW'],abs(value))
  if abs(value)>DISPLAY_TOL_MW:
   stat['nonzero_fields']+=1
   row=dict(variable=name,type=kind,local_price_tick=tick,MW=value,is_current_quarter=tick==0)
   if start:
    at=datetime.fromisoformat(start)+timedelta(minutes=15*tick);row.update(start=at.isoformat(),end=(at+timedelta(minutes=15)).isoformat())
   rows.append(row)
 return dict(schema=SCHEMA,status='penalty_slacks_active' if rows else 'no_material_penalty_slacks',penalty_slacks_provided=True,
  native_status=result.get('native_status','not_recorded'),stored_KKT_passed=result.get('KKT',{}).get('passed'),
  nonzero_rows=rows,by_type=types,display_tolerance_MW=DISPLAY_TOL_MW,
  current_quarter_penalty_active=any(r['is_current_quarter'] for r in rows),
  delta=context.get('delta'),delta_semantics='relative band: ref ± delta*abs(ref); delta .1 means ±10%, not ±0.1 MW',
  physical_load_shedding='not_inferred_from_PRICE',AC_safety='not_certified_by_PRICE',
  provenance=provenance or {},safety_release=False,formal_bill=None,
  warning='PRICE惩罚松弛不是实际SCED切负荷。Optimal/KKT仅为所列数学模型结果，不等于无松弛、安全或正式账单。')
def _bound_digest(pins,path):
 """Match strict canonical path identity, never unrelated equal-content files."""
 target=Path(path).resolve()
 values={digest for alias,digest in pins.items()if Path(alias).resolve()==target}
 if len(values)>1:raise ValueError('conflicting accepted PRICE source aliases: '+str(path))
 return next(iter(values))if values else None

def _from_accepted_quality(path):
 """Same-directory, exact accepted context/raw binding; absent evidence stays unknown."""
 f=Path(path);a=read(f);p=f.parent/'prepared';rf=p/'PRICE_RESULT.json'
 if not rf.is_file():return unknown('同来源PRICE_RESULT未提供；历史ACCEPTED缺字段不等于零。')
 r=read(rf)
 if not isinstance(r.get('slacks'),dict) or not r['slacks']:return unknown('同来源结果未提供完整定价松弛字段；不能视为零。')
 if r.get('context')!=a.get('context') or r.get('raw_prices')!=a.get('raw_prices') or r.get('KKT')!=a.get('KKT'):raise ValueError('PRICE result differs from accepted source')
 for rel in ('names.json','native_once/COMPLETED.json','native_once/IDENTITY.json','native_once/response.json'):
  q=p/rel
  expected=_bound_digest(a.get('source_pins',{}),q)
  if expected is None or not q.is_file() or sha(q)!=expected:raise ValueError('accepted PRICE raw binding changed: '+rel)
 n=read(p/'names.json');raw=read(p/'native_once/response.json');done=read(p/'native_once/COMPLETED.json')
 if done['response_sha256']!=sha(p/'native_once/response.json') or done['returncode']!=0 or raw.get('optimize_calls')!=1:raise ValueError('native PRICE identity')
 if len(n['variables'])!=len(raw['primal']) or len(set(n['variables']))!=len(n['variables']):raise ValueError('raw primal dimensions')
 values=dict(zip(n['variables'],raw['primal']))
 expected={k:v for k,v in values.items() if k.startswith('PRICE_')}
 if set(expected)!=set(r.get('slacks',{})) or any(v!=r['slacks'][k] for k,v in expected.items()):raise ValueError('reported slacks not identical to raw primal')
 return summarize(r,provenance=dict(accepted_sha256=sha(f),result_sha256=sha(rf),raw_sha256=sha(p/'native_once/response.json'),run_id=r['context'].get('run_id'),quarter=r['context'].get('day_quarter'),source=str(rf)))
def from_accepted(path):
 from gd_price_timing import from_accepted as timing
 clock=timing(path)
 result=_from_accepted_quality(path)
 result['information_time']=clock
 return result

_TYPE_ZH={'PRICE_load_shortage':'电量不足惩罚松弛','PRICE_load_surplus':'电量过剩惩罚松弛','PRICE_accident_shortfall':'事故备用不足惩罚松弛','PRICE_line_positive':'线路正向越限惩罚松弛','PRICE_line_negative':'线路反向越限惩罚松弛'}
def display_rows(rows):
 return [dict({'松弛类型':_TYPE_ZH.get(r['type'],'其他惩罚松弛'),'价格域内时段':r['local_price_tick'],'松弛量（MW）':r['MW'],'当前季度':'是' if r.get('is_current_quarter') else '否'},**({'季度':r['quarter']} if 'quarter' in r else {}),**({'开始时间':r['start'],'结束时间':r['end']} if 'start' in r else {})) for r in rows]
def render(quality):
 import streamlit as st
 q=quality if isinstance(quality,dict) and quality.get('schema')==SCHEMA else unknown('此历史/当前结果尚未提供价格质量字段；不能默认为零。')
 st.caption('数值状态、定价惩罚松弛、实际物理出力、交流安全与正式账单分别判断。δ=.1为±10%相对带，不是±0.1MW。')
 clock=q.get('information_time')
 if clock and clock.get('status')!='not_declared':
  st.warning(clock['label']+'；不用于正式结算或预出清。')
 if q['status']=='unknown':st.warning(q['reason']);return
 st.write('定价模型数值状态：'+('最优' if q.get('native_status')=='Optimal' else '见技术详情')+'；留存最优性检验：'+('通过' if q.get('stored_KKT_passed') is True else '未提供或未通过'))
 if q['nonzero_rows']:
  st.warning('定价模型存在惩罚松弛。这不表示实际SCED已切负荷，也不能仅凭Optimal认定无缺供物理出清。')
  st.dataframe(display_rows(q['nonzero_rows']),hide_index=True)
  with st.expander('技术详情：原始变量、字段及数值状态',expanded=False):st.json(q)
 else:st.info('已提供字段在显示阈值内未见惩罚松弛；这仍不构成AC安全或正式账单放行。')
 st.caption('本表为当前季度及其价格预测域；未来时段会重叠，不能跨季度直接累加为实际日电量缺口。')

def render_many(rows):
 """Historical cards remain immutable; consolidate absent-field disclosure."""
 missing=[r for r in rows if not r.get('price_quality')]
 if missing:render(unknown(str(len(missing))+'个历史季度未提供价格质量字段；不能默认为零。'))
 for r in rows:
  if r.get('price_quality'):
   import streamlit as st
   st.write('季度 '+str(r.get('quarter',r.get('day_quarter','未提供'))))
   render(r['price_quality'])

def render_audit_supplement(index,complete):
 """Separate v19 read-only supplement, not retroactively added old result fields."""
 import streamlit as st
 from gd_live_control import sources
 pins=sources();p=Path(__file__).resolve().parent/'PRICE_QUALITY_AUDIT.json'
 if str(p) not in pins:raise ValueError('unregistered price audit supplement')
 audit=read(p)
 if audit['complete_sha256']!=index['files']['COMPLETE.json'] or audit['logical_branch']!=complete['logical_branch']:raise ValueError('audit supplement wrong COMPLETE/branch')
 if set(audit['quarters'])!=set(complete['price_paths']):raise ValueError('audit quarter coverage changed')
 for q,path in complete['price_paths'].items():
  if Path(audit['quarters'][q]['provenance']['source'])!=Path(path).parent/'prepared/PRICE_RESULT.json':raise ValueError('audit source branch mismatch')
 s=audit['summary']
 st.warning('独立只读审计补充（不是旧卡原字段、不是v19新运行）：96季度中'+str(len(s['active_current']))+'个当前季度有PRICE惩罚松弛；'+str(len(s['active_any']))+'个季度的含重叠预测域有松弛。')
 st.caption('逐份核对原始PRICE primal及接受来源哈希；预测域重叠不能累加为实际日电量缺口。定价惩罚不代表实际SCED切负荷，不改变原安全阻断或正式账单状态。')
 st.dataframe([{'松弛类型':_TYPE_ZH.get(k,'其他惩罚松弛'),'字段数':v['fields'],'非零字段数':v['nonzero_fields'],'最大绝对值（MW）':v['max_abs_MW']} for k,v in s['by_type'].items()],hide_index=True)
 with st.expander('审计补充：逐季度/类型/价格时段/MW（预测域可重叠）'):
  st.dataframe(display_rows([dict(quarter=q,**row) for q,v in audit['quarters'].items() for row in (v['nonzero_rows'] or [])]),hide_index=True)
 with st.expander('技术详情：原始审计字段与来源',expanded=False):st.json(audit)
