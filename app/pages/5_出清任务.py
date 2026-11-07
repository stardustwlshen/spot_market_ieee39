"""Compatibility route for saved bookmarks; the active app is single-day only."""
import streamlit as st
from common import page_setup

page_setup('单日模拟入口')
st.info('请从单日模拟开始，依次查看场景、机组、报价、运行和结果。')
st.page_link('pages/6_单日模拟.py', label='前往单日模拟', icon='▶️')
