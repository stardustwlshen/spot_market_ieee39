"""Produce the same interactive day-ahead review for Streamlit and offline use."""
import json
from pathlib import Path

from sichuan_v5_da_review import daily_metrics, station_quarters, system_quarters


ASSETS = Path(__file__).with_name('dadu_sheet')


def _columns(rows):
    return {key:[r[key] for r in rows] for key in rows[0]}


def review_data(result, label):
    data = result['inputs']
    lines = data['lines']
    rows = result['da']
    system = system_quarters(rows,data['assets'],lines)
    stations = []
    for station in data['stations']:
        uid = station['id']
        trace = station_quarters(result,station)
        stations.append(dict(id=uid,name=station['name'],bus=station['bus'],model=station['model'],
                             capacity=station['capacity_mw'],series=_columns(trace),
                             raw=result['raw_offers'][uid],effective=result['effective_offers'][uid],
                             daily_water=sum(r['spill'] for r in trace)*900,
                             daily_generation=sum(r['sced'] for r in trace)*.25))
    mix = []
    for kind in ('hydro','thermal','fixed','renewable','storage'):
        group = [a for a in data['assets'] if a['kind']==kind]
        if group:
            mix.append(dict(kind=kind,capacity=sum(a['capacity_mw'] for a in group),
                            available=[sum(slot[a['id']] for a in group) for slot in data['available']]))
    pump = data.get('pumped_storage')
    if pump:
        pump = dict(bus=pump['bus'],plan=pump['signed_mw'],start=pump['initial_mwh'],
                    energy=[row['energy'][pump['id']] for row in rows])
    grid = []
    for row in rows:
        prices = row['raw_nodal_prices']
        low = min(prices,key=lambda b:(prices[b],int(b)))
        high = max(prices,key=lambda b:(prices[b],-int(b)))
        def top(flows):
            if not flows:
                return None
            line = max(lines,key=lambda l:abs(flows[l['id']])/l['limit_mw'])
            return [line['id'],flows[line['id']],line['limit_mw']]
        grid.append(dict(low=[low,prices[low]],high=[high,prices[high]],base=top(row['flows']),
                         stress=top(row.get('forecast_stress_flows_mw')),reserve=sum(row['thermal_reliability_reserve_mw'].values())))
    ref = result.get('storage_comparison',{'status':'historical'})
    return dict(label=label,scenario=result['scenario'],signature=result['company']['declaration_sha256'],
                system=_columns(system),totals=daily_metrics(system),grid=grid,stations=stations,mix=mix,pump=pump,
                reference=ref,mitigation=result['policy']['active'],reserve=result['margin']['da_requirement_mw'],
                max_residual=result['audit']['max_node_residual_mw'])


def standalone_report(reviews):
    if not reviews:
        raise ValueError('需要至少一份已出清的日前归档')
    css = (ASSETS/'day_review.css').read_text(encoding='utf-8')
    js = (ASSETS/'day_review.js').read_text(encoding='utf-8')
    payload = json.dumps(reviews,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>大渡河 · 日前现货复盘</title><style>'
            'body{margin:0;background:#f3f5f0;padding:clamp(8px,2vw,28px)}main{max-width:1240px;margin:auto}'
            +css+'</style><main><div class="da-review"></div></main><script type="application/json" id="snapshots">'
            +payload+'</script><script>'+js+'\nrenderDayReview(document.querySelector(".da-review"),'
            'JSON.parse(document.getElementById("snapshots").textContent));</script></html>')


def render_review(review, key):
    import streamlit as st
    component = st.components.v2.component('dadu_day_review',html='<div class="da-review"></div>',
        css=(ASSETS/'day_review.css').read_text(encoding='utf-8'),
        js=(ASSETS/'day_review.js').read_text(encoding='utf-8')+
           '\nexport default function({parentElement,data}){renderDayReview(parentElement.querySelector(".da-review"),[data]);}')
    component(data=review,key=key,width='stretch')
