"""Independent raw PRICE acceptance without launching any solver."""
from pathlib import Path
from domain import read,write,need,sha
from evidence import optimal
from scoped_kkt import metrics

def nodal_prices(q,r,n,tick=0,duration_h=.25):
 """Load sensitivity = injection row coefficient * RHS dual / hours.
 Pyomo may orient different balance equalities in opposite directions.
 VRE is a named +1 physical injection at every node, including zero capacity.
 """
 names=n['variables'];out={};proof={}
 need(duration_h>0,'positive modeled interval')
 for i,name in enumerate(n['constraints']):
  if not name.startswith('balance[') or not name.endswith(','+str(tick)+']'):continue
  node=name.split('[',1)[1].split(',')[0].strip("'")
  row=q['rows'][i];need(row['lb']==row['ub'],'node balance equality required')
  anchors=[(names[j],float(c)) for j,c in row['terms'] if names[j].startswith('vre[') and names[j].split('[',1)[1].split(',')[0].strip("'")==node and names[j].endswith(','+str(tick)+']')]
  need(len(anchors)==1 and abs(anchors[0][1])==1,'unique unit physical injection anchor required')
  sign=anchors[0][1];out[node]=sign*float(r['dual'][i])/duration_h
  proof[node]=dict(balance_row=i,injection_anchor=anchors[0][0],load_rhs_derivative=sign,duration_h=duration_h,raw_rhs_dual=r['dual'][i])
 return out,proof

def review(prepared,output):
 p=Path(prepared).resolve();ready=read(p/'READY.json')
 for f,h in ready['artifact_pins'].items():need(sha(p/f)==h,'prepared artifact changed')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'prepared producer changed')
 d=p/'native_once';q=read(d/'request.json');r=read(d/'response.json');n=read(p/'names.json');id=read(d/'IDENTITY.json');done=read(d/'COMPLETED.json');ctx=read(p/'CONTEXT.json')
 need(done['returncode']==0 and done['response_sha256']==sha(d/'response.json'),'completed response');need(id['request_sha256']==sha(d/'request.json') and id['ready_sha256']==sha(p/'READY.json'),'same ready/request')
 need({k:v for k,v in q.items() if k!='deadline'}==read(p/'template.json'),'only native deadline differs');need(r.get('optimize_calls')==1,'one optimization');optimal(q,r,n)
 prices,sensitivity=nodal_prices(q,r,n)
 z=dict(schema='gd.independent_PRICE.accepted.v2',context=ctx,KKT=metrics(q,r,n,angle_tolerance=1e-8),raw_prices=prices,price_sensitivity=sensitivity,formal_bill=None,payable=False,safety_release=False,native_calls_this_review=0,source_pins={str(f):sha(f) for f in [p/'READY.json',p/'CONTEXT.json',p/'names.json',d/'request.json',d/'response.json',d/'IDENTITY.json',d/'COMPLETED.json',Path(__file__)]})
 need(len(z['raw_prices'])==39,'complete first-quarter node inventory');write(output,z);return {'accepted':True,'native_calls':0,'output':str(output)}
