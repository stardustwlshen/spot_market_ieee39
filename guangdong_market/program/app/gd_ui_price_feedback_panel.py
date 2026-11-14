import streamlit as st
from gd_ui_price_feedback_evidence import price_comparison,security_feedback,csv_bytes

def render():
 st.subheader('新实时独立 PRICE 与后继安全反馈：只读')
 with st.expander('两种研究定价域对比（官方解释尚未裁定）'):
  st.warning('两域都是研究模型，官方解释尚未裁定。156 个节点价、78 行对比只覆盖两个已定价首交付时段；缺 00:00–00:15 初始段，无完整小时价格、无结算。不能把 RT 对偶当独立 PRICE，也不据此裁定哪域正式正确。')
  price=st.text_input('PRICE 对比 COMPARISON_SUMMARY.json',key='pf_price')
  if st.button('读取新 PRICE 对比（不求解）'):
   try:
    z=price_comparison(price);st.caption('同 run：'+z['run_id']+'；从已绑定原始 PRICE 对偶重核节点价，不填缺价。');st.dataframe(z['rows'],width='stretch');st.download_button('下载78行研究对比 CSV',csv_bytes(z['rows']),'research_PRICE_comparison.csv','text/csv');st.download_button('下载156个原始研究节点价 CSV',csv_bytes(z['prices']),'research_PRICE_nodes.csv','text/csv')
   except Exception as e:st.error('PRICE 证据不可接受：'+str(e))
 with st.expander('独立后继安全反馈（不替换原票据）'):
  st.warning('后继反馈单独关联原 base_binding，不替换原 loss_allocation_pending 票据，不用于其他候选。voltage_correction_pending 表示仍有电压越限，不是安全通过，不支付、不发布。')
  feedback=st.text_input('后继 FINAL_FEEDBACK.json',key='pf_feedback');base=st.text_input('对应原候选 PHYSICAL_REQUEST.json（必须精确匹配）',key='pf_base')
  if st.button('读取关联后继反馈（不求解）'):
   try:
    z=security_feedback(feedback,base);st.json({k:z[k]for k in ('status','base_binding','loss_proxy_MW','changed_rhs_rows','base_loop_acceptance_changed','payable','dispatch_authorized')});st.dataframe(z['remaining_violations'],width='stretch');st.download_button('下载后继剩余越限 CSV',csv_bytes(z['remaining_violations']),'successor_voltage_violations.csv','text/csv');st.caption('原条件的数学/交流验收由证据生产者完成；本页校验来源、原始解/回执和汇总一致性，不重新求解。')
   except Exception as e:st.error('后继证据不可接受：'+str(e))
