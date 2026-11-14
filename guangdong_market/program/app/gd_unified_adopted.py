"""Read-only pinned compatibility report, never a new native acceptance."""
from pathlib import Path
import json,hashlib
EXPECTED = {'ADOPTED_EVIDENCE.json': '04d4277bc8b9ada5d2f715f42dba3b6a11399e4dd547677ddd8b031fe7be8956', 'COMPATIBILITY.json': 'c478c859dc966e6de315ea31c57d98f1a0a0bf63d6b1cabec8d02ef4e65b19e8'}
def read_adopted(root=None):
 root=Path(root)if root else Path(__file__).resolve().parents[1]/'verified_evidence/unified_v3'
 data={}
 for name,h in EXPECTED.items():
  p=root/name
  if not p.is_file()or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('兼容报告缺失或改变，拒绝显示沿用证据')
  data[name]=json.loads(p.read_bytes())
 a=data['ADOPTED_EVIDENCE.json'];c=data['COMPATIBILITY.json']
 assert a['status']=='compatible_adopted_original_v2_evidence' and a['new_native_calls']==c['new_native_calls']==0
 assert c['compatible'] and a['do_not_import_v2_context_as_v3'] and a['v2_source_unchanged']
 assert a['compatibility_sha256']==EXPECTED['COMPATIBILITY.json'] and a['source_manifest_sha256']=='0a2e2405e4ac5d07fe3b27cbb978fa6ab98d80d1b95d1428f305b7bb467ca7c4'
 old=a['original_result'];assert sum(x['native_optimize_calls']for x in old['native_stages'])==12
 return dict(status='pinned_compatibility_report_only',old_source='v2',new_matrix_source='v3',original_native_calls=12,new_native_calls=0,windows=[z['window']for z in c['windows']],formal=False,safety_release=False,payable=False,can_import_old_context=False,claim='原v2的12次实际求解证据经v3新增日末循环吞吐必要约束兼容校核后沿用；不是v3新原生求解或v3运行上下文。')

def render():
 import streamlit as st
 with st.expander('v3兼容沿用报告（不是新求解回执）'):
  st.caption('旧v2证据保留旧源身份；v3新增约束经过独立兼容校核。新v3运行仍须独立准备，旧上下文不得自动改签。这里只读取冻结报告，不重做数学校核。')
  if st.button('读取明确沿用报告（0求解）',key='u_adopted_read'):
   try:
    z=read_adopted();st.info(z['claim']);st.json(z)
   except Exception as e:st.error(str(e))
