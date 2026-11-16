"""Presentation of existing component amounts, not a settlement formula or authority."""
from decimal import Decimal,localcontext
LABEL='模型列示账户电能量分项净合计（研究）'

def storage_rows(rows):
 groups={}
 for row in rows:
  if row.get('technology')!='independent_storage':continue
  key=(row['resource'],str(row['node']));g=groups.setdefault(key,{'charge':[],'discharge':[]})
  if row['direction']in g:g[row['direction']].append(row['energy_amount_CNY'])
 out=[]
 with localcontext()as context:
  context.prec=80
  for (resource,node),parts in sorted(groups.items()):
   values={side:sum((Decimal(str(v))for v in seq),Decimal(0))if seq and all(v is not None for v in seq)else None for side,seq in parts.items()}
   net=values['charge']+values['discharge']if all(v is not None for v in values.values())else None
   out.append({'储能账户':resource,'节点':node,'充电分项金额（元）':None if values['charge']is None else str(values['charge']),'放电分项金额（元）':None if values['discharge']is None else str(values['discharge']),'两方向净额（元）':None if net is None else str(net),'数据等级':'研究已列示分项'if net is not None else'方向缺项，净额未知'})
 return out

def simulation_rows(rows):
 """Display research income separately from unavailable formal settlement."""
 columns={'resource':'机组','energy_revenue_CNY':'电能量收入（元）','simulated_reserve_gross_revenue_CNY':'行政备用补偿估算（元）','generation_cost_CNY':'发电成本（元）','executed_startup_cost_CNY':'已执行启停成本（元）','estimated_spot_margin_before_service_allocation_CNY':'模拟收益（月度分摊前，元）'}
 def agc(r):
  if 'simulated_agc_gross_revenue_CNY' in r:return r['simulated_agc_gross_revenue_CNY']
  return r.get('agc_revenue_CNY') if r.get('agc_revenue_CNY') is not None else (r.get('agc_estimate') or {}).get('amount_CNY')
 def basis(r):
  key=r.get('agc_revenue_basis') or ('supplied_component' if r.get('agc_revenue_CNY') is not None else 'research_mileage_estimate' if agc(r) is not None else 'missing')
  return {'supplied_component':'已提供的同源计算分项（非正式账单）','research_mileage_estimate':'模拟里程估算','mixed':'混合口径，详见各小时','missing':'缺失，未按零处理'}.get(key,key)
 return [{**{label:r.get(k) for k,label in columns.items()},'调频收入（元）':agc(r),'调频数据来源':basis(r)} for r in rows]

def render_energy_hour(result,export=None,economics_inputs=None,service_components=None):
 import streamlit as st
 from gd_balance_diagnostics import unavailable
 if unavailable(result):
  st.error(result['economic_reason'])
  st.dataframe(result['balance_diagnostics'],hide_index=True)
  st.dataframe(result['dispatch_rows'],hide_index=True)
  with st.expander('保留的调度与价格来源'):st.json({'physical_cells':result['physical_cells'],'hour_prices':result['hour_prices'],'input_binding':result['input_binding']})
  return
 from gd_unit_economics import summarize,from_components
 try:
  bound_hour=dict(result)
  if export:bound_hour['_hour_sha256']=export['sha256']
  if export and service_components is None and economics_inputs is None:
   from gd_settlement_bridge import from_export,components
   economics=components(from_export(export['path'],result['run_id'],export['sha256']))['unit_economics']
  else:economics=from_components(bound_hour,**service_components) if service_components is not None else summarize(bound_hour,economics_inputs)
  st.subheader('分机组收入、成本与模拟收益')
  choice=st.selectbox('查看机组',['全部']+[r['resource'] for r in economics['rows']],key='economics_unit_'+result['run_id']+'_'+economics['start'])
  visible=[r for r in economics['rows'] if choice=='全部' or r['resource']==choice]
  st.dataframe(simulation_rows(visible),hide_index=True)
  st.caption('模拟收益＝电能量收入＋所列调频收入＋行政备用补偿估算－发电及实际启停成本。未扣月度辅助服务分摊，不是正式净利润。')
  with st.expander('估算依据与未核实项目'):
   st.caption('备用补偿池和补偿单价属于可配置研究假设；不等同实际备用交易收入。调频采用模拟里程，尚未核实实际里程及考核扣减。2025年规则来源不代表已认证2026年适用性。')
   st.dataframe([{'机组':r['resource'],'正式结算缺项':'；'.join(r.get('missing',[]))} for r in visible],hide_index=True)
   if economics.get('reserve_estimate'):st.json(economics['reserve_estimate'])
 except (ValueError,TypeError,KeyError) as exc:st.error('经济性来源检查失败：'+str(exc))
 with st.expander('电能量分项明细'):
  labels={'resource':'机组','node':'节点','direction':'方向','start':'开始','end':'结束','QDA_MWh':'日前电量（MWh）','simulated_executed_MWh':'实时电量（MWh）','deviation_MWh':'偏差电量（MWh）','DA_hour_price':'日前价格（元/MWh）','RT_hour_price':'实时价格（元/MWh）','energy_amount_CNY':'电能量收入（元）'}
  st.dataframe([{label:({'charge':'充电','discharge':'放电'}.get(r.get(k),r.get(k)) if k=='direction' else r.get(k)) for k,label in labels.items()}for r in result['rows']],hide_index=True)
