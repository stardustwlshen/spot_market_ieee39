"""Independent raw PRICE acceptance without launching any solver."""
from pathlib import Path
from domain import read,write,need,sha
from evidence import optimal
from scoped_kkt import metrics

def review(prepared,output):
 p=Path(prepared).resolve();ready=read(p/'READY.json')
 for f,h in ready['artifact_pins'].items():need(sha(p/f)==h,'prepared artifact changed')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'prepared producer changed')
 d=p/'native_once';q=read(d/'request.json');r=read(d/'response.json');n=read(p/'names.json');id=read(d/'IDENTITY.json');done=read(d/'COMPLETED.json');ctx=read(p/'CONTEXT.json')
 need(done['returncode']==0 and done['response_sha256']==sha(d/'response.json'),'completed response');need(id['request_sha256']==sha(d/'request.json') and id['ready_sha256']==sha(p/'READY.json'),'same ready/request')
 need({k:v for k,v in q.items() if k!='deadline'}==read(p/'template.json'),'only native deadline differs');need(r.get('optimize_calls')==1,'one optimization');optimal(q,r,n)
 z=dict(schema='gd.independent_PRICE.accepted.v2',context=ctx,KKT=metrics(q,r,n,angle_tolerance=1e-8),raw_prices={s.split('[',1)[1].split(',')[0].strip("'"):v/.25 for s,v in zip(n['constraints'],r['dual']) if s.startswith('balance[') and s.endswith(',0]')},formal_bill=None,payable=False,safety_release=False,native_calls_this_review=0,source_pins={str(f):sha(f) for f in [p/'READY.json',p/'CONTEXT.json',p/'names.json',d/'request.json',d/'response.json',d/'IDENTITY.json',d/'COMPLETED.json',Path(__file__)]})
 need(len(z['raw_prices'])==39,'complete first-quarter node inventory');write(output,z);return {'accepted':True,'native_calls':0,'output':str(output)}
