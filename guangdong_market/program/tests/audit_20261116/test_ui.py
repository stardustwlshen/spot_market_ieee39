from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'app'
VENDOR=ROOT/'engines/guangdong_v3/guangdong_after_reset_r23_compressed_20261112/repo/src'
sys.path[:0]=[str(APP),str(VENDOR)]
import sys,types,json
from pathlib import Path
from streamlit.testing.v1 import AppTest
B=APP;sys.path.insert(0,str(B))
STATE={'exit':None,'completed_windows':1,'quarters':[],'hours':[]}
COUNTS={'prepare':0,'start':0}
def mod(name,**kw):
 m=types.ModuleType(name);m.__dict__.update(kw);sys.modules[name]=m
mod('gd_provincial_prepare',inputs_identity=lambda s:{'same':'source'},prepare_fresh=lambda *a,**k: prepare())
def prepare():
 COUNTS['prepare']+=1;return {'da':'/tmp/testDA','run':'/tmp/testRT'}
def status(o):
 if STATE.get('raise'):raise OSError('simulated read error')
 return dict(STATE)
mod('gd_live_control',status=status)
params={'G1':dict(bus='1',min_mw=10,max_mw=100,initial_online=1,initial_power_mw=50,initial_state_age_h=24,min_up_h=2,min_down_h=2)}
mod('gd_provincial_inputs',thermal_parameters=lambda s:params,snapshot=lambda *a,**kw:'/tmp/edited')
mod('gd_resource_editor',render_resource_editor=lambda *a:{})
mod('gd_resource_add_editor',render_resource_add_editor=lambda *a:[])
mod('gd_renewable_bid_editor',render_renewable_bid_editor=lambda *a:{})
mod('gd_pricing_clock_panel',render=lambda *a:'/tmp/clock')
mod('gd_unified_backend',layout=lambda *a:{'root':'/tmp/test_run_ui'})
def start(*a):
 COUNTS['start']+=1;STATE['exit']=None;return {'output':'test-out'}
mod('gd_full_day_ui',start_day=start,progress=lambda s:{'failed':bool(s.get('exit')) and s['exit'].get('status')!='complete_research'},render_status=lambda s:__import__('streamlit').write('TRACKED '+str(s.get('completed_windows'))))
SCRIPT="import gd_provincial_page;gd_provincial_page.render()"
a=AppTest.from_string(SCRIPT).run()
assert not a.exception
# Create current same-input DA and active RT state after first identity established.
a.session_state['prov_ready']={'da':'/tmp/testDA','run':'/tmp/testRT'}
a.session_state['prov_day']={'output':'test-out'}
a.run();assert not a.exception
assert a.button(key='prov_prepare').disabled
# Edit next-run draft while background execution ongoing.
next(x for x in a.number_input if x.label=='全网负荷倍率').set_value(1.1).run()
assert not a.exception
assert a.session_state['prov_day']['output']=='test-out'
assert 'prov_ready' not in a.session_state
assert a.button(key='prov_prepare').disabled
assert any('TRACKED' in x.value for x in a.markdown)
assert COUNTS=={'prepare':0,'start':0}
# Repeated reruns do not start another computation.
a.run();a.run();assert COUNTS=={'prepare':0,'start':0}
# Unknown state keeps handle and does not allow duplicate start.
STATE['raise']=True;a.run();assert not a.exception
assert a.button(key='prov_prepare').disabled and a.session_state['prov_day']['output']=='test-out'
STATE.pop('raise');STATE['exit']={'status':'failed','error':'test failure'};a.run()
assert not a.button(key='prov_prepare').disabled
# Fresh DA successful after failure clears old run only on success.
a.button(key='prov_prepare').click().run();assert not a.exception
assert COUNTS['prepare']==1 and 'prov_day' not in a.session_state
# Start once, re-render must hide restart until exit.
a.button(key='prov_rt_start').click().run();assert not a.exception
assert COUNTS['start']==1 and a.session_state['prov_day']['output']=='test-out'
a.run();assert COUNTS['start']==1
assert not any(b.key=='prov_rt_start' for b in a.button)
# Completed task allows new DA and retains displayed previous results until then.
STATE['exit']={'status':'complete_research'};a.run();assert not a.exception
assert not a.button(key='prov_prepare').disabled
assert a.session_state['prov_day']['output']=='test-out'
print(json.dumps({'passed':True,'scope':'actual page Streamlit AppTest; backend lifecycle stubbed; no market solve','checks':['edit while running','no orphaned handle','duplicate start blocked','unknown status blocked','failure retry','completed run preserved'],'counts':COUNTS}))
