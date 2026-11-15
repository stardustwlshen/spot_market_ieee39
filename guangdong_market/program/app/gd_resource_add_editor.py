"""Optional template additions: declarations only, applied on a fresh simulation."""
from pathlib import Path
from copy import deepcopy
import json
LABELS={'coal':'煤机','gas':'燃机','battery':'储能','pumped':'抽蓄'}

def read_source(source):
 from gd_provincial_inputs import codec
 loads,_=codec();p=Path(source)/'seed'
 return loads((p/'case.json').read_bytes()),json.loads((p/'agc.json').read_text()),json.loads((p/'request.json').read_text())

def validate_additions(source,additions):
 from gd_resource_add import add
 c,a,r=read_source(source)
 for spec in additions:c,a,r=add(c,a,r,**spec)
 return c

def templates(case,kind):
 from gd_resource_registry import thermal_types
 if kind in ('coal','gas'):return sorted(g for g,t in thermal_types(case).items() if t==kind)
 return sorted(case['batteries'] if kind=='battery' else case['battery_contract']['storage']['pumped_storage']['inventory'])

def render_resource_add_editor(source,source_token):
 import streamlit as st
 key='resource_additions_'+source_token
 if key not in st.session_state:st.session_state[key]=[]
 case,_,_=read_source(source)
 with st.expander('新增机组（高级，可选）'):
  st.caption('按原始场景中的机组模板建立研究机组，继承原模板参数与报价，不继承本页对原机组另行保存的修改。风电、光伏仍按节点聚合，暂不支持独立新增。')
  kind=st.selectbox('新增类型',list(LABELS),format_func=LABELS.get,key='add_kind_'+source_token)
  available=templates(case,kind)
  if not available:st.warning('没有可用模板，不能新增此类型。');return deepcopy(st.session_state[key])
  with st.form('add_resource_'+kind+'_'+source_token):
   template=st.selectbox('参数与报价模板',available,key='add_template_'+kind+'_'+source_token)
   resource_id=st.text_input('新机组唯一编号',key='add_id_'+kind+'_'+source_token,help='字母开头，只含字母、数字、下划线，最多31字符')
   bus=st.selectbox('接入节点',sorted(map(str,case['energy_inputs']['buses']),key=int),key='add_bus_'+kind+'_'+source_token)
   if kind in ('coal','gas'):st.caption('新增火电初始停机、已停24小时；调频资格未申报，调频中标容量默认为0，不自动赋予资格。')
   if st.form_submit_button('保存新增机组'):
    spec=dict(kind=kind,template=template,resource_id=resource_id.strip(),bus=bus)
    try:validate_additions(source,st.session_state[key]+[spec])
    except (ValueError,KeyError)as exc:st.error(str(exc))
    else:st.session_state[key].append(spec);st.success('已保存，重新计算日前和实时后生效。')
  if st.session_state[key]:
   st.dataframe([{'类型':LABELS[z['kind']],'编号':z['resource_id'],'节点':z['bus'],'模板':z['template']}for z in st.session_state[key]],hide_index=True)
   if st.button('撤销最后一台待新增机组',key='undo_add_'+source_token):st.session_state[key].pop();st.rerun()
 return deepcopy(st.session_state[key])
