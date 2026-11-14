"""Read-only evidence viewer. Never grants operating or billing authority."""
from pathlib import Path
import json,hashlib,argparse
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def status(run,ac_plan=None,ac_result=None,*,report_path=None):
 root=Path(run).resolve();r=read(root/'RUN.json');rid=r['run_id'];evidence=[]
 def load(p):
  p=Path(p);d=read(p);evidence.append(dict(path=str(p),sha256=sha(p)));return d
 def ref(z):
  p=(root/z['path']).resolve();need(p.is_relative_to(root)and sha(p)==z['sha256'],'run reference mismatch');return p
 a=load(root/'ARTIFACTS.json')if(root/'ARTIFACTS.json').exists()else{}
 need(not a or a['run_id']==rid,'DA run mismatch')
 candidates=[root/'rt/RUN_RESULT.json']if(root/'rt/RUN_RESULT.json').exists()else sorted((root/'rt').glob('PARTIAL_W*.json'),key=lambda p:int(p.stem.split('W')[-1]),reverse=True)
 if report_path is not None:
  rp=Path(report_path).resolve();need(rp.parent==root/'rt','report outside run');candidates=[rp]
 rt=load(candidates[0])if candidates else{}
 report=rt.get('report',{});n=0
 if rt:
  from datetime import datetime,timedelta
  source=load(root/'rt/SOURCE_CONTRACT.json');identity=load(root/'rt/SOURCE_IDENTITY.json')
  need(report['branch_id']==rid+'-RT'and rt['DA_source_identity']==a['source_identity']and rt['RT_source_identity_sha256']==identity['sha256'],'execution receipt source mismatch')
  need(source['DA_pipeline_run_id']==rid and source['DA_receipt_sha256']==sha(root/'ARTIFACTS.json'),'RT DA binding')
  n=report['accepted_RT_windows'];executed=report['executed_RT_cells'];issued=report['issued_RT_cells']
  need(type(n)is int and 1<=n<=96 and issued==3*n and 0<=executed<=issued,'execution counts')
  if report['same_controller_full96_complete']:
   need(n==96 and executed==288 and load(root/'rt/D96_DRAIN.json')['report']==report,'full drain missing')
  else:
   folder=root/f'rt/W{n-1}';c=load(folder/'accepted/COMPLETE.json');fp=folder/'accepted/STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json'
   need(c['window']==n-1 and c['DA_receipt_sha256']==sha(root/'ARTIFACTS.json'),'partial completion identity')
   for f,h in c['files'].items():
    path=(folder/f).resolve();need(path.is_relative_to(folder)and sha(path)==h,'partial COMPLETE hash')
   facts=load(fp);need(facts['report']==report and facts['receipt']['at']==report['receipt_at'],'partial execution receipt')
  need(datetime.fromisoformat(report['receipt_at'])==datetime.fromisoformat(source['day_start'])+timedelta(minutes=5*executed),'execution clock mismatch')
 count=0
 # Count only accepted independent PRICE for the selected execution boundary.
 for w in range(n):
  folder=root/f'rt_price/W{w}';pp=folder/'PRICE_RECEIPT.json'
  if not pp.exists():continue
  pr=load(pp);need(pr['window']==w and pr['run_id']==rid+'-RT'and pr['source_contract_sha256']==sha(root/'rt/SOURCE_CONTRACT.json'),'PRICE source/run/window mismatch')
  need(pr['status']=='verified_research_raw_price'and pr['KKT']['passed']is True and pr['RT_dual_used']is False,'PRICE not independently accepted')
  need(sha(folder/'native/response.json')==pr['response_sha256']and sha(folder/'prepared/PREPARATION.json')==pr['preparation_sha256'],'PRICE raw/preparation hash')
  for f,h in pr['raw_hashes'].items():
   path=(folder/'native'/f).resolve();need(path.is_relative_to(folder/'native')and sha(path)==h,'PRICE raw hash mismatch')
  kr=load(folder/'KKT_PRICE_REVIEW.json');need(sha(folder/'KKT_PRICE_REVIEW.json')==pr['KKT_review_sha256']and kr['first_quarter_research_nodal_price_yuan_mwh']==pr['raw_prices'],'PRICE KKT binding')
  count+=1
 settle=load(root/'settlement/RESULT.json')if report.get('same_controller_full96_complete')and(root/'settlement/RESULT.json').exists()else{}
 if settle:need(settle['fresh_execution_receipt']['DA_run_id']==rid and settle['fresh_execution_receipt']['DA_artifacts_sha256']==sha(root/'ARTIFACTS.json'),'settlement run mismatch')
 ac=dict(status='not_checked',checked_cells=[],formal_security_acceptance=False)
 need(bool(ac_plan)==bool(ac_result),'provide both AC plan and result')
 if ac_plan:
  plan=load(ac_plan);out=load(ac_result)
  need(plan['schema']=='gd.pipeline.fresh_executed_to_AC.v1'and plan['run_id']==rid and Path(plan['run_root']).resolve()==root,'AC belongs to another run')
  need(out['plan_sha256']==sha(ac_plan),'AC plan hash mismatch')
  need(plan['source_pins'].get(str(root/'ARTIFACTS.json'))==sha(root/'ARTIFACTS.json'),'AC DA binding missing')
  need(plan['source_pins'].get(str(root/'rt/D96_DRAIN.json'))==sha(root/'rt/D96_DRAIN.json'),'AC executed state binding missing')
  for p,h in plan['source_pins'].items():need(sha(p)==h,'AC source hash mismatch: '+p)
  items={z['index']:z for z in plan['items']};need(len(items)==len(plan['items'])==len(out['results']),'AC cell population mismatch')
  unsafe=False
  for z in out['results']:
   need(z['index']in items,'unexpected AC cell');i=items.pop(z['index']);cell=load(i['path']);need(sha(i['path'])==i['sha256']==z['prepared_sha256']and cell['global_cell_index']==z['index'],'AC cell hash/index mismatch')
   need(hashlib.sha256(json.dumps(cell['ac_input'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()==z['AC']['input_sha256'],'AC input mismatch')
   unsafe|=z['AC'].get('ac_snapshot_safe')is not True
   ac['checked_cells'].append(dict(index=z['index'],start=cell['start'],status=z['AC']['status'],safe=z['AC'].get('ac_snapshot_safe'),prepared_sha256=i['sha256']))
  ac['status']='research_snapshot_unsafe'if unsafe else 'research_sample_checked'
 rows=settle.get('row_count',len(settle.get('rows',[])))
 complete=a.get('status')=='accepted'and report.get('executed_RT_cells')==288 and count==96 and bool(settle)
 unknown=[]
 for p in root.rglob('request.json'):
  if p.parent.name.startswith('native'):
   try:
    response=read(p.parent/'response.json')
    if 'status'not in response:unknown.append(str(p.parent))
   except (OSError,ValueError):unknown.append(str(p.parent))
 return dict(blocking_unknown_attempt=bool(unknown),unknown_attempts=unknown,schema='gd.delivery.user_status.v1',run_id=rid,energy=dict(status='research_energy_chain_complete'if complete else'pending',rt_executed_cells=report.get('executed_RT_cells',0),price_windows=count,settlement_rows=rows),ac=ac,formal_bill=None,evidence=evidence,warnings=['研究能源链完成不等于交流安全通过或正式账单','AC 仅显式所列时格研究快照，不是全日/N-1/动态安全认证','历史 accepted 结果只读展示，不以新 worker 替换其 pins'])
def main():
 p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--ac-plan');p.add_argument('--ac-result');a=p.parse_args();print(json.dumps(status(a.run,a.ac_plan,a.ac_result),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
