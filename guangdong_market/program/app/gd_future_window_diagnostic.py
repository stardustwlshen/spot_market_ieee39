"""Read-only UI adapter; never interpret predicted curve checks as execution."""
from pathlib import Path
import json,hashlib

def load(root,*,expected_sced_sha256):
 root=Path(root);source=json.loads((root/'SOURCE_BINDING.json').read_bytes())
 if source['SCED_response_sha256']!=expected_sced_sha256:raise ValueError('different candidate SCED; do not inherit diagnostics')
 z=json.loads((root/'SUMMARY.json').read_bytes());rows=z['results']
 if [r['global_tick']for r in rows]!=list(range(291,339)):raise ValueError('missing/duplicate forecast curve cells')
 if z['new_HiGHS']or z['new_PRICE']or z['execution_advancement']or z['new_AC']>47:raise ValueError('diagnostic scope mismatch')
 return {'label':'未执行W96预测出力曲线：48格静态AC校核，不是全天/执行/N-1/动态安全','coverage':len(rows),'converged':z['converged'],'safe':z['safe'],'unsupported':z['unsupported'],'elapsed_seconds':z['elapsed_seconds'],'new_AC':z['new_AC'],'reused_AC':z['reused_AC'],'AGC_headroom_passed_cells':sum(r.get('AGC_preserved')is True for r in rows),'cells':rows,'publication_allowed':False,'executed':False,'loss_feedback_adopted':False}

def render(st,z):
 st.error(z['label'])
 st.write(f"收敛 {z['converged']}/{z['coverage']}；满足静态全部检查 {z['safe']}/{z['coverage']}；不支持 {z['unsupported']}；调频余量满足 {z['AGC_headroom_passed_cells']}/{z['coverage']}")
 st.caption(f"批量耗时{z['elapsed_seconds']:.2f}秒，新增{z['new_AC']}次NR，复用{z['reused_AC']}次；未重新出清、未推进执行。")
 st.dataframe([{'预测格':r['global_tick'],'状态':r['status'],'静态安全':r.get('safe'),'调频余量':r.get('AGC_preserved'),'网损MW':r.get('loss_MW'),'正备用MW':r.get('positive_available_MW'),'事故备用MW':r.get('accident_available_MW')}for r in z['cells']],use_container_width=True)
