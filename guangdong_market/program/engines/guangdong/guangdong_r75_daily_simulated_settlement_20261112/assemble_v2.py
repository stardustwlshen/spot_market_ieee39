"""Full-day simulation assembler. Reject incomplete execution or price provenance."""
from arithmetic import *
import argparse,csv
B=Path(__file__).resolve().parent
RUN=ROOT/'guangdong_r67_actual_complete_tail_20261112/native_day_v1'
DA=ROOT/'guangdong_r53_same_source_da_chain_20261112'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
class Sources:
 def __init__(self):self.pins={}
 def pin(self,p,expected=None):
  p=Path(p);p=p if p.is_absolute()else ROOT/p;h=sha(p);need(expected is None or h==expected,'source identity mismatch '+str(p));self.pins[str(p)]=h;return read(p)
 def verify(self):
  for p,h in self.pins.items():need(sha(p)==h,'source changed during assembly')
def assumptions():return dict(channel='simulation',participation='participates',participation_scope='all listed thermal/storage/VRE resources solely this research day; actual entitlement unknown',contracts=[],known_empty_contracts=True,agency_contracts=[],agency_mode='non_decoupled_provincial assumed for thermal with explicit zero agency quantity',actual_meter=None,quantity_kind='simulated_executed_not_metered',price_kind=PRICE_KIND,official_publication=False,formal_bill=None,payable=False,dynamic_price_limit_verified=False,price_limit_baseline='粤发改价格〔2025〕263号; -50/1800 CNY/MWh; quarter clip then four-point arithmetic mean',storage='separate signed discharge and charge ledgers; never net before calculation',scope='electric energy components only; excludes AGC, reserves compensation, monthly mechanisms, green certificates, fees and taxes')
def load_execution(S):
 result=S.pin(RUN/'RUN_RESULT.json');drain=S.pin(RUN/'D96_DRAIN.json');r=result['report']
 need(r['same_controller_full96_complete']and r['executed_RT_cells']==r['issued_RT_cells']==288,'full day not actually executed')
 need(drain['report']==r,'final drain/report mismatch')
 source=S.pin(RUN/'SOURCE_CONTRACT.json');sh=sha(RUN/'SOURCE_CONTRACT.json');si=S.pin(RUN/'SOURCE_IDENTITY.json')['sha256']
 cells=[];inputs=[]
 for w in range(96):
  p=RUN/f'W{w}';a=S.pin(p/'accepted/ACCEPTANCE.json');need(a['window']==w and a['KKT']['passed'] and a['issued_cells']==3,'strict actual RT acceptance')
  need(a['source_contract_sha256']==sh and a['source_identity_sha256']==si,'mixed RT source')
  inp=S.pin(p/'prepared/local_RT_input.json',a['local_RT_input_sha256']);inputs.append(inp)
  fact=S.pin(p/'accepted/GLOBAL_EXTENSION_FACTS.json');need(fact['acceptance_sha256']==sha(p/'accepted/ACCEPTANCE.json') and fact['local_RT_input_sha256']==a['local_RT_input_sha256'],'extension acceptance binding');commands=S.pin(p/'accepted/issued_commands.json')
  need(len(commands)==3,'first three only')
  for k,c in enumerate(commands):need(c['global_cell_index']==3*w+k and c['accepted_event']==a['event_id'],'command/actual binding');cells.append(c['cell'])
 start=datetime.fromisoformat(source['day_start'])
 for j,c in enumerate(cells):need(c['start']==(start+timedelta(minutes=5*j)).isoformat() and c['end']==(start+timedelta(minutes=5*(j+1))).isoformat(),'execution gap')
 da=source['physical_cells'][:288];need(len(da)==288 and all(x['start']==y['start']for x,y in zip(da,cells)),'DA/RT timeline')
 # Bind the retained actual DA physical and independent price receipts, not a new solve.
 for p,h in source['source_bindings'].items():
  if '/sced_review_v3/physical_solution.json' in p or '/sced_review_v3/SCED_REVIEW.json' in p or '/price_review_v1/' in p:S.pin(p,h)
 return source,cells,da,inputs,start

def compute(S,source,cells,da,inputs,start,hour_prices):
 a=assumptions();rows=[];normalizations=[];resources=inputs[0]['resources'];vre=sorted(cells[0]['renewable'])
 def quantity(seq,r,technology,direction):
  if technology=='vre':return sum((D(str(x['renewable'][r]['dispatched_mw'])) for x in seq),D(0))/12
  if technology=='independent_storage':
   z=sum((D(str(x['batteries'][r][direction+'_mw']))for x in seq),D(0))/12;return -z if direction=='charge'else z
  return sum((D(str(x['thermal'][r]['mean_mw']))for x in seq),D(0))/12
 inventory=[(r,str(v['node']),'independent_storage'if v['kind']=='battery'else('coal'if r in ('C1','C2','C3')else'gas'))for r,v in resources.items()]+[('VRE:'+b,b,'vre')for b in vre]
 need(set(resources)=={'C1','C2','C3','CCGT','GT','ES1','ES2','ES3','ES4','ES5'},'unsupported resource classification requires explicit mapping')
 for hour in range(24):
  st=start+timedelta(hours=hour);segment=slice(12*hour,12*(hour+1))
  for r,node,tech in inventory:
   for direction in (('discharge','charge')if tech=='independent_storage'else('discharge',)):
    key=r.split(':')[-1]if tech=='vre'else r
    qd,qe=(quantity(seq[segment],key,tech,direction)for seq in (da,cells))
    # Tiny native feasibility residuals remain signed raw, not rounded into a meter.
    need((qd>=D('-1e-7')and qe>=D('-1e-7'))if direction=='discharge'else(qd<=D('1e-7')and qe<=D('1e-7')),'directional quantity sign')
    raw_qd,raw_qe=qd,qe
    for label,value in [('DA',qd),('executed',qe)]:
     wrong=value<0 if direction=='discharge' else value>0
     if wrong:
      need(abs(value)<=D('1e-9'),'wrong-sign accounting quantity exceeds explicit zero-normalization threshold')
      normalizations.append(dict(resource=r,direction=direction,start=st.isoformat(),source=label,raw_signed_MWh=value,accounting_signed_MWh=D(0),delta_MWh=-value,threshold_MWh=D('1e-9'),reason='native roundoff at zero; accounting only, original execution unchanged'))
      if label=='DA':qd=D(0)
      else:qe=D(0)
    rows.append(settle_hour(resource=r,node=node,technology=tech,direction=direction,start=st,qda=qd,qexecuted=qe,prices={s:hour_prices[s][node][hour]for s in ('DA','RT')},assumptions=a))
    rows[-1].update(raw_DA_signed_MWh=raw_qd,raw_executed_signed_MWh=raw_qe,accounting_zero_normalization_applied=(qd!=raw_qd or qe!=raw_qe))
 totals={k:sum((x[k]for x in rows),D(0))for k in ('QDA_MWh','simulated_executed_MWh','deviation_MWh','energy_amount_CNY')}
 components={k:sum((x['components_CNY'][k]for x in rows),D(0))for k in ('LT','DA','RT_deviation')}
 need(abs(totals['QDA_MWh']+totals['deviation_MWh']-totals['simulated_executed_MWh'])<D('1e-20'),'energy sum conservation')
 need(abs(sum(components.values(),D(0))-totals['energy_amount_CNY'])<D('1e-15'),'amount sum conservation')
 balances=[];loads=[];fixeds=[];supplies=[]
 for j,c in enumerate(cells):
  inp=inputs[j//3];k=j%3
  supplied=sum(D(str(x['mean_mw']))for x in c['thermal'].values())+sum(D(str(x['discharge_mw']))-D(str(x['charge_mw']))for x in c['batteries'].values())+sum(D(str(x['dispatched_mw']))for x in c['renewable'].values())
  load=sum(D(str(v[k]))for v in inp['load_mw'].values());fixed=sum(D(str(v[k]))for v in inp['fixed_injection_mw'].values());balances.append(supplied+fixed-load);loads.append(load);fixeds.append(fixed);supplies.append(supplied)
 need(max(map(abs,balances))<D('1e-6'),'executed all-node energy balance')
 need(abs(sum(supplies,D(0))/12-(totals['simulated_executed_MWh']-sum((x['delta_MWh']for x in normalizations if x['source']=='executed'),D(0))))<D('1e-15'),'financial inventory equals modeled thermal/storage/VRE executed energy')
 physical=dict(modeled_resource_signed_net_MWh=sum(supplies,D(0))/12,fixed_injection_MWh=sum(fixeds,D(0))/12,load_MWh=sum(loads,D(0))/12,residual_MWh=sum(balances,D(0))/12,fixed_injection_financial_amount=None,load_side_bill=None,note='System physical energy balances; financial totals cover specified modeled resources only. Fixed injections and load-side contracts/registration are not supplied, so no whole-system monetary conservation claim.')
 return dict(schema='gd.research.r71.daily_simulated_energy.v1',status='computed_simulation_only',assumptions=a,rows=rows,totals=totals,components_CNY=components,accounting_zero_normalizations=normalizations,accounting_quantity_policy='Only wrong-direction native residual <=1e-9 MWh set to zero for retained strict-sign arithmetic; raw per-row and physical sums retained, commands unchanged',physical_energy_conservation=physical,resource_direction_hour_rows=len(rows),executed_cells=288,max_cell_power_balance_residual_MW=max(map(abs,balances)),day_energy_balance_residual_MWh=sum(balances,D(0))/12,unknown_formal_bill=True,formal_bill=None,payable=False,actual_meter=None,native_calls=0,KKT_repeated=False)

def load_prices(S,summary_path,source,start):
 sys.path.insert(0,str(ROOT/'guangdong_r74_price_summary_20261112'))
 from summarize import spec
 from r47_price import price_candidate,hourly_candidate
 summary=S.pin(summary_path);need(summary['schema']=='gd.research.r74.full96_price_summary.v1','R74 full96 research summary required')
 # Independent raw receipts remain the authority, not bounded candidate labels.
 quarters=summary['quarters'];need(len(quarters)==96*39 and all(x['status']=='success'for x in quarters),'96 complete independent PRICE quarters required')
 nodes=set(x['node']for x in quarters)
 need(summary['source_contract_sha256']==sha(RUN/'SOURCE_CONTRACT.json'),'mixed PRICE/RT contract')
 need(len(nodes)==39,'39-node price inventory')
 verified={}
 for item in summary['source_batches']:
  plan=S.pin(item['plan_path'],item['plan_sha256']);review=S.pin(item['review_path'],item['review_sha256'])
  need(plan['schema']=='gd.research.r74.PRICE_only_C_batch.v1','PRICE-only plan')
  for item in plan['items']:
   w=item['window'];p=Path(item['prepared']);meta=S.pin(p/'PREPARATION.json',item['preparation_sha256']);inp=S.pin(p/'input.json',meta['artifacts']['input.json'])
   need(meta['schema']=='gd.research.r74.quarter_PRICE.preparation.v1' and meta['window']==w,'R74 quarter-domain preparation required')
   need(inp['contract_sha256']==sha(RUN/'SOURCE_CONTRACT.json'),'same R67 source contract')
   need(sha(p/'input.json')==sha(RUN/f'W{w}/prepared/local_RT_input.json'),'PRICE not bound to actual local input')
   ap=RUN/f'W{w}/accepted/ACCEPTANCE.json';need(meta['source_pins'].get(str(ap))==sha(ap),'PRICE lacks actual RT acceptance binding')
  for rec in review['results']:
   need(rec['status']=='verified_research_raw_price','unverified PRICE result');w=int(rec['label'][1:]);need(w not in verified,'duplicate PRICE receipt');verified[w]=rec
 need(set(verified)==set(range(96)),'96 distinct PRICE receipts')
 for window in summary['windows']:
  w=window['window'];rec=verified[w];need(window['raw_prices']==rec['first_quarter_research_nodal_price_yuan_mwh'],'summary/raw review mismatch')
  native=Path(window['raw_native_path']);S.pin(native/'response.json',rec['raw_hashes']['response.json'])
 for q in quarters:
  rec=verified[q['window']];need(D(str(q['raw_price']))==D(str(rec['first_quarter_research_nodal_price_yuan_mwh'][q['node']])) and q['evidence_sha256']==rec['raw_hashes']['response.json'],'quarter raw provenance')
 r53=S.pin(DA/'price_review_v1/PRICE_REVIEW.json',source['source_bindings'][str(DA/'price_review_v1/PRICE_REVIEW.json')]);da=S.pin(DA/'price_review_v1/RAW_UNCLIPPED_LMPS98.json',r53['raw_unclipped_LMP_sha256'])['raw_unclipped_LMP'];need(set(da)==nodes,'DA/RT node inventory')
 by={(x['window'],x['node']):x for x in quarters};need(len(by)==96*39,'duplicate quarter price')
 policy=spec();result={'DA':{},'RT':{}};trace=[]
 for node in sorted(nodes):
  for stage in ('DA','RT'):
   qs=[]
   for w in range(96):
    st=start+timedelta(minutes=15*w)
    if stage=='RT':
     x=by[w,node];need(x['start']==st.isoformat(),'RT time binding');raw=x['raw_price'];h=x['evidence_sha256'];eid=x['evidence_id']
    else:raw=da[node][w];h=r53['event_pins']['response']['sha256'];eid='R53actualDA:'+h
    qs.append(price_candidate(spec=policy,status='success',run_id='R71same_source_'+stage,node=node,start=st,end=st+timedelta(minutes=15),stage=stage,raw_price=D(str(raw)),evidence_id=eid,evidence_sha256=h))
   result[stage][node]=[]
   for hour in range(24):
    h=hourly_candidate(qs[4*hour:4*hour+4]);record=dict(kind=PRICE_KIND,channel='simulation',node=node,start=h['start'].isoformat(),stage=stage,value=h['bounded_hourly_candidate_price'],source_sha256=digest(qs[4*hour:4*hour+4]),aggregation='quarter_clip_then_arithmetic_mean_four',official_publication=False,formal_bill=None)
    result[stage][node].append(record)
   trace.extend(qs)
 return result,trace

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--price-summary',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();S=Sources()
 source,cells,da,inputs,start=load_execution(S);prices,trace=load_prices(S,args.price_summary,source,start);result=compute(S,source,cells,da,inputs,start,prices);S.verify();args.output.mkdir(parents=True,exist_ok=False)
 result.update(schema='gd.research.r75.daily_simulated_energy.v1',price_source_model='R74 pure eight-quarter independent PRICE; 15min ramp mapping is research',retained_arithmetic='R71 unchanged R36/R47 formula path')
 for name,obj in [('RESULT.json',result),('PRICE_TRACE.json',trace),('SOURCE_PINS.json',S.pins),('ASSUMPTIONS.json',assumptions())]:
  (args.output/name).write_text(json.dumps(obj,default=encode,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
 print(json.dumps(dict(rows=len(result['rows']),totals=result['totals'],formal_bill=None,native_calls=0),default=encode))
if __name__=='__main__':main()
