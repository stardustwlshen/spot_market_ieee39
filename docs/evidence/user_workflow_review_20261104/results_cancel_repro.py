from pathlib import Path
import json,time,copy
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from smm import scenario as sc
from smm.config import ROOT,load_config
from smm import scuc
from smm.pipeline import prepare
E=Path('/workspace/scratch/42c5fe32dad0/ieee39-generator-evidence/flow3')
sc.SCEN_DIR=E/'scenarios';sc.RUNS_DIR=E/'runs'
runs=sc.list_runs();r=Path(next(m['dir'] for m in runs if m['status']=='done'))
q=json.loads((r/'submitted_bids.json').read_bytes())
resolved=json.loads((r/'resolved_bids.json').read_bytes())
for uid,ub in q['units'].items():
 for field,value in ub.items(): assert resolved['units'][uid][field]==value,(uid,field)
cfg=load_config(root=r/'scenario_snapshot');case=prepare(cfg,3,read_only=True);md=scuc.prepare(case)
assert md.thermal['QA_coal']['noload_bid']==432.1
assert case.bids['units']['QA_storage']['soc_end']==.52
assert case.bids['units']['QA_pumped']['vup_end']==q['units']['QA_pumped']['vup_end']
for uid in ['QA_coal','QA_wind','QA_pv','QA_storage','QA_pumped']:
 assert uid in pd.read_csv(r/'rt_units.csv').unit.values
p=pd.read_csv(r/'da/da_storage.csv');print('DA_STORAGE_END',p[p.unit=='QA_storage'].tail(1).to_dict('records'),flush=True)
p=pd.read_csv(r/'da/da_pumped.csv');print('DA_PUMPED_END',p[p.unit=='QA_pumped'].tail(1).to_dict('records'),flush=True)
# Read actual result page, compare values directly with its exported source tables.
at=AppTest.from_file(str(ROOT/'app/pages/3_结果.py'),default_timeout=120).run()
assert not at.exception,[x.message for x in at.exception]
print('TABS',[x.label for x in at.tabs],flush=True)
print('RESULT_WARNINGS',[x.value for x in at.warning],flush=True)
print('DOWNLOADS',[x.proto.label for x in at.get('download_button')],flush=True)
profit=pd.read_csv(r/'operating_profit_by_unit.csv')
table=next(d.value for d in at.dataframe if '模拟运营利润 元' in d.value and '净能量现金结算 元' in d.value)
pd.testing.assert_series_equal(table['模拟运营利润 元'].reset_index(drop=True),profit.simulated_operating_profit_yuan,check_names=False)
print('NEW_ASSET_PROFIT',profit[profit.unit.str.startswith('QA_')].to_dict('records'),flush=True)
def item(xs,label):return next(x for x in xs if x.label==label)
at=AppTest.from_file(str(ROOT/'app/pages/2_运行.py'),default_timeout=90).run()
item(at.selectbox,'当前场景').set_value('报价员实操').run()
item(at.radio,'运行模式').set_value('da').run()
item(at.button,'▶ 开始运行').click().run()
assert not at.exception
run=Path(at.session_state['watch_run']);print('CANCEL_RUN',run,flush=True)
assert item(at.button,'▶ 开始运行').disabled
import common
try: common.start_run('报价员实操','da',3,None,bids=q)
except Exception as exc: print('REPEAT_BLOCKED',type(exc).__name__,str(exc),flush=True)
else: raise AssertionError('double launch not blocked')
time.sleep(3)
item(at.button,'请求取消（窗口边界）').click().run()
assert not at.exception and (run/'cancel.request').exists()
print('CANCEL_WARNINGS',[x.value for x in at.warning],flush=True)
for i in range(120):
 m=json.loads((run/'meta.json').read_bytes())
 if m['status'] not in ('starting','running','cancelling'):break
 time.sleep(1)
assert m['status']=='stopped',m
assert not m['settled'] and not m['settlement_calculated'] and m['executed_periods']==0
at.run();assert not at.exception
assert not item(at.button,'▶ 开始运行').disabled
print('CANCEL_TERMINAL',m,flush=True)
print('DONE',flush=True)
