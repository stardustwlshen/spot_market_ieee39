"""Reopen combined market research and historical archives."""
from pathlib import Path

import streamlit as st

from market_rule_profile import SICHUAN_V5
from sichuan_v5_dadu_view import render_dadu
from sichuan_v5_page import _render_original

st.set_page_config(page_title=SICHUAN_V5.display_name,layout='wide')
dadu, historical = st.tabs(('大渡河 19 站','原四川示例与历史档案'))
with dadu:
    render_dadu(SICHUAN_V5.default_results(Path(__file__).resolve().parents[1]))
with historical:
    _render_original()
