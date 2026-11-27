"""Replay archived runs through the real UI; this acceptance test never runs a solver."""
import json
from math import fsum
import os
from pathlib import Path
from shutil import copytree

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


PROGRAM = Path(__file__).resolve().parents[1]


def test_archives_through_five_result_areas(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile as profiles
    import sichuan_v5_dadu_run as backend
    from sichuan_v5_dadu_overview import matched_pair

    root = Path(os.environ.get('DADU_NAVIGATION_RUNS', PROGRAM/'runs_sichuan_v5_consultation'))
    short_root = Path(os.environ.get('DADU_NAVIGATION_SHORT_RUNS', root))
    def pick(location, *, short):
        results = []
        for name in backend.list_dadu(location):
            receipt = json.loads((location/'dadu19'/name/'receipt.json').read_bytes())
            if short and receipt['periods']<96:
                return [(name,backend.read_dadu(location,name))]
            if not short and receipt['scenario']=='forecast_error' and receipt['periods']==96:
                results.append((name,backend.read_dadu(location,name)))
        return results
    full = pick(root,short=False)
    shorts = pick(short_root,short=True)
    pairs = [(n,r,other,_r) for n,r in full if r['company']['strategy']=='naive'
             and max(fsum(q['shed'].values()) for q in r['rt'])>1e-5
             for other,_r in full if matched_pair(r,_r)]
    if not pairs or not shorts:
        pytest.skip('Set DADU_NAVIGATION_RUNS and DADU_NAVIGATION_SHORT_RUNS to full paired and short archives')
    naive_id, naive, water_id, water = pairs[0]
    short_id, short_run = shorts[0]
    foreign = next((n for n,r in full if r['company']['strategy']=='water_value'
                    and r['summary']['boundary_hash']!=naive['summary']['boundary_hash']),None)
    local = tmp_path/'ui'
    chosen = [(root,naive_id),(root,water_id),(short_root,short_id)]
    if foreign:
        chosen.append((root,foreign))
    for source,name in chosen:
        copytree(source/'dadu19'/name,local/'dadu19'/name)
    monkeypatch.setattr(profiles.MarketProfile,'default_results',lambda self,_:local)
    monkeypatch.setattr(backend,'run_dadu',lambda *a,**k:pytest.fail('archive navigation launched the solver'))
    visible=backend.list_dadu
    excluded=set()
    monkeypatch.setattr(backend,'list_dadu',lambda directory:[n for n in visible(directory) if n not in excluded])
    app = AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=90).run()
    app.radio(key='dadu_workspace').set_value('市场结果').run()
    def check():
        assert not app.error and not app.exception,([e.value for e in app.error],[e.value for e in app.exception])
        tabs = [tab.label for tab in app.tabs]
        stages = ['已冻结申报','日前预出清','实时出清与执行','全流域水情','收入与对照']
        assert [label for label in tabs if label in stages] == stages
    def pair_table():
        return next((d.value for d in app.dataframe if '水价值减朴素' in d.value),None)
    check()
    app.selectbox(key='dadu_saved').select(naive_id).run()
    check()
    peak = max(range(96), key=lambda t:fsum(naive['rt'][t]['shed'].values()))
    app.button(key='dadu_worst_'+naive_id).click().run()
    check()
    assert app.selectbox(key='dadu_q_'+naive_id).value==peak
    assert any('系统失负荷' in row.value for row in app.warning)
    slot = pair_table().set_index('指标')
    assert slot.loc['系统失负荷（MW）','朴素报价'] == pytest.approx(fsum(naive['rt'][peak]['shed'].values()))
    assert pd.isna(slot.loc['本站采用节点价（元/MWh）','朴素报价'])
    app.radio(key='dadu_pair_scope').set_value('已执行累计').run()
    pair = pair_table().set_index('指标')
    delta = .25 * (fsum(fsum(q['shed'].values()) for q in water['rt'])
                   -fsum(fsum(q['shed'].values()) for q in naive['rt']))
    assert pair.loc['失负荷（MWh）','水价值减朴素']==pytest.approx(delta)
    app.selectbox(key='dadu_unit_'+naive_id).select('D10').run()
    origin = naive['rt'][peak]['hydro']['D10']['release_m3s'] * 900
    app.button(key='dadu_arrival_'+naive_id).click().run()
    check()
    reached = app.selectbox(key='dadu_q_'+naive_id).value
    assert app.selectbox(key='dadu_unit_'+naive_id).value=='D11'
    assert reached==peak+5
    assert naive['rt'][reached]['hydro']['D11']['upstream_arrival_m3']==pytest.approx(origin)
    assert app.selectbox(key=f'dadu_water_reach_{naive_id}_D11').value=='瀑布沟 → 深溪沟'
    app.button(key='dadu_worst_'+naive_id).click().run()
    for station, asset in [('D10','COAL1'),('D19','GAS1')]:
        app.selectbox(key='dadu_unit_'+naive_id).select(station).run()
        app.selectbox(key=f'dadu_market_{naive_id}_{station}').select(asset).run()
        app.radio(key='grid_area_'+naive_id).set_value('煤机与气电').run()
        check()
    assert any('气电执行保供计划' in c.value for c in app.caption)
    app.radio(key='grid_area_'+naive_id).set_value('线路与备用').run()
    check()
    app.button(key='dadu_switch_pair_'+naive_id).click().run()
    check()
    assert app.selectbox(key='dadu_saved').value==water_id
    assert app.selectbox(key='dadu_unit_'+water_id).value=='D19'
    assert app.selectbox(key='dadu_q_'+water_id).value==peak
    assert pair_table() is not None
    excluded.add(water_id)
    app.selectbox(key='dadu_saved').select(naive_id).run()
    check()
    assert pair_table() is None
    app.selectbox(key='dadu_saved').select(short_id).run()
    check()
    n=len(short_run['rt'])
    assert len(app.selectbox(key='dadu_q_'+short_id).options)==n
    assert app.selectbox(key='dadu_q_'+short_id).value==n-1
    assert app.selectbox(key='dadu_unit_'+short_id).value=='D19'
    assert any('部分日' in c.value and f'{n}/96' in c.value for c in app.info)
    cash=next(d.value for d in app.dataframe if '含水价值研究收益 元' in d.value)
    assert cash['含水价值研究收益 元'].isna().all()
    assert set(visible(local))=={name for _,name in chosen}
