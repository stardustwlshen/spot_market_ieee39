"""Pure same-source W0 and boundary construction; no native calls."""
from pathlib import Path
import sys,json,hashlib
from datetime import datetime,timedelta
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(B),str(R/'guangdong_r46_curtailable_full_day_20261112'),str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src'),str(R/'guangdong_r53_same_source_da_chain_20261112')]
from r62smm import gd_r46_source_contract as source,gd_r46_day_session as session,gd_r46_day_models as models,highs_solver as hs
from r62smm.gd_r15_da_rt_bridge import canonical,digest,file_sha,require
from guard import attach,POLICY
OPTIONS=dict(threads=1,time_limit=120.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False)
def write(p,x):
 with Path(p).open('xb') as f:f.write(canonical(x))
def main():
 out=B/'prepared_v1';out.mkdir(exist_ok=False)
 c,p=source.load_contract(B/'SOURCE_CONFIG.json',policy=source.POLICY);write(out/'SOURCE_CONTRACT.json',c)
 print('new same-source contract loaded',flush=True)
 reports=[]
 for i in (0,88,95):
  with source.native_stage_scope(c,p,run_id='R62-PURE',index=i,stage='RT'):
   inp=session.pure_template_input(c,p,i);require(all(u['bid_proxy_yuan_mwh']==-50 for u in inp['renewable'].values()),'new bid propagation')
   m,a=models.build_rt_template(inp);removed=[]
   if datetime.fromisoformat(inp['window']['horizon_end'])!=datetime.fromisoformat(inp['day_start'])+timedelta(days=1):
    for r,row in m.rolling_terminal.items():removed.append(row.name);row.deactivate()
   g=attach(m,inp);q,vs,cs=hs.serialize_model(m,OPTIONS);q.update(dual=True,preparation_elapsed_s=0.,r62_contract_sha256=digest(c),r62_policy_sha256=digest(POLICY),r62_window=i)
  w=out/('W'+str(i));w.mkdir();names=dict(variables=[v.name for v in vs],constraints=[z.name for z in cs])
  for f,z in [('input.json',inp),('template.json',q),('names.json',names),('GUARD.json',g)]:write(w/f,z)
  ticket=dict(schema='gd.research.r62.new_DA98_RT.prepared.v1',window=i,source_contract_sha256=digest(c),source_contract_path=str((out/'SOURCE_CONTRACT.json').resolve()),artifact_hashes={f:file_sha(w/f) for f in ('input.json','template.json','names.json','GUARD.json')},actual_initial_seed=(i==0),boundary_fixture_uses_DA_state_not_actual_RT=(i!=0),native_launch_allowed_after_review=(i==0),native_calls=0,execution_authority=False,old_R60_or_R46_result_reused=False)
  write(w/'TICKET.json',ticket);reports.append(dict(window=i,variables=len(vs),rows=len(cs),guard_rows=g['added_rows'],removed_non_D96_rows=removed,ticket_sha256=file_sha(w/'TICKET.json')));print(reports[-1],flush=True)
 write(out/'RESULT.json',dict(status='pure_new_source_boundary_construction',reports=reports,native_calls=0,forecast_future_actual_used=False,publication_at=c['publication_at'],D_day_start=c['day_start'],noquote_bid=-50,curtailment_research=1000,financial_price_available=False,full_day_proven=False))
if __name__=='__main__':main()
