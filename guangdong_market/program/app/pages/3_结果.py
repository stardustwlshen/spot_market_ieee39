"""旧页面保留源码归档，不允许误接新省级流程。"""
import streamlit as st
st.set_page_config(page_title="旧页面已停用")
st.info("此旧页面未接入当前省级流程，已停用。请返回单日市场模拟主页。")
st.markdown("[返回单日市场模拟](/)")
st.stop()
