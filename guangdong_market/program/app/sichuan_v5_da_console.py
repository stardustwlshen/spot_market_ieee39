"""Read-only dispatch console for a published Dadu day."""
from pathlib import Path

import streamlit as st

from sichuan_v5_da_report import review_data
from sichuan_v5_dadu_da import read_day

ASSETS = Path(__file__).with_name('dadu_sheet')


@st.cache_data(show_spinner=False, max_entries=6)
def console_data(root, name, label):
    result = read_day(root, name)
    review = review_data(result, label)
    stations = []
    fields = ('local', 'arrival', 'spill', 'declared_low', 'declared_high',
              'approved_high', 'scuc', 'sced', 'price')
    for station in review['stations']:
        stations.append({**{k: station[k] for k in ('id', 'name', 'model', 'bus')},
                         'series': {k: station['series'][k] for k in fields}})
    return dict(label=label, signature=review['signature'], system=review['system'],
                grid=review['grid'], reserve=review['reserve'], stations=stations,
                audit_slack=result['audit']['max_slack_mw'])


def render_console(root, name, label):
    data = console_data(str(root), name, label)
    component = st.components.v2.component(
        'dadu_dispatch_console', html='<div class="dispatch-console"></div>',
        css=(ASSETS / 'dispatch_console.css').read_text(encoding='utf-8'),
        js=(ASSETS / 'dispatch_console.js').read_text(encoding='utf-8'))
    component(data=data, key='dispatch_console_' + name, width='stretch')


def console_header():
    st.html('<style>' + (ASSETS / 'workspace.css').read_text(encoding='utf-8') + '</style>')
    st.html('''<header class="market-bar">
        <div class="market-brand"><span class="market-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <h1>四川电力现货<span>日前调度工作台</span></h1></div>
        <div class="market-context"><b>大渡河梯级</b><span>IEEE 39 节点</span><span>研究仿真</span></div>
        </header>''')
