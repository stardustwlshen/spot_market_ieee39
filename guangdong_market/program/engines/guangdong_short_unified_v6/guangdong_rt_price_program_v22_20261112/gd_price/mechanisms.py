"""PRICE-only sourced penalty interfaces and serialized slack transformation."""
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import json,hashlib,math

def need(x,m):
 if not x:raise ValueError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def validate_parameters(p,as_of):
 need(p.get('schema')=='gd.PRICE.penalties.v1'and p.get('mode')in('research','formal'),'explicit parameter mode')
 now=datetime.fromisoformat(as_of);need(now.tzinfo is not None,'aware parameter cutoff')
 need(set(p['factors'])=={'load','accident','network'},'ML prime/MR prime/M1 prime exact inventory')
 for name,z in p['factors'].items():
  need(type(z['value'])in(int,float)and math.isfinite(z['value'])and z['value']>0,'positive finite penalty '+name)
  need(z['unit']=='yuan_per_MWh'and z['meaning']=={'load':'ML_prime','accident':'MR_prime','network':'M1_prime'}[name],'penalty units/meaning')
  source=z['source'];need(source['document']and source['sha256']and source['locator']and source['grade']in('explicit_research_assumption','official_parameter_document'),'sourced factor '+name);need(sha(source['document'])==source['sha256'],'factor source bytes')
  begin=datetime.fromisoformat(z['effective_from']);end=datetime.fromisoformat(z['effective_to']);available=datetime.fromisoformat(source['available_at']);need(begin.tzinfo is not None and end.tzinfo is not None and available.tzinfo is not None and begin<=now<end and available<=now,'factor not effective/available')
  if p['mode']=='formal':need(source['grade']=='official_parameter_document'and source.get('official_url')and source.get('verification_receipt'),'formal missing independently verified current parameter evidence')
 allocation=p['balance_allocation'];need(allocation['basis']and allocation['source']and allocation['officially_certified']is False,'allocation explicit noncertified interface; official location mapping not yet supplied')
 # Current adapter never promotes supplied provenance labels to a complete formal market certificate.
 return {k:z['value']for k,z in p['factors'].items()}

def soften(base,names,mapping,parameters,*,as_of):
 """Mapping explicitly names every balance, accident, line and supported section.
 Original resource bounds/qualification rows and positive reserve remain untouched.
 """
 rates=validate_parameters(parameters,as_of);q=deepcopy(base);n=deepcopy(names)
 need(len(q['variables'])==len(n['variables'])and len(set(n['variables']))==len(n['variables']),'column inventory');need(len(q['rows'])==len(n['constraints'])and len(set(n['constraints']))==len(n['constraints']),'row inventory');need(q['objective']['minimize']is True and all(v['kind']=='C'for v in q['variables']),'independent PRICE continuous minimization only')
 need(mapping['base_template_sha256']==digest(base)and mapping['base_names_sha256']==digest(names),'exact base binding');need(mapping['schema']=='gd.PRICE.slack_mapping.v1','mapping schema')
 vi={s:i for i,s in enumerate(n['variables'])};ri={s:i for i,s in enumerate(n['constraints'])};seen_rows=set();slacks=[];edits=[]
 def slack(name,rate,h):
  need(name not in vi and type(h)in(int,float)and math.isfinite(h)and h>0,'unique slack / positive hours');j=len(q['variables']);q['variables'].append(dict(lb=0.,ub=None,kind='C',start=None));n['variables'].append(name);vi[name]=j;q['objective']['terms'].append([j,rate*h]);slacks.append(dict(name=name,column=j,penalty_yuan_MWh=rate,objective_hours=h));return j
 def row(name):
  need(name in ri and name not in seen_rows,'missing/duplicate mechanism row '+name);seen_rows.add(name);return q['rows'][ri[name]]
 load_rows=set()
 for z in mapping['balance']:
  t=z['period'];h=z['duration_h'];weights=z['rows'];need(weights and all(type(w)in(int,float)and math.isfinite(w)and w>=0 for w in weights.values())and abs(sum(weights.values())-1)<1e-12,'one explicit system slack allocation sums1')
  plus=slack(f'PRICE_load_surplus[{t}]',rates['load'],h);minus=slack(f'PRICE_load_shortage[{t}]',rates['load'],h)
  for name,w in weights.items():
   r=row(name);need(r['lb']is not None and r['lb']==r['ub'],'load balance equality required');load_rows.add(name)
   # Original equality generation/net flow = demand; official + is surplus on RHS.
   if w:r['terms'].extend([[plus,-w],[minus,w]])
 need(load_rows==set(mapping['balance_inventory'])=={v for v in names['constraints']if v.startswith('balance[')},'complete balance inventory mapping')
 need({z['period']for z in mapping['balance']}=={z['period']for z in mapping['accident']},'accident/balance period inventory')
 need({v for z in mapping['accident']for v in z['qualified_columns']}=={v for v in names['variables']if v.startswith(('accident[','price_reserve.battery_up[','price_reserve.pumped_up['))},'complete qualified accident column inventory')
 for z in mapping['accident']:
  r=row(z['row']);need(r['lb']==z['requirement_MW'] and z['requirement_MW']>=0 and r['ub']is None and all(c==1 for j,c in r['terms']),'source contingency requirement unchanged');need(set(j for j,c in r['terms'])=={vi[s]for s in z['qualified_columns']},'exact existing eligible reserve inventory')
  s=slack(f"PRICE_accident_shortfall[{z['period']}]",rates['accident'],z['duration_h']);r['terms'].append([s,1.])
 for family,key in [('line','lines'),('section','sections')]:
  for z in mapping[key]:
   h=z['duration_h'];label=z['id'];positive=slack(f'PRICE_{family}_positive[{label}]',rates['network'],h);negative=slack(f'PRICE_{family}_negative[{label}]',rates['network'],h)
   if 'column'in z:
    need(family=='line'and z['column']in vi,'line column required');j=vi[z['column']];v=q['variables'][j];need(v['lb']is not None and v['ub']is not None,'finite original line limits');r=dict(lb=v['lb'],ub=v['ub'],terms=[[j,1.],[positive,-1.],[negative,1.]]);edits.append(dict(column=j,original_bounds=[v['lb'],v['ub']]));v['lb']=v['ub']=None;q['rows'].append(r);n['constraints'].append(f'PRICE_line_limit[{label}]')
   else:
    r=row(z['row']);need(r['lb']is not None and r['ub']is not None,'ranged section/line row required');r['terms'].extend([[positive,-1.],[negative,1.]])
 need({z['column']for z in mapping['lines']if 'column'in z}==set(mapping['line_column_inventory'])=={v for v in names['variables']if v.startswith('flow[')},'complete explicit line-column inventory')
 need({z['row']for z in mapping['sections']}==set(mapping['section_row_inventory']),'complete explicit section inventory')
 for name in mapping['positive_reserve_rows']:need(name in ri and q['rows'][ri[name]]==base['rows'][ri[name]],'positive reserve must stay hard/unchanged')
 modified={z['column']for z in edits}
 need(all(q['variables'][j]==v for j,v in enumerate(base['variables'])if j not in modified),'resource inventory/qualification bounds changed')
 trace=dict(schema='gd.PRICE.slack_mechanisms.v1',parameters_sha256=digest(parameters),mapping_sha256=digest(mapping),base_template_sha256=digest(base),slacks=slacks,removed_line_column_bounds=edits,original_resource_columns=len(base['variables']),new_columns=len(slacks),accident_requirement_MW=[z['requirement_MW'] for z in mapping['accident']],positive_reserve_relaxed=False,no_new_qualification=True,PRICE_only=True,not_executable_dispatch=True,formal_market_complete=False,parameter_authenticity_not_established_by_code=True,storage_price_domain_unchanged=True,source_code_sha256=sha(__file__))
 return q,n,trace
