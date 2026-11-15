"""Read-only same-result AC diagnostics; never a live receipt or dispatch permission."""
from pathlib import Path
import hashlib,json,math

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def need(ok,message):
 if not ok:raise ValueError(message)

def load(view,bundle,*,manifest_sha256):
 """A caller must pin its chosen diagnostic manifest, not trust a self-hash as authority.
 The execution-view source binding prevents a same-name/different-history match.
 Reading completed diagnostics is allowed even when physical checks failed.
 """
 root=Path(bundle).resolve();manifest=root/'MANIFEST.json'
 need(sha(manifest)==manifest_sha256,'安全诊断清单与所选版本不一致')
 index=read(manifest)
 for name,h in index['files'].items():
  p=(root/name).resolve();need(p.is_relative_to(root) and sha(p)==h,'安全诊断文件已变更：'+name)
 source=read(root/'SOURCE.json');result=read(root/'AC_RESULT.json');mapping=read(root/'MAPPING.json');actual=read(root/'ACTUAL_DIAGNOSTIC.json');interpretation=read(root/'INTERPRETATION.json');started=read(root/'AC_STARTED.json')
 need(source['run_id']==view['run_id']==actual['run_id']==started['run_id'],'安全诊断属于另一个运行')
 tick=source['executed_tick'];need(type(tick)is int and tick in view['execution_receipt']['executed_ticks'],'安全诊断点不是本结果的已执行格')
 # The exported view already verified its source_manifest; retain exact execution evidence.
 selected=view['source_manifest'];anchors={p:h for p,h in source['source_pins'].items() if Path(p).name.startswith('EXECUTION_H')}
 need(len(anchors)==1,'缺少唯一执行原始记录绑定')
 for p,h in anchors.items():
  matches=[q for q,digest_value in selected.items() if Path(q).name==Path(p).name and digest_value==h]
  need(bool(matches),'安全诊断的执行原件不属于当前结果快照') # exact whole-file bytes, permits recorded restore-path relocation
 digest=lambda z:hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 need(result['input_sha256']==started['input_sha256']==digest(mapping['ac_input']),'交流输入输出绑定不一致')
 need(actual['new_AC_calls']==1 and actual['new_native_calls']==0,'调用记录不一致')
 need(actual['posthoc_not_live_safety_receipt'] is True,'事后诊断不得转换为实时放行票据')
 violations=result.get('violations',[])
 failed=result.get('ac_snapshot_safe')is not True
 if not failed:need(not violations,'安全通过与越限记录矛盾')
 missing=[{'check':'全天AC覆盖','status':'未验证','required':'每个已执行控制点的同源潮流/控制与网络设备状态；单点不得扩展为全天'},
 {'check':'N-1及断面安全','status':'未验证','required':'可信故障集合、设备状态、断面/事故后限值与逐故障校核'},
 {'check':'动态/AGC性能','status':'未验证','required':'机组及储能真实调节资格、爬坡/响应/性能数据，不能以50MW研究分配代替'},
 {'check':'交流无功能力与控制','status':'研究假设','required':'可追溯的机组Q限、AVC/VG目标、变压器档位、负荷Q及并网装置P-Q能力；当前Q0和比例Q/P不是实测'}]
 actions=[]
 if any(x['kind']=='GENERATOR_P_LIMIT'for x in violations):actions.append('把实测/模型可追溯的网损纳入未执行时段再出清，保留原P上下限、AGC占用及双400MW；重出清后重新AC核查，不能只修改平衡机读数。')
 if any(x['kind']=='BUS_VOLTAGE_LIMIT'for x in violations):actions.append('核实VG/AVC、变压器档位、负荷Q及设备Q能力后，在原电压界内求控制；原GT VG=1.0636已超过1.06，不可静默裁剪或凭空增加逆变器Q能力。')
 return dict(schema='gd.same_result.safety_diagnostic.v1',status='research_snapshot_unsafe'if failed else'point_pass_other_checks_missing',run_id=view['run_id'],point_start=mapping['start'],point_end=mapping['end'],executed_tick=tick,AC_checked_cells=1,total_displayed_executed_cells=len(view['execution_receipt']['executed_ticks']),coverage_scope='one already-executed point; posthoc, not live callback',AC_status=result['status'],violations=violations,interpretation=interpretation,missing_checks=missing,required_actions=actions,source_manifest_sha256=manifest_sha256,original_SCED_preserved=True,physical_infeasibility_proven=False,dispatch_authorized=False,safety_release=False,publication_allowed=False,read_only_results_allowed=True,formal=False)

def require_dispatch(diagnostic):
 raise ValueError('禁止安全调度发布：单点事后交流诊断不具备全天、N-1或动态安全放行资格；研究结果仍可查看。')

def render(diagnostic):
 import streamlit as st
 if diagnostic['status']=='research_snapshot_unsafe':st.error('当前运行的实际执行点交流校核未通过；不能作为安全调度方案。')
 else:st.warning('仅单点基态交流通过，其他安全条件仍未验证，不可发布调度。')
 st.caption(f"已检查 {diagnostic['AC_checked_cells']}/{diagnostic['total_displayed_executed_cells']} 个已显示执行格；{diagnostic['point_start']}，事后研究诊断，不是实时安全票据。")
 i=diagnostic['interpretation']
 st.write(f"C1市场计划 {i['C1_market_scheduled_MW']:.3f} MW，AC平衡机 {i['C1_AC_slack_MW']:.3f} MW；增加 {i['AC_slack_increment_MW']:.3f} MW 对应网损，原技术上限 {i['original_technical_Pmax_MW']} MW。原DC计划未违约，未闭合的是网损到AC安全衔接。")
 st.caption('GT固定VG=1.0636本就超过1.06；其余为潮流越限。以下交流母线ID从0计数，市场节点号为ID+1。')
 if diagnostic['violations']:st.dataframe(diagnostic['violations'],hide_index=True)
 for action in diagnostic['required_actions']:st.write(action)
 with st.expander('仍缺少的安全数据与校核'):
  st.dataframe(diagnostic['missing_checks'],hide_index=True)
  st.caption('不改变原400MW+400MW要求、不扩大资源资格、不证明整个市场物理无解；不阻止查看研究图表。')
