"""Read-only overlay: embed render() in a future app after integration tests."""
import streamlit as st
from gd_ui_rt_agc_evidence import rt_status,agc_status,synthetic_demo_status

def render():
 st.subheader('实时重新组合与调频安全闭环：只读证据')
 st.info('本历史证据区不启动求解。新实时单窗启动位于上方独立入口；调频候选准备与历史失败在此分开查看。')
 st.warning('独立研究，不是正式市场或安全通过。原 D 日 288 格每台热机占用 10 MW 调频，D+1 为 0；当前 W0/W1 近 4 小时热机受原调频及日前保护保持稳定，主要测试储能整数，不能据此声称已演示本日热机新启停。')
 st.caption('同厂 20% 上限与原资格范围不变；排除一台后可能至少缺 10 MW，不凭空补资格或降低需求。此页不启动求解、不签发调度、不生成付款。')
 tabs=st.tabs(['RT-SCUC→SCED','调频候选与安全核验','独立纯假设演示（非实测）'])
 with tabs[0]:
  request=st.text_input('实时请求 JSON',key='new_rt_request');acceptance=st.text_input('对应数学验收 JSON（可空）',key='new_rt_acceptance');receipt=st.text_input('对应模拟执行回执 JSON（可空）',key='new_rt_receipt')
  if st.button('读取实时证据'):
   try:
    d=rt_status(request,acceptance or None,receipt or None);st.json(d);st.warning(d['status']);st.caption('已执行 '+str(d['executed_cells'])+' 格；已下发 '+str(d['issued_cells'])+' 格；下发未执行 '+str(d['issued_not_executed_cells'])+' 格。'+d['frontier_basis'])
   except Exception as e:st.error('证据不可接受：'+str(e))
 with tabs[1]:
  snapshot=st.text_input('调频闭环快照 JSON',key='new_agc_snapshot')
  if st.button('读取调频证据'):
   try:
    d=agc_status(snapshot);st.json({k:v for k,v in d.items()if k not in ('rows','awards')});st.dataframe(d['rows'],width='stretch');st.dataframe(d['awards'],width='stretch');st.warning('当前状态：'+d['status']+'。缺失条件继续待核验；不能视为安全通过。')
   except Exception as e:st.error('证据不可接受：'+str(e))
 with tabs[2]:
  st.warning('此处仅演示假设 C1 通信失败后的状态迁移；不是实测健康、不是新 SCED/AC，不改变实际 loss_allocation_pending 结果。')
  demo=st.text_input('纯假设演示结果 JSON',key='new_agc_synthetic')
  if st.button('读取纯假设演示（非实测）'):
   try:st.json(synthetic_demo_status(demo))
   except Exception as e:st.error('假设证据不可接受：'+str(e))
if __name__=='__main__':render()
