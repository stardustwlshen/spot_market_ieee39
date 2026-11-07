"""Explicit single-day routes, default pages/6_单日模拟.py; safe legacy URLs."""
import streamlit as st
from common import PAGES

pages = [st.Page(path, title=title, icon=icon, default=index == 0)
         for index, (path, title, icon, _) in enumerate(PAGES)]
# Compatibility URLs only render a safe single-day link or parameter redirect.
# Explicit navigation disables Streamlit's automatic pages-directory discovery.
pages.extend([
    st.Page("pages/1_边界条件.py", title="场景参数入口", url_path="边界条件"),
    st.Page("pages/5_出清任务.py", title="单日模拟入口", url_path="出清任务"),
])
st.navigation(pages, position="hidden").run()
