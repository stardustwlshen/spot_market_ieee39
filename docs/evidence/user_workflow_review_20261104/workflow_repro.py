import os, sys, json, copy, time
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path('/workspace/scratch/42c5fe32dad0/ieee39-generator-qa')
EVIDENCE=Path('/workspace/scratch/42c5fe32dad0/ieee39-generator-evidence/flow3')
from smm import scenario as sc
from smm.config import load_config
sc.SCEN_DIR=EVIDENCE/'scenarios';sc.RUNS_DIR=EVIDENCE/'runs'
PAGE=ROOT/'app/pages/2_运行.py'

def item(elements,label):return next(x for x in elements if x.label==label)
def check(at,stage):
    assert not at.exception, [(e.message,e.stack_trace) for e in at.exception]
    print(stage, 'errors=', [x.value for x in at.error], flush=True)

def snap(root): return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
sc.ensure_default()
at=AppTest.from_file(str(PAGE),default_timeout=90).run();check(at,'open')
old=snap(sc.scenario_path('默认'))
item(at.text_input,'新版场景名称').set_value('报价员实操')
item(at.button,'创建并切换到新版场景').click().run();check(at,'new scene')
# Harness-only solver budget for bounded exploratory run, not a product default.
b=sc.read_yaml('报价员实操','base');b['solver']['time_limit_s']=30;sc.write_yaml('报价员实操','base',b)
at.run()
for kind in ['coal','wind','pv','storage','pumped']:
    item(at.radio,'机组操作').set_value('新增机组').run()
    item(at.selectbox,'新增类型').set_value(kind).run()
    item(at.text_input,'新机组编号（不能重复）').set_value('QA_'+kind)
    item(at.number_input,'总装机功率（MW）').set_value(50.)
    item(at.selectbox,'接入节点').set_value(31)
    item(at.button,'核对技术参数').click().run();check(at,'inspect '+kind)
    item(at.button,'检查参数').click().run();check(at,'validate '+kind)
    assert not at.error
    item(at.button,'保存机组').click().run();check(at,'save '+kind)
    assert not at.error
    item(at.selectbox,'模拟机组').set_value('QA_'+kind).run();check(at,'choose '+kind)
    key='quote_current:报价员实操'
    original=copy.deepcopy(at.session_state[key])
    curve=next(c for c in ['segments','dis_segments','gen_segments'] if c in original['units']['QA_'+kind])
    widget=f'quote_edit:{key}:QA_{kind}:{curve}'
    price=original['units']['QA_'+kind][curve][0]['price']
    at.session_state[widget]={'edited_rows':{0:{'报价（元/MWh）':-1000.}},'added_rows':[],'deleted_rows':[]}
    at.run();check(at,'invalid price '+kind)
    assert item(at.button,'▶ 开始运行').disabled
    at.session_state[widget]={'edited_rows':{0:{'报价（元/MWh）':price}},'added_rows':[],'deleted_rows':[]}
    at.run();check(at,'repair price '+kind)
    assert not item(at.button,'▶ 开始运行').disabled
    item(at.checkbox,'允许模拟报价替换该机组手工修改').check().run()
    item(at.button,'按成本生成预览').click().run();check(at,'preview '+kind)
    item(at.button,'采用预览报价').click().run();check(at,'accept '+kind)
    assert not at.error
assert snap(sc.scenario_path('默认'))==old
# Test scalar offer and terminal declarations with exact frozen values later.
item(at.selectbox,'模拟机组').set_value('QA_coal').run()
item(at.number_input,'空载报价（元/h）').set_value(432.1).run()
item(at.selectbox,'模拟机组').set_value('QA_storage').run()
item(at.number_input,'期末 SOC（0–1，日前硬约束）').set_value(.52).run()
item(at.selectbox,'模拟机组').set_value('QA_pumped').run()
v=item(at.number_input,'日末上库库容（万m³，日前硬约束）').value
item(at.number_input,'日末上库库容（万m³，日前硬约束）').set_value(v+1).run()
item(at.checkbox,'短时试跑').check().run()
item(at.number_input,'实时试跑时段数（每段15分钟）').set_value(8)
submitted=copy.deepcopy(at.session_state['quote_current:报价员实操'])
item(at.button,'▶ 开始运行').click().run();check(at,'real start')
assert item(at.button,'▶ 开始运行').disabled
run=Path(at.session_state['watch_run'])
assert json.loads((run/'submitted_bids.json').read_bytes())==submitted
print('RUN',run,flush=True)
for i in range(150):
    m=json.loads((run/'meta.json').read_bytes())
    if m['status'] not in ('starting','running','cancelling'):break
    if i%10==0:print('WAIT',m['status'],m.get('stage'),flush=True)
    time.sleep(1)
print('TERMINAL',m,flush=True)
at.run();check(at,'completed UI')
print('METRICS',[(x.label,x.value) for x in at.metric],flush=True)
print('WARNINGS',[x.value for x in at.warning],flush=True)
print('RUN-LOG-TAIL',(run/'run.log').read_text()[-3500:],flush=True)
