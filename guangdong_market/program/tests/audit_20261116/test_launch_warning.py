from pathlib import Path
from streamlit.testing.v1 import AppTest
P=str(Path(__file__).resolve().parents[2]/'app')
for status in ['launch_unknown','started_record_warning']:
 script=f'''import sys,types
sys.path.insert(0,{P!r})
import streamlit as st
ctl=types.ModuleType('gd_live_control');ctl.status=lambda _:{{'exit':None}}
sys.modules['gd_live_control']=ctl
ui=types.ModuleType('gd_full_day_ui');ui.render_status=lambda _:st.write('状态仍可查看')
sys.modules['gd_full_day_ui']=ui
from gd_provincial_page import _render_tracked_day
st.session_state['prov_day']={{'output':'test','receipt':{{'status':{status!r},'warning':'启动确认异常，保留记录，禁止重复启动'}}}}
_render_tracked_day()
'''
 at=AppTest.from_string(script).run()
 assert not at.exception
 assert len(at.warning)==1 and '启动确认异常' in at.warning[0].value
 assert at.session_state['prov_day']['output']=='test'
print('PASS: launch_unknown and started_record_warning displayed; handle preserved; real page function, status/render stubs')
