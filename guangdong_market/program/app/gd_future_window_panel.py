"""Separate immutable forecast diagnostic, not current execution or publication."""
from pathlib import Path
import hashlib
from gd_future_window_diagnostic import load,render as render_diagnostic
BUNDLE=Path(__file__).resolve().parent/'future_window_evidence'
PINS={'SOURCE_BINDING.json': 'e3d97816b820121c471be660bbe75c193959bb290d60f2f359eafd7773f90f0b', 'SUMMARY.json': '4cca03d5566aee3838912dfee422b7c77f2b744d8c6ac92de5d0cc25bab50adb'}
SCED='4c3a49b2c63f7ff6737bff937e7cfac4b93329a5537ffe9aaeb46adacdfc9b9e'
PARENT_RUN='unified-first-hour-b76a73dcfc41476593796b3ab61d730b'
def read_selected(view,bundle=None):
 if view.get('run_id')!=PARENT_RUN:raise ValueError('该独立预测候选不属于当前运行')
 root=Path(bundle or BUNDLE)
 for n,h in PINS.items():
  if hashlib.sha256((root/n).read_bytes()).hexdigest()!=h:raise ValueError('预测诊断展示证据已变更')
 return load(root,expected_sced_sha256=SCED)
def render(view):
 import streamlit as st
 if view.get('run_id')!=PARENT_RUN:return
 with st.expander('独立未来候选 W96：未执行预测安全诊断',expanded=False):
  st.caption('独立候选，不计入上方已执行24小时进度或1/288安全覆盖。未采用损耗反馈，不允许安全发布。')
  try:z=read_selected(view)
  except (ValueError,KeyError,OSError) as exc:st.warning(str(exc));return
  render_diagnostic(st,z)
