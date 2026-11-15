"""Compact optional storage declarations editor. No solve until user starts new run."""
from copy import deepcopy
from gd_resource_inputs import parameters, read_case, apply_resource_edits
LABELS={'energy_capacity_mwh':'额定电量（MWh）','energy_min_mwh':'电量下限（MWh）','energy_max_mwh':'电量上限（MWh）','initial_energy_mwh':'初始电量（MWh）','terminal_energy_mwh':'日末目标电量（MWh）','charge_max_mw':'充电功率上限（MW）','discharge_max_mw':'放电功率上限（MW）','vup0':'初始上库水量（万m³）','vup_end_bid':'日末上库目标（万m³）','v_total':'上下库总水量（万m³）','vup_min':'上库水量下限（万m³）','vup_max':'上库水量上限（万m³）','vdn_min':'下库水量下限（万m³）','vdn_max':'下库水量上限（万m³）','unit_gen_min':'单机最小发电功率（MW）','unit_gen_max':'单机最大发电功率（MW）','unit_pump':'单机固定抽水功率（MW）'}
def render_resource_editor(source,source_token):
 import streamlit as st
 key='resource_edits_'+source_token
 if key not in st.session_state:st.session_state[key]={}
 base=parameters(source)
 with st.expander('储能、抽蓄参数（可选）'):
  ids=[(kind,g)for kind,units in base.items()for g in units]
  selected=st.selectbox('选择储能或抽蓄',ids,format_func=lambda v:('储能 'if v[0]=='battery'else'抽蓄 ')+v[1],key='resource_selected_'+source_token)
  kind,g=selected;values={**base[kind][g],**st.session_state[key].get(kind,{}).get(g,{})}
  with st.form('resource_form_'+source_token+g):
   changes={k:st.number_input(LABELS[k],min_value=0.,value=float(v),key='res_'+source_token+g+k)for k,v in values.items()}
   if st.form_submit_button('保存参数'):
    edits=deepcopy(st.session_state[key]);edits.setdefault(kind,{})[g]=changes
    try:apply_resource_edits(read_case(source),edits)
    except ValueError as exc:st.error(str(exc))
    else:st.session_state[key]=edits;st.success('已保存；重新计算日前和实时后生效。')
  if kind=='battery':st.caption('SOC＝电量÷额定电量。修改功率时同步缩放报价段宽，价格和爬坡能力不变。')
  else:st.caption('初始机组停机；下库初态＝总水量−上库初态。水量单位为万m³。')
 return deepcopy(st.session_state[key])
