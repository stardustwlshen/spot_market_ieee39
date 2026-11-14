"""Pending data-only continuation card. No live status/acceptance or recovery API."""
from pathlib import Path
import json,hashlib
TABLES=('PHYSICAL_CELLS.json','QUARTER_PRICES.json','HOURS.json','ACCOUNT_ROWS.json')
def load(root,index_sha):
 root=Path(root).resolve()
 def readfile(name,h):
  p=(root/name).resolve()
  if not p.is_relative_to(root):raise ValueError('snapshot path escapes bundle')
  data=p.read_bytes()
  if hashlib.sha256(data).hexdigest()!=h:raise ValueError('snapshot content changed: '+name)
  return json.loads(data)
 index=readfile('INDEX.json',index_sha)
 if index['schema']!='gd.explicit_continuation.readonly.v1':raise ValueError('wrong result kind')
 if index['source_kind']!='same_logical_branch_explicit_continuation':raise ValueError('not explicit continuation')
 if index['original_ui_worker_succeeded'] is not False:raise ValueError('original UI failure must be retained')
 if index['safety_release'] is not False or index['formal_bill'] is not None or index['live_restore_allowed'] is not False:raise ValueError('display cannot grant authority')
 required=('FINAL_REVIEW.json','COMPLETE.json','SELECTED_SOURCES.json',*TABLES)
 if not all(k in index['files'] for k in required):raise ValueError('final review/completion/selected paths missing')
 tables={name:readfile(name,index['files'][name]) for name in required}
 # FINAL_REVIEW and COMPLETE are pinned original reports, not reconstructed UI EXITs.
 # Their producer-specific completion schema must be checked by final assembler.
 if index['final_report_binding']['review_sha256']!=index['files']['FINAL_REVIEW.json'] or index['final_report_binding']['complete_sha256']!=index['files']['COMPLETE.json']:raise ValueError('final report binding mismatch')
 return index,tables

from gd_price_quality import render_many as render_price_qualities
def render(root,index_sha):
 import streamlit as st
 st.caption('历史示例保持原版本与原来源；不是v19新运行。未附价格质量字段不能默认为零。')
 from gd_partial_day_panel import rows,render_physical_charts,render_price_chart
 index,tables=load(root,index_sha)
 st.warning('固定研究快照，不随当前场景/报价编辑自动重算。结果来自同来源显式接续，不是原UI worker一次成功；不恢复live权限，不是正式账单或安全放行。')
 st.caption('逻辑原分支：'+index['logical_branch']+'；实际接续结果位置（追溯）：'+index['physical_result_location'])
 st.caption('源case '+index['source_case_sha256'][:16]+'；DA '+index['source_DA_artifacts_sha256'][:16])
 st.json(index['final_review_summary'])
 tabs=st.tabs(['5分钟执行/功率/SOC/AGC','15分钟价格','小时研究核算','账户电能量分项','接续与最终审查来源'])
 for tab,name in zip(tabs,TABLES):
  with tab:
   data=rows(tables[name])
   if data is None:st.json(tables[name]);continue
   if name=='PHYSICAL_CELLS.json':render_physical_charts(data)
   if name=='QUARTER_PRICES.json':
    render_price_chart(data)
    render_price_qualities(data)
    from gd_price_quality import render_audit_supplement
    render_audit_supplement(index,tables['COMPLETE.json'])
   st.dataframe(data,hide_index=True)
 with tabs[-1]:
  for name in ('FINAL_REVIEW.json','COMPLETE.json','SELECTED_SOURCES.json'):st.write(name);st.json(tables[name])
 st.caption('快照哈希仅核对打包内容完整性；本页面没有重跑数学、求解、AC或KKT。缺失价格/小时不补零。账户分项净合计不是市场总额、利润或正式净账单。')
