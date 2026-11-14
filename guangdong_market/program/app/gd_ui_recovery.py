"""Explicit one-shot precision recovery UI. Never changes main UI run lock."""
from pathlib import Path
import hashlib,json,subprocess,sys,os,uuid
B=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def pins(core,run):
 core=Path(core).resolve();run=Path(run).resolve()
 files=[core,run/'RUN.json',run/'da_scuc/ACCEPTANCE.json',run/'da_sced/native_v2/request.json',run/'da_sced/native_v2/response.json',run/'da_sced/native_v2/COMPLETED.json']
 controller=core.parent/'recovery_controller'
 if not(controller/'controller.py').is_file():raise ValueError('恢复controller未安装')
 files+=sorted(p for p in controller.rglob('*')if p.is_file()and '__pycache__'not in p.parts)
 return {str(p):sha(p)for p in files}
def preflight(core,run):
 before=pins(core,run)
 cmd=[sys.executable,'-B',str(Path(core).resolve()),'recovery','preflight','--run',str(Path(run).resolve())]
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
 if p.returncode:raise ValueError(p.stderr[-5000:]or p.stdout[-5000:])
 report=json.loads(p.stdout)
 if report.get('original_strict_KKT',{}).get('passed')is not False:raise ValueError('非已确认原strict KKT失败，不能恢复')
 if pins(core,run)!=before:raise ValueError('预检期间来源改变')
 return dict(schema='gd.ui.recovery_preflight.v1',run=str(Path(run).resolve()),core=str(Path(core).resolve()),source_pins=before,controller_preflight=report,native_calls=0)
def launch(core,run,proof,confirmed=False):
 if confirmed is not True:raise ValueError('需要明确确认单次恢复求解')
 if proof.get('run')!=str(Path(run).resolve())or proof.get('core')!=str(Path(core).resolve())or proof.get('source_pins')!=pins(core,run):raise ValueError('恢复预检已过期，来源或目标改变')
 # Controller remains authority: recompute eligibility and full source/model identity.
 current=preflight(core,run)
 if current!=proof:raise ValueError('恢复资格/原strict KKT变化，请重新预检')
 run=Path(run).resolve();control=run.parent/(run.name+'_recovery_ui_control');control.mkdir(parents=True,exist_ok=True)
 lock=control/'ONE_SHOT.json'
 # Never release this lock automatically, including success/failure/unknown exit.
 with lock.open('x')as f:json.dump(dict(run=str(run),proof=proof),f)
 attempt=control/uuid.uuid4().hex;attempt.mkdir()
 (attempt/'COMMAND.json').write_text(json.dumps([sys.executable,'-B',str(Path(core).resolve()),'recovery','perform','--run',str(run),'--execute-native']))
 (attempt/'PREFLIGHT.json').write_text(json.dumps(proof))
 with(attempt/'driver.log').open('x')as log:
  c=subprocess.Popen([sys.executable,'-B',str(B/'gd_ui_supervisor.py'),str(attempt)],stdout=log,stderr=log,start_new_session=True,env={**os.environ,'TZ':'UTC'})
 (attempt/'UI_LAUNCH.json').write_text(json.dumps(dict(pid=c.pid,automatic_retry=False,main_UI_lock_modified=False)))
 return dict(attempt=str(attempt),pid=c.pid,next_action='等待恢复收据。成功仅到SCED接受与PRICE纯准备；之后用正常resume，核心reconcile核旧锁。')
def render(core,run):
 import streamlit as st
 with st.expander('受限 SCED 数值恢复（独立单次操作，不自动重试）'):
  st.caption('仅已确认的原strict KKT数值失败可用。预检只读；确认后只新求解一次IPM/off并接受原严格KKT，不重复SCUC、不自动PRICE/RT。unknown或失败不自动重试，不改主UI锁。')
  if st.button('只读恢复资格预检',key='gd_recovery_preflight_button'):
   try:st.session_state['gd_recovery_proof']=preflight(core,run);st.json(st.session_state['gd_recovery_proof'])
   except Exception as e:st.session_state.pop('gd_recovery_proof',None);st.error(str(e))
  confirmed=st.checkbox('确认单次SCED恢复求解，保留全部失败原件',key='gd_recovery_confirm')
  if st.button('启动一次受限恢复',key='gd_recovery_launch',disabled=not confirmed or 'gd_recovery_proof'not in st.session_state):
   try:st.json(launch(core,run,st.session_state['gd_recovery_proof'],confirmed));st.session_state.pop('gd_recovery_proof',None)
   except Exception as e:st.error(str(e))
