"""Release-registered fixed results, never a live status or recovery route."""
from pathlib import Path
import json
B=Path(__file__).resolve().parent
from gd_price_quality import render_many as render_price_qualities
def render(selected_da):
 import streamlit as st
 st.caption('历史示例保持原版本与原来源；不是v19新运行。未附价格质量字段不能默认为零。')
 with st.expander('最终已核研究结果：显式接续全天 / 正常入口首小时（固定只读）',expanded=False):
  st.warning('固定示例，不随当前场景/报价编辑自动重算。全天来自显式接续，原UI曾失败；6720首小时是另一独立正常入口。两者均非安全放行或正式账单。')
  choice=st.selectbox('选择最终已核来源',['同来源显式接续全天研究结果','最终6720正常入口首小时'],key='final_fixed_choice')
  try:
   from gd_live_control import sources
   sources()
   regs=json.loads((B/'FINAL_RESULTS_REGISTRATION.json').read_text());day=choice.startswith('同来源');reg=regs['continuation'if day else'normal12'];rel=Path(reg['bundle'])
   if rel.is_absolute():raise ValueError('registered bundle must be relative')
   root=(B/rel).resolve()
   if not root.is_relative_to(B.parent):raise ValueError('snapshot outside application')
   if day:
    from gd_continuation_card import load,render as show
   else:
    from gd_agc_results import load_snapshot as load
   index,tables=load(root,reg['index_sha256'])
   if Path(selected_da).resolve()!=Path(index['selected_DA']).resolve():
    if not st.checkbox('确认查看不同DA的固定历史结果，不作为当前编辑场景结果',key='final_history_source_'+str(day)):st.info('来源不同，尚未展示。');return
   if day:show(root,reg['index_sha256'])
   else:
    st.caption('源版本 '+index['source_version'][:16]+'；case '+index['source_case_sha256'][:16]+'；DA '+index['selected_DA_artifacts_sha256'][:16]+'；分支 '+index['branch_id'])
    st.info(index['warning']);st.caption(index['performance_note'])
    status=tables['STATUS.json'];st.metric('首小时账户电能量分项净合计（研究，元）',status['hour']['total_energy_component_CNY'])
    st.json({k:index[k]for k in ['completed_windows','accepted_price_quarters','account_rows','executed_cells','issued_cells','native_calls','AC_calls','safety_status','safety_release','formal_bill']})
    price,hour,agc,perf=st.tabs(['4季度价格','54行账户分项','原始耐久H1回执','并行耗时与原正常状态'])
    with price:
     from gd_partial_day_panel import render_price_chart
     q=[dict(day_quarter=v['quarter'],raw_prices=v['raw_prices'])for v in status['quarters']];render_price_chart(q);st.dataframe(q,hide_index=True)
     render_price_qualities(status['quarters'])
    with hour:st.dataframe(status['hour']['rows'],hide_index=True)
    with agc:
     from gd_agc_results import render as show_agc
     show_agc(status['agc_results'])
    with perf:st.json(tables['WAIT_RESULT.json']);st.json(tables['RUN_CONTEXT.json']);st.json(status['exit'])
  except Exception as e:st.error('最终固定结果来源读取被拒绝：'+str(e))
