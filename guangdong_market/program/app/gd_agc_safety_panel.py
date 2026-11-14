from pathlib import Path
import json,hashlib
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read_gate(base):
 base=Path(base);r=json.loads((base/'AGC_SAFETY.json').read_text());b=(base/r['bundle']).resolve();m=b/'MANIFEST.json'
 if sha(m)!=r['manifest_sha256']:raise ValueError('安全证据清单改变')
 for n,h in json.loads(m.read_text())['files'].items():
  p=(b/n).resolve()
  if not p.is_relative_to(b)or sha(p)!=h:raise ValueError('安全证据改变')
 z=json.loads((b/'SAFETY_RESULT.json').read_text());c=json.loads((b/'COMPARISON.json').read_text())
 if z['safety_release']or z['dispatch_authorized']or z['unit_removals']or z['research_no_increase_check']!='fail':raise ValueError('安全gate状态异常，不能放行')
 return z,c
def render():
 import streamlit as st
 try:z,c=read_gate(Path(__file__).resolve().parent)
 except Exception as e:st.error('调频安全未放行：无法核实固定研究证据。'+str(e));return
 st.error('调频安全未放行：当前原案例的小时AGC弃电不增加检查失败；Optimal不等于安全放行。')
 st.caption('原修正分支W45固定整数AGC0独立对照，非当前编辑场景或新H12正式调频出清。12–13时弃电 '+str(round(c['baseline_hour_curtailment_MWh'],6))+' → '+str(round(c['AGC0_hour_curtailment_MWh'],6))+' MWh；全4小时减少约50 MWh。原400 MW、固定UC及储能限制保持。')
 st.write({'研究安全放行':False,'自动逐机移除':[],'逐机归因':'未建立','仍需':'交流校验、AGC状态、安全断面及后续重新出清调查'})
 st.caption('此处只核随包证据完整性，原始优化来源已在导出时核验并保留索引，不在异地重复求解。')
