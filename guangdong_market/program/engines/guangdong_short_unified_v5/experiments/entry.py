"""Explicit research laboratory, never automatically applied to live RT or UI runs."""
from pathlib import Path
import sys,json,importlib.util
B=Path(__file__).resolve().parent
LIMITS={'formal':False,'safety_release':False,'automatic_RT_consumption':False,'native_calls':0,'source_authentication':'thermal plan uses fixed raw-pair verifier; AGC pure interface requires separately trusted host plan digest; boundary cells require caller physics/source verification','not_general_thermal_recommit':True,'shutdown_permission_importer':False,'AGC_followup':'new SCED, same-UC zero-AGC curtailment contrast and AC safety remain necessary'}
def module(kind,name):
 sys.path.insert(0,str(B/kind));s=importlib.util.spec_from_file_location('_experimental_'+name,B/kind/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('action',choices=['info','thermal-plan','thermal-publish','thermal-boundary','agc-produce','agc-project']);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path);p.add_argument('--explicit-research-laboratory',action='store_true');a=p.parse_args()
 if a.action=='info':print(json.dumps(dict(status='experimental_interfaces_not_live',**LIMITS)));raise SystemExit
 assert a.explicit_research_laboratory,'explicit research laboratory opt-in required'
 assert a.input and a.output and not a.output.exists(),'fresh input/output required'
 x=json.loads(a.input.read_bytes())
 if a.action.startswith('thermal'):
  m=module('thermal','notice_bridge');f={'thermal-plan':m.load_plan,'thermal-publish':m.publish_due,'thermal-boundary':m.connect_boundary}[a.action]
 else:m=module('agc','hourly');f={'agc-produce':m.produce,'agc-project':m.project}[a.action]
 z=f(**x)
 # Preserve the native helper result schema for explicit downstream use. This is
 # not a schedule, and this router has no RT build/launch/publication side effects.
 with a.output.open('x')as out:json.dump(z,out,indent=2,allow_nan=False)
 print(json.dumps(dict(status='experimental_output_created_not_consumed',output=str(a.output.resolve()),**LIMITS)))
