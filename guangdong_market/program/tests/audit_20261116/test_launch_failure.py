import sys,tempfile,types,json,unittest,importlib.util,os
from pathlib import Path
from unittest.mock import patch
APP=Path(os.environ.get('GD_TEST_APP',str(Path(__file__).resolve().parents[2]/'app')));sys.path.insert(0,str(APP))
import gd_process_guard as guard

spec=importlib.util.spec_from_file_location('tested_control',Path(os.environ.get('GD_TEST_CONTROL',str(APP/'gd_live_control.py'))));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

def plan(out):return dict(da='da',run='run',output=str(out),research_event_simulation=True,counterfactual_continue=False,ac_once=False,event_price_profile=True)
class Tests(unittest.TestCase):
 def setUp(self):
  self.modules=patch.dict(sys.modules,{'gd_resume_control':types.SimpleNamespace(acquire=lambda out:guard.acquire(out/'PROCESS.guard'))});self.modules.start();self.addCleanup(self.modules.stop)
 def test_popen_failure_closes_lock_and_marks_failure(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'run';p=plan(out);handles=[]
   def acquire(out):f=guard.acquire(out/'PROCESS.guard');handles.append(f);return f
   with patch.object(c,'plan',return_value=p),patch('gd_resume_control.acquire',acquire),patch.object(c.subprocess,'Popen',side_effect=OSError('spawn failed')):
    with self.assertRaises(OSError):c.start(p,confirmed=True)
   self.assertTrue(handles[0].closed);self.assertEqual(json.loads((out/'EXIT.json').read_text())['status'],'launch_failed')
   lock=guard.acquire(out/'PROCESS.guard');lock.close()
 def test_pid_write_failure_keeps_started_receipt(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'run';p=plan(out);original=c.write
   def write(path,value):
    if Path(path).name=='PID.json':raise OSError('disk write failed')
    original(path,value)
   with patch.object(c,'plan',return_value=p),patch.object(c,'write',write),patch.object(c.subprocess,'Popen',return_value=types.SimpleNamespace(pid=54321)):
    result=c.start(p,confirmed=True)
   self.assertEqual(result['status'],'started_record_warning');self.assertEqual(result['output'],str(out));self.assertFalse((out/'EXIT.json').exists())
 def test_windows_handshake_unknown_retains_output(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'run';p=plan(out)
   fake_os=types.SimpleNamespace(name='nt',environ={})
   with patch.object(c,'plan',return_value=p),patch.object(c,'os',fake_os),patch.object(guard,'launch_windows',side_effect=RuntimeError('handshake timeout')):
    result=c.start(p,confirmed=True)
   self.assertEqual(result['status'],'launch_unknown');self.assertEqual(result['output'],str(out));self.assertFalse((out/'EXIT.json').exists())
   self.assertIsNone(json.loads((out/'LAUNCH_ERROR.json').read_text())['native_calls'])
 def test_start_day_propagates_started_handle(self):
  import gd_full_day_ui
  receipt={'status':'started_record_warning','output':'saved-run','pid':123,'warning':'write failure'}
  control=types.SimpleNamespace(plan=lambda *a,**kw:{'output':'saved-run'},start=lambda *a,**kw:receipt)
  r=gd_full_day_ui.start_day({'da':'da','run':'rt'},'clock',.1,control)
  self.assertEqual(r['output'],'saved-run');self.assertEqual(r['receipt'],receipt)
 @unittest.skipUnless(os.name=='posix','inherited descriptor test is POSIX-only')
 def test_real_child_retains_lock_after_pid_write_failure(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'run';p=plan(out);original=c.write;spawn=c.subprocess.Popen;children=[]
   def write(path,value):
    if Path(path).name=='PID.json':raise OSError('simulated bookkeeping failure')
    original(path,value)
   def actual_spawn(cmd,**kwargs):
    proc=spawn([sys.executable,'-c','import time;time.sleep(10)'],**kwargs);children.append(proc);return proc
   try:
    with patch.object(c,'plan',return_value=p),patch.object(c,'write',write),patch.object(c.subprocess,'Popen',side_effect=actual_spawn):
     result=c.start(p,confirmed=True)
    self.assertEqual(result['status'],'started_record_warning')
    self.assertIsNone(children[0].poll())
    with self.assertRaises(guard.GuardBusy):guard.acquire(out/'PROCESS.guard')
    self.assertFalse((out/'EXIT.json').exists())
   finally:
    for proc in children:proc.terminate();proc.wait(timeout=5)
   lock=guard.acquire(out/'PROCESS.guard');lock.close()
if __name__=='__main__':unittest.main()
