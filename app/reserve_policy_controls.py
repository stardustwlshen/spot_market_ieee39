"""Concise new-run policy, separate from historical result presentation."""
import streamlit as st
from smm.reserve_policy import reserve_policy_label


def render_reserve_policy(cfg):
    st.caption('本次上备用：'+reserve_policy_label(cfg)+'。下备用'+
               ('关闭。' if not cfg.mode['reserve'].get('down_enabled',True) or 'up_requirement_mode' not in cfg.mode['reserve'] else '按当前配置开启。'))
