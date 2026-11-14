"""One fresh initialization + W0 build; never launch or accept any native event."""
from runtime import *
import json,time
start=time.perf_counter();run=FreshRun(B/'fresh_runtime_v2');init=time.perf_counter()-start
start=time.perf_counter();token,path,record=run.prepare(0);prep=time.perf_counter()-start
read=lambda p:json.loads(Path(p).read_bytes())
a=read(B/'prepared_v1/W0/template.json');b=read(path/'template.json')
for k in ('variables','rows','objective','options'):require(a[k]==b[k],'runtime/pure fixture math mismatch '+k)
rejections=[]
for name,fn in [('double_prepare',lambda:run.prepare(0)),('skip_next',lambda:run.prepare(1)),('foreign_ticket',lambda:run.accept(Ticket(),{}))]:
 try:fn()
 except ValueError:rejections.append(name)
 else:raise AssertionError(name)
result=dict(native_calls=0,initial_source_load_count=1,init_elapsed_s=init,W0_prepare_elapsed_s=prep,math_exactly_equals_pure_W0=True,rejections=rejections,ticket_sha256=file_sha(path/'TICKET.json'),source_identity_sha256=run.code['sha256'],report=run.report())
write(B/'PURE_RUNTIME_RESULT.json',result);print(json.dumps(result))
