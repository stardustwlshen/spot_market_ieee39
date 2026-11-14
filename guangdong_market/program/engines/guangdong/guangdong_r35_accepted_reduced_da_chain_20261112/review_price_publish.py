"""One new PRICE KKT/nodal derivative/physical audit and gated research publication.
Math oracle is unchanged from the previously reviewed raw-matrix checker.
No solver, real execution, official publication or formal settlement.
"""
import argparse,math,json,hashlib
from pathlib import Path
from datetime import datetime,timedelta
from common import ROOT,read,sha,write,event_paths,accepted_r34
from independent_price_math import certificate
from r35_pipeline import load_event,finalize_research_publication
def req(v,msg):
 if not v:raise ValueError(msg)
def close(a,b,msg,tol=1e-6):req(math.isfinite(a)and math.isfinite(b)and abs(a-b)<=tol,f'{msg}: {a} != {b}')
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def write(p,x):
 with p.open('xb')as f:f.write(canonical(x))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prepared',type=Path,required=True);ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();prep=a.prepared;native=a.native
 pins=read(prep/'PINS.json')
 for f,h in pins.items():req(sha(prep/f)==h,'new PRICE prepared pin '+f)
 metadata=read(prep/'PURE_PREPARATION.json');bridge=read(prep/'bridge.json');source=metadata['source_receipt'];d=read(prep/'input.json');names=read(prep/'names.json');template=read(prep/'template.json')
 accepted_r34(Path(metadata['SCUC_paths']['request']).parent,Path(metadata['SCUC_audit_path']))
 event=load_event(event_paths(native));q=event['request'];r=event['response'];strip=lambda z:{k:v for k,v in z.items()if k not in ('deadline','preparation_elapsed_s')}
 req(strip(q)==strip(template),'actual PRICE exactly new template');req(r['status']=='optimal'and r['native_status']=='Optimal'and r['dual_valid']is True and r['has_primal']is True,'new Optimal PRICE primal/dual')
 req(all(z['kind']=='C'for z in q['variables']),'independent PRICE LP');req(source['SCUC_accepted']is True and source['diagnostic_only']is False,'accepted actual source, no diagnostic promotion')
 req(bridge['SCUC_accepted']is True and bridge['execution_authority']is False,'research source no execution authority')
 a.output.mkdir(exist_ok=False)
 kkt=certificate(q,r,names);kkt.pop('details')
 vi={n:i for i,n in enumerate(names['variables'])};rows={n:row for n,row in zip(names['constraints'],q['rows'])};duals=dict(zip(names['constraints'],r['dual']));values=dict(zip(names['variables'],r['primal']));nodes=d['nodes'];resources=d['resources'];x=d['physical_lift'];mapping=x['market_to_physical'];grid=d['grid'];lines=d['network'];day=datetime.fromisoformat(grid[0]['start'])
 req(len(nodes)==39 and len(grid)==98 and len(x['cells'])==576,'full grids');req(mapping[:96]==[list(range(3*t,3*t+3))for t in range(96)],'96 complete delivery quarters');req(mapping[96:]==[[363,364,365],[498,499,500]],'distinct actual representative mapping')
 req(len({j for ids in mapping for j in ids})==294,'nonoverlap projections');req(x['availability_is_simulated']is True and x['future_RT_used']is False,'no future actual data')
 for t,g in enumerate(grid):
  req(g['settlement']==(t<96),'representatives never settle');ids=mapping[t];req(len(ids)==3 and ids==list(range(ids[0],ids[0]+3)),'three contiguous physical cells')
  req(g['start']==x['cells'][ids[0]]['start']and g['end']==x['cells'][ids[-1]]['end'],'actual timestamps')
  req(g['objective_weight_h']>0,'positive derivative weight')
 for j,c in enumerate(x['cells']):req(datetime.fromisoformat(c['start'])==day+timedelta(minutes=5*j)and datetime.fromisoformat(c['end'])==day+timedelta(minutes=5*(j+1)),'continuous physical time')
 physical_source=read(Path(metadata['SCED_physical_path']));req(source['physical_report_sha256']==hashlib.sha256(canonical(physical_source)).hexdigest(),'source physical report binding')
 zero_refs=0
 for uid,u in resources.items():
  for t,row in enumerate(u['rows']):
   ids=mapping[t];source_p=[physical_source['physical_cells'][j]['thermal'][uid]['mean_mw']if u['kind']=='generator'else physical_source['physical_cells'][j]['battery'][uid]['discharge_mw']-physical_source['physical_cells'][j]['battery'][uid]['charge_mw']for j in ids]
   close(row['sced_mw'],math.fsum(source_p)/3,'new SCED reference projection')
   p=values[f'power[{uid},{t}]'];close(3*p,math.fsum(values[f'physical_power[{uid},{j}]']for j in ids),'PRICE quarter physical mean')
   ref=row['sced_mw'];lo=max(row['min_mw'],ref-abs(ref)*d['delta'])if row['price_eligible']else ref;hi=min(row['max_mw'],ref+abs(ref)*d['delta'])if row['price_eligible']else ref
   if not row['online']:lo=hi=0.
   z=q['variables'][vi[f'power[{uid},{t}]']];close(z['lb'],lo,'raw DA band lower');close(z['ub'],hi,'raw DA band upper');req(lo-1e-6<=p<=hi+1e-6,'DA band actual power')
   if ref==0:zero_refs+=1;req(lo==hi==0.,'zero reference exactly locked')
 # Verify coefficients and RHS of every row used by the nodal price derivative.
 derivative_rows=0
 for prefix,load,fixed,count in (('',d['load_mw'],d['fixed_injection_mw'],98),('physical_',x['load_mw'],x['fixed_injection_mw'],576)):
  for t in range(count):
   row=rows[f'{prefix}balance[{t}]'];rhs=math.fsum(load[n][t]-fixed[n][t]for n in nodes);close(row['lb'],rhs,'balance load derivative RHS');close(row['ub'],rhs,'balance equality RHS')
   actual={i:c for i,c in row['terms']if c};expected={vi[f'{prefix}power[{uid},{t}]']:1. for uid in resources};req(actual==expected,'balance power coefficients');derivative_rows+=1
   for line,z in lines.items():
    constant=math.fsum(z['ptdf'][n]*(fixed[n][t]-load[n][t])for n in nodes)
    bounds=d['network'][line]if prefix==''else x['network'][line]
    for side,sign in (('upper',1.),('lower',-1.)):
     row=rows[f'{prefix}line_{side}[{line},{t}]'];rhs=sign*bounds['upper_mw'if side=='upper'else'lower_mw'][t]-sign*constant;close(row['ub'],rhs,'network load derivative RHS');req(row['lb']is None,'upper-oriented network row')
     expected={vi[f'{prefix}power[{uid},{t}]']:sign*z['ptdf'][u['node']]for uid,u in resources.items()if z['ptdf'][u['node']]!=0}
     if prefix=='':expected[vi[f'slack_{"up"if side=="upper"else"down"}[{line},{t}]']]=-1.
     actual={i:c for i,c in row['terms']if c};req(set(actual)==set(expected),'network derivative columns')
     for i,c in expected.items():close(actual[i],c,'network derivative coefficient',1e-12)
     derivative_rows+=1
 prices={n:[]for n in nodes}
 for n in nodes:
  for t,g in enumerate(grid):
   value=duals[f'balance[{t}]']+math.fsum(lines[l]['ptdf'][n]*(duals[f'line_upper[{l},{t}]']-duals[f'line_lower[{l},{t}]'])for l in lines)
   value+=math.fsum(duals[f'physical_balance[{j}]']+math.fsum(lines[l]['ptdf'][n]*(duals[f'physical_line_upper[{l},{j}]']-duals[f'physical_line_lower[{l},{j}]'])for l in lines)for j in mapping[t])
   prices[n].append(value/g['objective_weight_h'])
 terminal={};throughput={};max_balance=0.;min_positive=math.inf;min_accident=math.inf
 for uid,u in resources.items():
  prev=u['initial_power_mw'];energy=u.get('initial_energy_mwh');usage={}
  if u['kind']=='battery':close(values[f'physical_energy[{uid},0]'],energy,'initial SOC')
  for j,row in enumerate(x['resources'][uid]['rows']):
   p=values[f'physical_power[{uid},{j}]'];req(row['min_mw']-1e-6<=p<=row['max_mw']+1e-6,'physical bound')
   if u['kind']=='generator'and row['phase']in('startup','shutdown'):
    close(prev,row['begin_mw'],'transition beginning continuity');close(p,row['mean_mw'],'fixed curve mean');prev=row['end_mw']
   else:
    req(p-prev<=row['ramp_up_mw_h']/12+1e-6 and prev-p<=row['ramp_down_mw_h']/12+1e-6,'true endpoint ramp');prev=p
   if u['kind']=='battery':
    mode=row['mode'];coef=u['eta_charge']if mode=='charge'else 1/u['eta_discharge'];energy-=coef*p/12;close(values[f'physical_energy[{uid},{j+1}]'],energy,'continuous physical SOC',1e-6);req(u['energy_min_mwh']-1e-6<=energy<=u['energy_max_mwh']+1e-6,'SOC hard bound')
    if j==287:terminal[uid]=energy
    date=x['cells'][j]['start'][:10];usage[date]=usage.get(date,0.)+(-u['eta_charge']if mode=='charge'else 1/u['eta_discharge'])*p/12
   for stem,key in(('up','positive_cap_mw'),('down','negative_cap_mw'),('primary','primary_cap_mw'),('accident','accident_cap_mw')):
    reserve=values[f'physical_{stem}[{uid},{j}]'];req(-1e-6<=reserve<=row[key]+1e-6,'reserve engineering cap');req(reserve<=(p-row['min_mw']if stem=='down'else row['max_mw']-p)+1e-6,'reserve physical headroom')
    if u['kind']=='battery' or not row['reserve_eligible']:req(row[key]==0 and abs(reserve)<=1e-6,'unqualified resource no reserve')
  if u['kind']=='battery':
   req(u['terminal_interpretation']=='D96_only_research'and u['terminal_targets'][0]['index']==95,'D96 hard target not moved');close(terminal[uid],u['terminal_targets'][0]['energy_mwh'],'D96 true SOC')
   for date,v in usage.items():budget=x['resources'][uid]['cycle_budget'][date];req(v+budget['used_internal_mwh']<=budget['limit_internal_mwh']+1e-6,'dated throughput')
   throughput[uid]=usage
 for j,rr in enumerate(x['reserve']):
  req(rr['positive_mw']==400. and rr['accident_mw']==400.,'two original independent 400 requirements')
  positive=math.fsum(values[f'physical_up[{uid},{j}]']for uid in resources);accident=math.fsum(values[f'physical_accident[{uid},{j}]']for uid in resources);req(positive>=400.-1e-6 and accident>=400.-1e-6,'separate400 reserve satisfaction');min_positive=min(min_positive,positive);min_accident=min(min_accident,accident)
  balance=math.fsum(values[f'physical_power[{uid},{j}]']for uid in resources)+math.fsum(x['fixed_injection_mw'][n][j]-x['load_mw'][n][j]for n in nodes);max_balance=max(max_balance,abs(balance));req(abs(balance)<=1e-6,'physical balance')
 req(all(rr['positive_mw']==rr['accident_mw']==400. for rr in d['reserve']),'market400 series')
 price_artifact={'schema':'gd.research.r35.independent_PRICE_dual_prices.v1','prices98_yuan_mwh':prices,'prices96_delivery_yuan_mwh':{n:v[:96]for n,v in prices.items()},'hourly_four_quarter_mean_yuan_mwh':{n:[math.fsum(v[4*h:4*h+4])/4 for h in range(24)]for n,v in prices.items()},'representative_indices_not_settled':[96,97],'source_PRICE_response_sha256':sha(native/'response.json'),'SCED_duals_used':False,'formula':'(market balance + PTDF network RHS duals + sum of corresponding three physical balance/network RHS duals) / market objective_weight_h','formal_publication_allowed':False,'SCUC_accepted':True,'not_settlement':True}
 write(a.output/'INDEPENDENT_PRICES98.json',price_artifact)
 result={'schema':'gd.research.r35.new_PRICE.independent_review.v1','status':'pass_research_PRICE','auditor_solver_calls':0,'KKT':kkt,'native_request_sha256':sha(native/'request.json'),'native_response_sha256':sha(native/'response.json'),'input_sha256':sha(prep/'input.json'),'template_sha256':sha(prep/'template.json'),'prices_sha256':sha(a.output/'INDEPENDENT_PRICES98.json'),'logical_available_at':d['planned_publication_at'],'logical_clock_is_simulation_not_historical_publication':True,'prices_checked':len(nodes)*98,'independent_derivative_rows_checked':derivative_rows,'physical_cells':576,'physical_resource_cells':576*len(resources),'delivery_quarters':96,'representatives_nonsettled':2,'zero_DA_reference_locked_count':zero_refs,'D96_SOC_mwh':terminal,'dated_throughput_mwh':throughput,'min_positive_reserve_mw':min_positive,'min_accident_reserve_mw':min_accident,'max_physical_balance_error_mw':max_balance,'SCUC_accepted':True,'formal_publication_allowed':False,'execution_authority':False,'source_SCUC_request_sha256':source['SCUC_request_sha256'],'source_SCUC_response_sha256':source['SCUC_response_sha256'],'SCED_duals_used':False}
 write(a.output/'PRICE_REVIEW.json',result)
 publication=finalize_research_publication(bridge,scuc_paths=metadata['SCUC_paths'],scuc_audit=metadata['SCUC_audit_path'],proof_files=metadata['proof_files'],sced_paths=metadata['SCED_paths'],sced_audit=metadata['SCED_audit_path'],price_paths=event_paths(native),price_audit=str(a.output/'PRICE_REVIEW.json'),price_input_path=str(prep/'input.json'),price_template_path=str(prep/'template.json'),prices_path=str(a.output/'INDEPENDENT_PRICES98.json'),available_at=d['planned_publication_at'])
 write(a.output/'RESEARCH_PUBLICATION.json',publication);print(json.dumps({'status':'new_accepted_research_chain_complete','KKT':kkt,'prices':len(nodes)*98,'research_publication_rows':len(publication['rows']),'formal_publication_allowed':False,'execution_authority':False}))
if __name__=='__main__':main()
