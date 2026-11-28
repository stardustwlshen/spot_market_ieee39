"""Read-only dispatch console for a published Dadu day."""
from math import fsum
from pathlib import Path
from statistics import fmean

import streamlit as st

from sichuan_v5_da_report import review_data
from sichuan_v5_dadu_da import read_day
from sichuan_v5_dadu_view import SCENARIOS as TEMPLATE_NAMES

ASSETS = Path(__file__).with_name('dadu_sheet')


def system_day(review):
    system, totals = review['system'], review['totals']
    def energy(series):
        return fsum(series) * .25

    mix = [dict(kind=source['kind'], capacity=source['capacity'],
                peak=max(system[source['kind']]), mwh=energy(system[source['kind']]))
           for source in review['mix']]
    generation = fsum(row['mwh'] for row in mix)
    generation_peak = max(map(fsum, zip(*(system[row['kind']] for row in mix))))
    demand = energy(system['load'])
    served = demand - totals['shed_mwh']
    excess = energy(system['excess'])
    lines = system['max_line_pu']
    return dict(mix=mix, generation=generation, generation_peak=generation_peak,
                capacity=fsum(row['capacity'] for row in mix), demand=demand, served=served,
                excess=excess, pump=totals['pump_mwh'], shed=totals['shed_mwh'],
                balance=generation-served-totals['pump_mwh']-excess,
                price=totals['weighted_price'],
                price_min=min(g['low'][1] for g in review['grid']),
                price_max=max(g['high'][1] for g in review['grid']),
                max_loading=max(lines), tight=totals['tight_quarters'],
                reserve_short=max(system['reserve_short']),
                reserve_short_slots=sum(v > 1e-5 for v in system['reserve_short']),
                pv_cut=totals['pv_cut_mwh'],
                pv_available=energy(system['renewable']) + totals['pv_cut_mwh'],
                spill=totals['station_spill_m3'])


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
    loads = review['system']['load']
    mean, peak = fmean(loads), max(loads)
    template = result.get('scenario_settings', {}).get('seasonal_template', result['scenario'])
    load_day = dict(template=TEMPLATE_NAMES.get(template, '历史负荷曲线'),
                    valley=min(loads), mean=mean, peak=peak,
                    load_factor=mean/peak if peak else None)
    return dict(label=label, signature=review['signature'], system=review['system'], load_day=load_day,
                grid=review['grid'], reserve=review['reserve'], stations=stations,
                day=system_day(review), audit_slack=result['audit']['max_slack_mw'])


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
