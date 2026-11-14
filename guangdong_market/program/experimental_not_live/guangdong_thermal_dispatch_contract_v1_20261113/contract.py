"""Fail-closed research thermal boundary compiler. No solves or dispatch authority."""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json,math

def need(ok,msg):
 if not ok: raise ValueError(msg)
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def clock(x):
 d=datetime.fromisoformat(x);need(d.utcoffset()==timedelta(hours=8),'explicit +08 clock');return d
def tick(x,origin):
 v=(clock(x)-clock(origin)).total_seconds()/300;need(v.is_integer(),'5 minute aligned clock');return int(v)
KINDS={'commercial_DA','reliability_plan','must_on','must_off','issued_instruction','actual_curve','forecast_curve','protection','permission'}
FIELDS={'online','start','stop','power'}

def compile_contract(doc,*,cutoff,begin,cells,units,expected_sha256):
 need(digest(doc)==expected_sha256,'contract identity');need(doc['schema']=='gd.thermal.dispatch.contract.v1','schema')
 need(doc['mode'] in ('research','formal'),'mode');need(doc['run_id'] and doc['origin'],'run clock')
 if doc['mode']=='formal':raise ValueError('FORMAL_BLOCKED: verified operator-data/authority adapter not implemented; research approval cannot substitute')
 now=tick(cutoff,doc['origin']);start=tick(begin,doc['origin']);need(start>=now and type(cells)is int and cells>0,'horizon')
 records=doc['records'];need(len({r['id'] for r in records})==len(records),'duplicate id')
 selected={};all_records={};series_kinds={}
 for r in records:
  need(r['kind'] in KINDS and r['unit'] in units,'kind/unit');need(type(r['version'])is int and r['version']>0,'version')
  a,b=tick(r['valid_from'],doc['origin']),tick(r['valid_to'],doc['origin']);available=tick(r['available_at'],doc['origin']);need(a<b,'empty/reversed interval')
  need(r['source']['id'] and len(r['source']['sha256'])==64 and all(c in '0123456789abcdef'for c in r['source']['sha256']),'source identity');need(r['issuer'] and r['approval']=='research_declared','explicit research approval')
  key=(r['unit'],r['series']);need(r['series'],'series identity');need(key not in series_kinds or series_kinds[key]==r['kind'],'series kind cannot change');series_kinds[key]=r['kind'];need(key not in all_records or r['version'] not in all_records[key],'duplicate series version');all_records.setdefault(key,set()).add(r['version'])
  need(r['scope'] in ('candidate','publication','reference'),'scope')
  if available<=now:
   old=selected.get(key)
   if old is None or r['version']>old['version']:selected[key]=r
 # Issued/actual are immutable, not silently replaced by a newer forecast/version.
 for r in records:
  if r['kind'] in ('issued_instruction','actual_curve') and tick(r['available_at'],doc['origin'])<=now:
   need(selected[(r['unit'],r['series'])]['id']==r['id'],'immutable issued/actual version superseded')
 out={u:[{} for _ in range(cells)]for u in units};obligations=[];refs=[];permissions=[]
 def fix(u,t,f,v,reason):
  if not start<=t<start+cells:return
  need(f in FIELDS and type(v)in(int,float) and math.isfinite(v),'constraint field/value')
  if f!='power':need(v in (0,1),'binary value')
  z=out[u][t-start];need(f not in z or abs(z[f]['value']-v)<=1e-8,'conflicting hard boundaries')
  z.setdefault(f,{'value':v,'record_ids':[]})['record_ids'].append(reason)
 for r in selected.values():
  a,b=tick(r['valid_from'],doc['origin']),tick(r['valid_to'],doc['origin']);kind=r['kind'];u=r['unit']
  if kind=='permission':
   need(r['scope']=='publication' and r['reason'] and r['target_ids'] and r['fields'] and set(r['fields'])<=FIELDS,'permission scope');need(set(r['target_ids'])<={x['id']for x in records if x['unit']==u and x['kind'] in ('commercial_DA','protection')},'permission target')
   need(r['safety_exception'] in (True,False),'exception flag');permissions.append(r);continue
  if kind in ('commercial_DA','reliability_plan','forecast_curve'):
   need(r['scope']=='reference','reference cannot silently become hard plan');refs.append(r)
   if kind=='commercial_DA':obligations.append(r)
   continue
  if kind=='protection':
   need(r['scope']=='publication' and isinstance(r['fields'],dict) and set(r['fields'])<=FIELDS and r['fields'],'protection fields');need(all(type(v)in(int,float) and math.isfinite(v) and (f=='power' or v in (0,1))for f,v in r['fields'].items()),'protection values');obligations.append(r);continue
  need(r['scope']=='candidate','hard boundary scope')
  if kind in ('must_on','must_off'):
   for t in range(max(a,start),min(b,start+cells)):
    fix(u,t,'online',int(kind=='must_on'),r['id'])
    fix(u,t,'stop' if kind=='must_on' else 'start',0,r['id'])
  else:
   need(len(r['rows'])==b-a,'curve interval coverage')
   if kind=='actual_curve':need(b<=now,'future cannot be actual')
   for j,row in enumerate(r['rows']):
    need(set(row)<=FIELDS and row,'row fields')
    for f,v in row.items():fix(u,a+j,f,v,r['id'])
 # References have explicit per-cell values, never any(day) propagation.
 for r in refs+obligations:
  if r['kind']=='protection':continue
  a,b=tick(r['valid_from'],doc['origin']),tick(r['valid_to'],doc['origin']);need(len(r['rows'])==b-a,'reference interval coverage')
  for row in r['rows']:
   need(row and set(row)<=FIELDS,'reference fields')
   for f,v in row.items():need(type(v)in(int,float) and math.isfinite(v) and (f=='power' or v in (0,1)),'reference values')
 z=dict(schema='gd.thermal.compiled.boundaries.v1',run_id=doc['run_id'],origin=doc['origin'],begin_tick=start,cutoff_tick=now,cells=cells,units=list(units),fixes=out,references=refs,publication_obligations=obligations,permissions=permissions,selected_ids=[r['id']for r in selected.values()],contract_sha256=expected_sha256,formal=False,native_calls=0,operator_authority_verified=False)
 z['compiled_sha256']=digest(z);return z

def valid(compiled):need(digest({k:v for k,v in compiled.items()if k!='compiled_sha256'})==compiled['compiled_sha256'],'compiled identity')
def check_publication(compiled,proposal):
 """Research proposal only. Permissions never erase immutable model boundaries."""
 valid(compiled);need(set(proposal)==set(compiled['units']),'proposal units');violations=[];start=compiled['begin_tick']
 for u,rows in proposal.items():
  need(len(rows)==compiled['cells'],'proposal length')
  for j,row in enumerate(rows):
   need(set(row)==FIELDS,'proposal full binary/power state')
   for f,v in row.items():need(type(v)in(int,float) and math.isfinite(v) and (f=='power' or v in (0,1)),'proposal values')
   for f,z in compiled['fixes'][u][j].items():need(abs(row[f]-z['value'])<=1e-7,'proposal violates hard boundary')
 for r in compiled['publication_obligations']:
  a,b=tick(r['valid_from'],compiled['origin']),tick(r['valid_to'],compiled['origin']);u=r['unit']
  for t in range(max(start,a),min(start+compiled['cells'],b)):
   row=proposal[u][t-start]
   changes=[]
   if r['kind']=='commercial_DA':
    if r['rows'][t-a].get('online')==1 and (row['online']==0 or row['stop']==1):changes=['online','stop']
   else:changes=[f for f,v in r['fields'].items()if abs(row[f]-v)>1e-7]
   for f in changes:
    permitted=any(p['unit']==u and r['id'] in p['target_ids'] and f in p['fields'] and p['safety_exception'] is True and tick(p['valid_from'],compiled['origin'])<=t<tick(p['valid_to'],compiled['origin']) for p in compiled['permissions'])
    if not permitted:violations.append(dict(unit=u,tick=t,field=f,record=r['id']))
 return dict(research_publication_eligible=not violations,violations=violations,formal=False,actual_dispatch_authorized=False)

def apply_to_model(model,compiled):
 """Add constraints to existing free-SCUC model BEFORE serialization. Never remove its conservative restrictions."""
 import pyomo.environ as p
 valid(compiled);need(set(model.G)==set(compiled['units']) and len(model.T)==compiled['cells'],'builder domain mismatch');need(not hasattr(model,'dispatch_contract_rows'),'duplicate application')
 model.dispatch_contract_rows=p.ConstraintList()
 for u,rows in compiled['fixes'].items():
  for j,row in enumerate(rows):
   for f,z in row.items():model.dispatch_contract_rows.add(getattr(model,f)[u,j]==z['value'])
 return {'rows':len(model.dispatch_contract_rows),'native_calls':0,'existing_protection_removed':False}

def bind_notice_carry(compiled,connected,ledger):
 """Accept already validated bridge/carry output, recheck pins and chronology; no issuance."""
 valid(compiled);need(ledger['run_id']==compiled['run_id'] and ledger['origin']==compiled['origin'],'ledger run/clock');need(ledger['cutoff_tick']<=compiled['cutoff_tick'],'future ledger')
 b=deepcopy(connected['short_boundary']);need(b['notice_ledger_sha256']==digest(ledger),'ledger binding');s=connected['carry']['predicted_issued_boundary'];need(s['tick']==compiled['begin_tick'],'carry delivery tick');need(tick(b['thermal'][s['unit']]['available_at'],compiled['origin'])<=compiled['cutoff_tick'],'future boundary')
 for r in ledger['receipts'].values():need(r['receipt_hash']==digest({k:v for k,v in r.items()if k!='receipt_hash'}),'receipt altered')
 b['dispatch_contract_sha256']=compiled['compiled_sha256'];return b

def issued_records_from_ledger(ledger):
 """Research receipts -> immutable mean-power/connection/event locks; pending excluded."""
 need(ledger['formal'] is False and ledger['actual_notice_claimed'] is False,'research ledger only')
 origin=clock(ledger['origin']);at=lambda t:(origin+timedelta(minutes=5*t)).isoformat();out=[]
 for e in ledger['receipts'].values():
  need(e['receipt_hash']==digest({k:v for k,v in e.items()if k!='receipt_hash'}),'receipt altered')
  need(e['run_id']==ledger['run_id'] and e['origin']==ledger['origin'] and e['issued_tick']<=ledger['cutoff_tick'],'receipt provenance/clock')
  out.append(dict(id=e['event_id'],series=e['event_id'],version=1,unit=e['unit'],kind='issued_instruction',valid_from=at(e['begin_tick']),valid_to=at(e['begin_tick']+len(e['rows'])),available_at=at(e['issued_tick']),source={'id':'simulated notice '+e['event_id'],'sha256':e['receipt_hash']},issuer='research notice bridge, not dispatch operator',approval='research_declared',scope='candidate',rows=[dict(online=1,start=int(j==0 and e['phase']=='startup'),stop=int(j==0 and e['phase']=='shutdown'),power=r['mean'])for j,r in enumerate(e['rows'])]))
 return out
