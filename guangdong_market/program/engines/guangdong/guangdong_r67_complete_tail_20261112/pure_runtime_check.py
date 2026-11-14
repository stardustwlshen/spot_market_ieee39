from runtime67 import *
import time
start=time.perf_counter();run=FreshRun(B/'pure_runtime_v3');initialized=time.perf_counter()-start
start=time.perf_counter();ticket,p,t=run.prepare(0);elapsed=time.perf_counter()-start
names,values=run.cache.local_view(0,run.previous_primal)
fullcells,terminal=reconstruct_solution_cells(run.full_input,run.cache.names,run.previous_primal)
localcells,_=reconstruct_solution_cells(read(p/'input.json'),names,values)
require(len(fullcells)==309 and len(localcells)==24,'pure original feasible witness geometry')
# Separate cache instance tests only; no fake acceptance or live history mutation.
test=TemplateCache(run.cache.template,run.cache.names);q0=test.request(0);test.commit_first_three(0,run.previous_primal);q1=test.request(1)
require(q0['rows'] is q1['rows'],'immutable rows reused')
for j,c in q1['objective']['terms']:require(3<=test.locations[j][1]<27,'only shifted24 objective')
require(all(q1['variables'][j]['lb']==q1['variables'][j]['ub']==v for j,v in test.fixed.items()),'fixed issued3 and E3 boundary')
negative=[]
for label,fn in [('skip_prefix',lambda:test.request(2)),('duplicate_prepare',lambda:run.prepare(0)),('foreign_ticket',lambda:run.accept(Ticket(),{}))]:
 try:fn()
 except ValueError:negative.append(label)
 else:raise AssertionError(label)
result=dict(native_calls=0,initialization_s=initialized,W0_prepare_s=elapsed,global_witness_geometry_cells=309,local_actual_schema_geometry_cells=24,updated_fixed_columns=len(test.fixed),negative_controls=negative,source_identity_sha256=run.code['sha256'],ticket_sha256=file_sha(p/'TICKET.json'),primal_witness_not_optimal=True,full_template_rebuilt_in_runtime=False)
write(B/'PURE_RUNTIME_RESULT_v3.json',result);print(json.dumps(result))
