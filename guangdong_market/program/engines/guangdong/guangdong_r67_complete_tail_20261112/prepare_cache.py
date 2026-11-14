"""One full-source construction/serialization; pure verification only."""
from common67 import *
from full_input import build_full_input
from full_model import build_full_template
from cache import TemplateCache
from collections import Counter
import time

def verify_local(full,names,local):
 m,_=local_models.build_rt_template(local)
 for row in m.rolling_terminal.values():row.deactivate()
 p,vs,cs=hs.serialize_model(m,OPTIONS);ln=dict(variables=[v.name for v in vs],constraints=[z.name for z in cs])
 def norm(row,ns):return (row['lb'],row['ub'],tuple(sorted((name(ns[j]),c)for j,c in row['terms'] if c)))
 fullrows=Counter(norm(r,names['variables'])for r in full['rows'])
 checked=0
 for label,row in zip(ln['constraints'],p['rows']):
  if label.startswith('daily_throughput['):continue # Replaced by complete same-date budget, not dropped.
  key=norm(row,ln['variables']);require(fullrows[key]>0,'local physical row absent '+label);fullrows[key]-=1;checked+=1
 fv={name(k):v for k,v in zip(names['variables'],full['variables'])}
 require(all(fv[name(k)]==v for k,v in zip(ln['variables'],p['variables'])),'local named column domains')
 current=TemplateCache(full,names).request(0);a={name(names['variables'][j]):c for j,c in current['objective']['terms']};z={name(ln['variables'][j]):c for j,c in p['objective']['terms']};require(a==z,'current24 objective exact')
 return dict(exact_matched_local_rows=checked,local_domains_exact=True,current24_cost_exact=True,local_daily_budget='implied by whole-date budget with nonnegative internal throughput; actual prefix fixed',rolling_endpoint='non-D96 removed per accepted research mapping',D96='hard global energy288')
def main():
 start=time.perf_counter();out=B/'prepared_v1';out.mkdir(exist_ok=False)
 plan,permit=source.load_contract(CONFIG,policy=source.POLICY);inp,local=build_full_input(plan,permit);m,a=build_full_template(inp);q,vs,cs=hs.serialize_model(m,OPTIONS);q.update(dual=True,preparation_elapsed_s=0.)
 names=dict(variables=[v.name for v in vs],constraints=[z.name for z in cs]);proof=verify_local(q,names,local)
 for f,x in [('SOURCE_CONTRACT.json',plan),('FULL_INPUT.json',inp),('LOCAL_W0_INPUT.json',local),('full_template.json',q),('names.json',names),('LOCAL_MAPPING.json',proof)]:write(out/f,x)
 meta=dict(schema='gd.research.r67.cached309.v1',native_calls=0,columns=len(vs),rows=len(cs),elapsed_s=time.perf_counter()-start,source_contract_sha256=digest(plan),artifact_hashes={f:file_sha(out/f)for f in ('SOURCE_CONTRACT.json','FULL_INPUT.json','LOCAL_W0_INPUT.json','full_template.json','names.json','LOCAL_MAPPING.json')},recursive_feasibility_conditions=['frozen prior forecasts','fixed UC/modes/qualification/network','zero tracking error','exact committed-prefix preservation; numeric acceptance at every step'],future_actual_used=False,DA_witness_verified=False,optimality_claim=False)
 write(out/'PREPARATION.json',meta);print(json.dumps(meta))
if __name__=='__main__':main()
