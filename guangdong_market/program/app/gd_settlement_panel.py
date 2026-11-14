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

def render_energy_hour(result,export=None,economics_inputs=None,service_components=None):
 import streamlit as st
 from gd_unit_economics import summarize, from_components, executed_events
 try:
  bound_hour=dict(result)
  if export:bound_hour['_hour_sha256']=export['sha256']
  economics=from_components(bound_hour,**service_components) if service_components is not None else summarize(bound_hour,economics_inputs)
  with st.expander('分机组收入、成本与模拟利润',expanded=True):
   columns={'resource':'机组','energy_revenue_CNY':'电能量收入（元）','agc_revenue_CNY':'调频收入（元）','reserve_revenue_CNY':'备用收入（元）','generation_cost_CNY':'发电成本（元）','executed_startup_cost_CNY':'已执行启停成本（元）','energy_contribution_CNY':'电能量贡献（元）','simulated_spot_profit_CNY':'模拟现货利润（元）'}
   choice=st.selectbox('查看机组',['全部']+[r['resource'] for r in economics['rows']],key='economics_unit_'+result['run_id']+'_'+economics['start'])
   visible=[r for r in economics['rows'] if choice=='全部' or r['resource']==choice]
   st.dataframe([{**{label:str(r[k]) if r[k] is not None else None for k,label in columns.items()},'缺项':'；'.join(r['missing'])} for r in visible],hide_index=True)
   if economics['status']=='partial':st.caption('空白为缺失输入，不是零；仅计发电和实际启停成本，不含运维。')
 except (ValueError,TypeError,KeyError) as exc:st.error('经济性来源检查失败：'+str(exc))
 energy,agc,reserve,other,net=st.tabs(['电能量分项','调频分项','旋转备用分项','其他/合约费用','净账单与数据等级'])
 with energy:
  st.metric(LABEL+'（元）',str(result['total_energy_component_CNY']))
  st.caption('仅对模型列示账户的已计算电能量分项作净汇总，不是市场总额、利润、完整结算或应付账单。')
  labels={'resource':'资源','node':'节点','direction':'账户方向','start':'开始时间','end':'结束时间','QDA_MWh':'日前电量（MWh）','simulated_executed_MWh':'模拟执行电量（MWh）','deviation_MWh':'偏差电量（MWh）','DA_hour_price':'日前小时价（元/MWh）','RT_hour_price':'实时小时研究价（元/MWh）','energy_amount_CNY':'电能量分项金额（元）'}
  shown=[{label:({'charge':'充电','discharge':'放电'}.get(row.get(key),row.get(key))if key=='direction'else row.get(key))for key,label in labels.items()}for row in result['rows']]
  st.dataframe(shown,hide_index=True)
  with st.expander('技术详情：原始分项字段与来源',expanded=False):st.json(result['rows'])
  storage=storage_rows(result['rows'])
  if storage:st.dataframe(storage,hide_index=True)
  st.caption('储能充电、放电均保留原有带符号金额；偏差与负价可令任一方向跨符号。净额仅为已列示两方向之和，不按方向强制改符号。')
 session=None
 if export:
  try:
   from gd_settlement_bridge import from_export
   session=from_export(export['path'],result['run_id'],export['sha256'])
  except Exception as exc:st.error('分项导出来源检查失败：'+str(exc))
 for tab,names in ((agc,('agc',)),(reserve,('reserve',)),(other,('energy','non_da_renewable','operating_compensation'))):
  with tab:
   st.caption('仅研究导出算术，不恢复 live host、不新增数学接受；声明为实测/公开发布亦不代表已核实真实性。未知只阻对应分项。')
   if session is None:st.warning('缺少所选同 run 小时导出，不能计算此分项。');continue
   import json
   from gd_settlement_bridge import components
   st.caption('字段格式：classification/source/run_id/start/end/value；详见 gd_settlement_runtime/INPUT_SCHEMA.md。')
   for name in names:
    key='settlement_'+result['run_id']+'_'+session.period[0]+'_'+name
    text=st.text_area(name+' 独立来源声明（JSON；默认缺项）','{}',key=key)
    try:
     fields=json.loads(text)
     if not isinstance(fields,dict):raise ValueError('字段必须是对象')
     output=components(session,{name:fields})[name]
     st.json(output)
    except Exception as exc:st.error(name+' 输入拒绝：'+str(exc))
 with net:
  st.write({'电能量':'研究模拟分项，不是实测计量','调频/备用/其他':'分别缺项，尚不能组合完整账单','合同未知':'不得当作空合同或零金额','正式净账单':'不可发布','安全/调度授权':False})
  st.caption('文件中自称formal/accepted不构成认证；正式状态只能由固定受信接口及完整来源条件产生。当前不展示正式净额。')
