"""Presentation refinements must preserve real values, editability and readonly guards."""
from pathlib import Path
import sys
import json
import pandas as pd
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from smm import series_workspace as ws,series_ui_adapter as adapter


def _initial(tmp_path,source='research_assumption',begun=False):
    root=ws.create_task(tmp_path,ROOT,title='起始状态UI',start_date='2026-07-15',day_count=2,timezone='Asia/Shanghai',root_seed='1',input_model='causal_physical_v3')
    m=ws.load_task(root);m['initial_state']=adapter.research_initial(root,m);m['initial_state_source']=source;ws.atomic_json(root/'manifest.json',m)
    code=f"""import sys
from pathlib import Path
sys.path.insert(0,{str(ROOT/'app')!r})
from common import page_setup
from series_workspace_ui import initial_panel
from smm.series_workspace import load_task
page_setup('起始状态UI')
p=Path({str(root)!r})
initial_panel(p,load_task(p),{begun!r})
"""
    return root,AppTest.from_string(code,default_timeout=30).run()


def test_grouped_initial_values_and_local_clock(tmp_path):
    root,at=_initial(tmp_path)
    assert not at.exception
    assert [t.label for t in at.tabs]==['火电','电池','水电','抽蓄']
    for frame in at.dataframe[:4]:assert not frame.value.isna().any().any()
    assert any('07-15 00:00' in c.value and 'Asia/Shanghai' in c.value for c in at.caption)
    next(s for s in at.selectbox if s.label=='选择机组或电站').set_value(('hydro','H1')).run()
    assert not at.exception
    table=next(d.value for d in at.dataframe if '时长含义' in d.value)
    assert set(table['时长含义'])=={'精确时长'}
    assert not table['最早切换'].astype(str).str.contains('T|Z',regex=True).any()
    next(n for n in at.number_input if n.label=='起始库容（万m³）').set_value(18001.)
    next(b for b in at.button if b.label=='校验并保存初态').click().run()
    assert not at.exception
    assert ws.load_task(root)['initial_state']['state']['hydro']['H1']==18001.
    at.run();assert next(n for n in at.number_input if n.label=='起始库容（万m³）').value==18001.


def test_actual_initial_stays_readonly(tmp_path):
    root,at=_initial(tmp_path,source='executed_checkpoint')
    before=(root/'manifest.json').read_bytes()
    assert not at.exception
    assert all(n.disabled for n in at.number_input)
    assert next(c for c in at.checkbox if c.label=='起始时已开机').disabled
    assert next(a for a in at.text_area if a.label=='完整研究初态 JSON').disabled
    assert next(b for b in at.button if b.label=='校验并保存初态').disabled
    assert any('中断原实际接续链' in w.value for w in at.warning)
    assert (root/'manifest.json').read_bytes()==before


def test_advanced_parameters_remain_editable_and_default_folded(tmp_path):
    from test_series_workspace_ui import app,button
    at=app(tmp_path)
    advanced=next(e for e in at.expander if e.label=='更多设置（可选）')
    assert not advanced.proto.expanded
    assert {'随机种子','运行时区'} <= {t.label for t in advanced.text_input}
    button(at,'创建模拟').click().run()
    error_panel=next(e for e in at.expander if e.label=='预测情况 · 可分别调整四类误差')
    detail_panel=next(e for e in at.expander if e.label=='详细调整：四类过程参数')
    assert not error_panel.proto.expanded and not detail_panel.proto.expanded
    next(s for s in error_panel.selectbox if s.label=='风电预测情况').set_value('delayed')
    next(n for n in detail_panel.number_input if n.label=='风电 · 慢变化波动强度').set_value(.23)
    button(at,'保存该日场景').click().run()
    assert not at.exception
    m=json.loads(next(tmp_path.glob('series_tasks/*/manifest.json')).read_text())
    assert m['days'][0]['forecast_policy']['wind']=='delayed'
    assert m['days'][0]['process_specs']['wind']['parameters']['slow_sigma']==.23
    assert m['days'][0]['bid_receipt'] is None
    assert not list(tmp_path.glob('series_tasks/*/launch.json'))
