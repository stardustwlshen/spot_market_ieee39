"""One isolated fixed producer per subprocess. Never executes receipt-selected paths."""
from pathlib import Path
import sys,importlib.util,json
R=Path(__file__).resolve().parent.parent/'rt';E=R/'guangdong_rt_short_user_cli_v5_20261112';sys.path.insert(0,str(E))
s=importlib.util.spec_from_file_location('_registered_rt',E/'program.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
if sys.argv[1]=='registry':print(json.dumps(m.pins()));raise SystemExit
r=Path(sys.argv[1]).resolve();w=int(sys.argv[2]);mode=sys.argv[3];r,c=m.context(r)
if mode=='carry':
 if w<=95:cp=m.current_carry(r,c,w)
 else:
  m.need(w==96,'last handoff only');m.need(m.preflight(r,95)['status']=='accepted','accepted final pair')
  import tempfile
  cp=r/'handoff/W96/CARRY.json';h=m.read(cp.parent/'HANDOFF.json')
  m.need(h['window']==96 and h['context_sha256']==m.sha(r/'SHORT_CONTEXT.json') and h['carry_sha256']==m.sha(cp) and h['previous_acceptance_sha256']==m.sha(r/'W95/actual/ACCEPTED_PAIR.json'),'final handoff identity')
  with tempfile.TemporaryDirectory()as t:expected=m.engine.carry(r/'seed',r/'W95/prepared',r/'W95/actual',Path(t)/'carry.json')
  m.need(expected==m.read(cp),'final full carry replay')
 print(json.dumps(dict(status='accepted',carry=m.read(cp),native_calls=0)));raise SystemExit
z=m.preflight(r,w);m.need(z['status']=='accepted','actual accepted pair required');print(json.dumps(z))
