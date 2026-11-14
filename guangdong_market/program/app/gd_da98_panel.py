"""Same selected accepted DA, read-only publication-candidate checks. Zero solves."""
from pathlib import Path
import hashlib,json
from gd_da98_runtime import da98_candidate as candidate
APP=Path(__file__).resolve().parent
CONTRACT=APP.parent/'engines/guangdong_v3/guangdong_pipeline_20261112/da_math/smm_extend/gd_r14_da_contract.py'
CONTRACT_SHA='92006bc8cd93fd66d27be116cb70c9c3f07d84484fae4479f8b4cfe081bb49f2'
def identity(run):
 p=Path(run).resolve();return str(p),candidate.sha(p/'ARTIFACTS.json')
def preview(run):
 candidate.need(candidate.sha(CONTRACT)==CONTRACT_SHA,'installed DA98 contract changed')
 z=candidate.export_candidate(run,candidate.load_contract_module(CONTRACT));z['integrated_into_current_UI']=True
 return z

def save(run,path,result,*,confirmed=False):
 candidate.need(confirmed is True,'explicit save confirmation required');p=Path(path).resolve();r=Path(run).resolve()
 candidate.need(not p.is_relative_to(r),'candidate must be outside accepted DA run');candidate.need(not p.exists(),'new candidate file only')
 # Recheck accepted same-run source chain. Generated-at is not source identity.
 fresh=preview(run);old={k:v for k,v in result.items()if k!='generated_at_utc'};new={k:v for k,v in fresh.items()if k!='generated_at_utc'}
 candidate.need(old==new,'candidate source or reviewed content changed')
 with p.open('x')as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
 return dict(path=str(p),sha256=candidate.sha(p),publication_allowed=False,native_calls=0)

def render(status):
 import streamlit as st
 if not status or not status.get('accepted'):return
 run=status['run']
 with st.expander('同来源DA98映射/日历审查及研究发布候选（0求解，不正式发布）'):
  st.caption('数量来自本DA已接受SCED物理解，不取PRICE变量；价格来自同run独立DA PRICE原始未限价节点价。96点/24小时为研究候选，98点正式等价等未决项保留。')
  try:current=identity(run)
  except Exception as exc:st.error(str(exc));return
  if st.session_state.get('da98_source_identity')!=current:
   for key in ('da98_result','da98_save_confirm','da98_saved'):st.session_state.pop(key,None)
   st.session_state['da98_source_identity']=current
  if st.button('读取本DA候选与缺项（0求解）',key='da98_preview'):
   st.session_state.pop('da98_result',None);st.session_state['da98_save_confirm']=False
   try:st.session_state['da98_result']=preview(run)
   except Exception as exc:st.error(str(exc))
  z=st.session_state.get('da98_result')
  if not z:return
  st.warning('研究候选，正式发布始终关闭。模型逻辑发布时间不是实际申报/出清按时证据；缺失事件不补造。')
  st.caption('源run '+z['run_id']+'；DA artifact '+z['source_pins']['ARTIFACTS.json'][:16])
  cal,quarters,hours,gaps=st.tabs(['官方日历与缺失事件','96点数量/独立价格','24小时汇总','98映射与未决条件'])
  with cal:st.json(z['calendar']);st.write('模拟时间（非实际事件）：'+z['logical_simulated_publication_at'])
  with quarters:st.dataframe(z['quarters'],hide_index=True)
  with hours:st.dataframe(z['hours'],hide_index=True)
  with gaps:st.json(z['mapping']);st.json(z['unsupported'])
  path=st.text_input('另存候选新文件（必须在已接受DA目录外）',str(Path(run).parent/(Path(run).name+'_DA98_RESEARCH_CANDIDATE.json')),key='da98_save_path')
  save_id=(current,path,hashlib.sha256(candidate.canonical(z)).hexdigest())
  if st.session_state.get('da98_save_identity')!=save_id:
   st.session_state['da98_save_identity']=save_id;st.session_state['da98_save_confirm']=False;st.session_state.pop('da98_saved',None)
  yes=st.checkbox('确认仅另存上述研究候选，不发布、不发送调度、不生成正式账单',key='da98_save_confirm')
  if st.button('另存同来源研究候选（0求解）',key='da98_save',disabled=not yes):
   try:st.session_state['da98_saved']=save(run,path,z,confirmed=True)
   except Exception as exc:st.error(str(exc))
  if st.session_state.get('da98_saved'):st.json(st.session_state['da98_saved'])
