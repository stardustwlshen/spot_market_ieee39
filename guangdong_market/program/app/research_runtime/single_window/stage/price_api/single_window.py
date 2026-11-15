"""Research planning façade: distinct near execution source/far prediction/PRICE.
No _finish, publication, execution, accounting or implicit native authorization.
"""
from pathlib import Path
from copy import deepcopy
import sys,json,hashlib
from single_price import prepare_single_quarter,execute_single_quarter
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def reuse_far_source(*,host,path,near_prepared,near_actual):
 host.session.verify_sources();f=Path(path).resolve();z=read(f);need(z['schema']=='gd.predicted.far_stage.accepted.v1' and z['execution_allowed']is False,'predicted far acceptance only')
 for p,h in z['pins'].items():need(sha(p)==h,'far source drift '+p)
 pred=z['prediction'];need(sha(pred['path'])==pred['sha256'],'prediction source drift');r=read(pred['path'])
 for p,h in r['pins'].items():need(sha(p)==h,'prediction upstream drift '+p)
 np=Path(near_prepared).resolve();na=Path(near_actual).resolve();need(r['origin_prepared']==str(np),'far belongs to different near')
 bundle,bh=host.session.export_generated_pair(np,na,host.hooks.prepared[str(np)])
 need(r['host_run_id']==host.session.registry['run_id']and r['accepted_bundle_sha256']==bh,'far/near/session mismatch')
 from v6_host import e
 worker=host.session.program.engine.worker;a=f.parent/'far/actual';n=read(f.parent/'far/prepared/names.json')
 rq=lambda k:worker.load(read(a/k/'JOB.json')['request'])
 rs=lambda k:worker.load(read(a/k/'COMPLETED.json')['response'])
 check=e.verify_pair(rq('SCUC'),rs('SCUC'),rq('SCED'),rs('SCED'),n)
 need(check['native_dual_passed']is True,'far pair not accepted')
 return dict(status='far_raw_reused',acceptance_path=str(f),sha256=sha(f),new_native_calls=0,near_bundle_sha256=bh)
def run_single_window(*,program_root,host,near_prepared,near_actual,base_run,selected_DA,research_agc_input,output,mode='prepare',execute_far=False,execute_price=False,reuse_far=None):
 need(mode in ('prepare','research_native'),'explicit mode');need(mode!='research_native'or execute_price is True,'PRICE explicit permission required')
 out=Path(output).resolve();need(not out.exists(),'fresh window output only');out.mkdir(parents=True)
 before=(deepcopy(host.hooks.carries),deepcopy(host.hooks.ledgers),host.window,str(host.cp),host.lid)
 try:
  if reuse_far is not None:far=reuse_far_source(host=host,path=reuse_far,near_prepared=near_prepared,near_actual=near_actual)
  else:
   sys.path.insert(0,str(Path(__file__).resolve().parent.parent));from window_api_corrected import continue_window
   far=continue_window(program_root=program_root,host=host,near_prepared=near_prepared,near_actual=near_actual,research_agc_input=research_agc_input,output=out/'far_window',mode=mode,execute_far=execute_far)
  price=prepare_single_quarter(program_root=program_root,session=host.session,hooks=host.hooks,near_prepared=near_prepared,near_actual=near_actual,base_run=base_run,selected_DA=selected_DA,output=out/'price')
  if mode=='research_native':price=execute_single_quarter(session=host.session,prepared=out/'price',output=out/'price_actual',execute_native=execute_price)
  result=dict(status='research_window_'+('prepared'if mode=='prepare'else'accepted'),far=far,price=price,near_source=str(Path(near_prepared).resolve()),far_does_not_replace_near=True,execution=False,publication=False,settlement=False,full_window_AC=False,price_quality=price.get('price_quality',dict(has_nonzero_relaxation=None,shortage_mw=None,execution_allowed=False,formal_price_usable=False)),execution_allowed=False,formal_price_usable=False)
 finally:need(before==(host.hooks.carries,host.hooks.ledgers,host.window,str(host.cp),host.lid),'actual domain changed')
 (out/'RESULT.json').write_text(json.dumps(result,indent=2));return result
