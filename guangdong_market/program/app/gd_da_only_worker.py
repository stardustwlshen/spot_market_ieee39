"""Only fixed existing DA routines; subprocess isolated from UI/RT engines."""
from pathlib import Path
import sys,json
B=Path(__file__).resolve().parent
from gd_da_only import read,write,check,sha,CORE

def main(action,c,execute_native=False):
 c=Path(c);p=read(c/'PLAN.json');check(p);r=Path(p['run']);sys.path.insert(0,str(CORE.parent/'guangdong_pipeline_20261112'))
 from da_pipeline_v4 import init,prepare_scuc,verify_sources,checked_prepared
 if action=='prepare':
  z=p['scenario'];root=Path(z['directory']);entries=z['entries'];init(root/entries['bundle'],r,root/entries['pricing_spec'],root/entries['rt_assets']);q=prepare_scuc(r)
  # Assert copied case is exactly the selected scenario's bundle source.
  bundle=read(root/entries['bundle']);source=(root/entries['bundle']).parent/bundle['case_file']
  if sha(source)!=sha(r/'inputs/case.json'):raise ValueError('selected case mismatch')
  write(c/'PREPARED.json',dict(run_id=read(r/'RUN.json')['run_id'],scenario_identity=z['identity'],case_sha256=sha(source),native_calls=0,SCUC=q));return
 if not execute_native:raise ValueError('explicit native permission required')
 if(c/'EXIT.json').exists():raise ValueError('previous result/unknown cannot restart')
 verify_sources(r);checked_prepared(r,'da_scuc')
 from pipeline_da_checked import run_da
 try:
  result=run_da(r,True)
  write(c/'ACCEPTED.json',dict(run_id=result['run_id'],ARTIFACTS_sha256=sha(r/'ARTIFACTS.json'),scenario_identity=p['scenario']['identity'],case_sha256=sha(r/'inputs/case.json'),native_calls=3,publication_allowed=False,formal_bill=None,review='existing fixed-stage acceptance/artifacts; not formal DA98 release'))
  write(c/'EXIT.json',dict(status='accepted_research_DA',RT_calls=0))
 except Exception as exc:
  write(c/'EXIT.json',dict(status='failed_or_unknown',reason=str(exc),native_calls=None,automatic_retry=False));raise
if __name__=='__main__':main(sys.argv[1],sys.argv[2],'--execute-native'in sys.argv[3:])
