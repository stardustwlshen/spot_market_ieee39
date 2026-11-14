from loader import *
from decimal import Decimal as D
from domain_v23 import digest
import tempfile
def verified_execution(da_run,execution):
 view=read(execution);need(view['schema']=='gd.short.PRICE.execution_view.v1','known execution view');verify(view['source_pins'])
 from program import execution_view
 with tempfile.TemporaryDirectory(prefix='gd-unified-hour-replay-') as temp:
  fresh=Path(temp)/'execution.json';execution_view(da_run,view['run'],view['hour'],fresh);need(read(fresh)==view,'execution values or context differ from complete producer replay')
 return view

mods=[LEG/'vendor/guangdong_r75_daily_simulated_settlement_20261112/arithmetic.py'];sys.path.insert(0,str(mods[0].parent))
from arithmetic import settle_hour,encode,PRICE_KIND
sys.path.insert(0,str(LEG/'vendor/guangdong_r47_price_settlement_contract_20261112'))
from r47_price import PriceLimitSpec,price_candidate,hourly_candidate

def compute(da_run,execution,quarters,output):
 need(not Path(output).exists(),'fresh hour output only');view=verified_execution(da_run,execution);hour=view['hour'];need(type(hour)is int and 0<=hour<24 and view['executed_end']>=12*(hour+1),'complete executed hour');ticks=list(range(hour*12,(hour+1)*12));need(view['cells']==ticks,'exact target12cells');x=view['values'];da=Path(da_run).resolve();need(Path(view['selected_DA']).resolve()==da,'wrong DA execution binding');run=Path(view['run']);req=read(run/'seed/request.json');need(req['run_id']==view['run_id'],'same execution run');start=datetime.fromisoformat(view['origin'])+timedelta(hours=hour);casefile=run/'seed/case.json';need(sha(casefile)==req['case_sha256'],'case binding');c=loads(casefile.read_bytes());e=c['energy_inputs'];a=read(da/'ARTIFACTS.json');s=a['stages'];need(s['da_sced']['acceptance']['sha256']==req['DA_acceptance_sha256'],'same accepted DA')
 for stage in ['da_sced','da_price']:
  for z in s[stage].values():
   if isinstance(z,dict)and 'path'in z:need(sha(da/z['path'])==z['sha256'],'DA artifact changed')
 cells=read(da/s['da_sced']['physical_solution']['path'])['physical_cells'];da_raw=read(da/s['da_price']['raw_prices']['path'])['raw_unclipped_LMP'];need(quarters and len(quarters)==4,'exact four explicit quarters');paths=[Path(p).resolve()for p in quarters];records=[]
 for i,p in enumerate(paths):
  z=read(p);need(z['schema']=='gd.independent_PRICE.accepted.v2','unified hour requires independently verified PRICE, including selected-DA seed quarter');from program import read_result
  verified=read_result(p);need(verified['context']==z['context'] and verified['raw_prices']==z['raw_prices'],'raw PRICE mismatch');ctx=z['context'];verify(z['source_pins']);q=hour*4+i;need(ctx['short_run']==str(run.resolve()) and ctx['selected_DA']==str(da) and ctx['run_id']==view['run_id'],'quarter run/DA identity');need(ctx['end']==(start+timedelta(minutes=15*(i+1))).isoformat(),'quarter end');need(ctx['day_quarter']==q and ctx['start']==(start+timedelta(minutes=15*i)).isoformat(),'quarter time mapping');need(z['KKT']['passed'] and len(z['raw_prices'])==39,'complete independently accepted raw price')
  expected=s['da_sced']['expanded_response']['sha256']if q==0 else read(run/f'W{q-1}/actual/SCED/COMPLETED.json')['response']['raw_sha256'];need(ctx['SCED_response_sha256']==expected,'quarter from wrong run SCED/DA seed');records.append(z)
 binding=dict(quarter_sources_sha256=digest([dict(path=str(p),sha256=sha(p))for p in paths]),execution_sha256=sha(execution),hour=hour,scope='selected short run counterfactual executed cells only')
 spec=PriceLimitSpec('GD','both','CNY/MWh',D(-50),D(1800),start,None,'research://explicit-price-bound-assumption','research_bounds_not_current_official_certification','synthetic',start,True,'not_found',False)
 prices={'DA':{},'RT':{}};trace=[]
 for node in da_raw:
  for stage in prices:
   qs=[]
   for t in range(4):
    raw=da_raw[node][4*hour+t] if stage=='DA' else records[t]['raw_prices'][node];pin=s['da_price']['response']['sha256'] if stage=='DA' else sha(paths[t]);st=start+timedelta(minutes=15*t)
    qs.append(price_candidate(spec=spec,status='success',run_id=req['run_id']+':'+stage,node=node,start=st,end=st+timedelta(minutes=15),stage=stage,raw_price=D(str(raw)),evidence_id=stage+pin,evidence_sha256=pin))
   hourly=hourly_candidate(qs);value=hourly['bounded_hourly_candidate_price'];need(value==sum((max(D(-50),min(D(1800),D(str(q['raw_price'])))) for q in qs),D(0))/4,'independent Decimal hourly crosscheck')
   prices[stage][node]=dict(kind=PRICE_KIND,channel='simulation',node=node,start=start.isoformat(),stage=stage,value=value,source_sha256=binding['quarter_sources_sha256'] if stage=='RT' else s['da_price']['response']['sha256'],aggregation='quarter_clip_then_arithmetic_mean_four',official_publication=False,formal_bill=None);trace+=qs
 assumptions=dict(channel='simulation',contracts=[],known_empty_contracts=True,participation='participates',actual_meter=None,scope='Explicit counterfactual participating accounts, empty LT/base/agency contracts, synthetic no-deviation execution without AGC/AC clearance; not real meter or registry')
 inventory=[(g,str(d['bus']),'coal' if g in ('C1','C2','C3') else 'gas') for g,d in e['thermal'].items()]+[(u,str(d['bus']),'independent_storage') for u,d in c['batteries'].items()]+[('VRE:'+str(b),str(b),'vre') for b in e['buses']]
 rows=[];normalizations=[];physical=[]
 for resource,node,tech in inventory:
  u=resource.split(':')[-1]
  for direction in (('discharge','charge') if tech=='independent_storage' else ('discharge',)):
   if tech=='independent_storage':qd=sum((D(str(cells[t]['battery'][u][direction+'_mw'])) for t in ticks),D(0))/12;qe=sum((D(str(x[f'battery.{direction}[{u},{t}]'])) for t in ticks),D(0))/12;sign=-1 if direction=='charge' else 1;qd*=sign;qe*=sign
   elif tech=='vre':qd=sum((D(str(cells[t]['renewable'][u]['dispatched_mw'])) for t in ticks),D(0))/12;qe=sum((D(str(x[f"renewable_power['{u}',{t}]"])) for t in ticks),D(0))/12
   else:qd=sum((D(str(cells[t]['thermal'][u]['mean_mw'])) for t in ticks),D(0))/12;qe=sum((D(str(x[f'power[{u},{t}]'])) for t in ticks),D(0))/12
   raw=(qd,qe)
   vals=[qd,qe]
   for j,v in enumerate(vals):
    if (direction=='charge' and v>0) or (direction!='charge' and v<0):need(abs(v)<=D('1e-9'),'directional residual too large');normalizations.append(dict(resource=resource,direction=direction,kind=['DA','execution'][j],raw=str(v),delta=str(-v)));vals[j]=D(0)
   qd,qe=vals;z=settle_hour(resource=resource,node=node,technology=tech,direction=direction,start=start,qda=qd,qexecuted=qe,prices={k:prices[k][node] for k in prices},assumptions=assumptions)
   expected=qd*prices['DA'][node]['value']+(qe-qd)*prices['RT'][node]['value'];need(abs(z['energy_amount_CNY']-expected)<D('1e-18'),'Decimal independent two-settlement crosscheck');z['raw_signed_MWh']=raw;rows.append(z)
 for t in ticks:
  supply=sum((D(str(x[f'power[{g},{t}]'])) for g in e['thermal']),D(0))+sum((D(str(x[f'battery.discharge[{u},{t}]']))-D(str(x[f'battery.charge[{u},{t}]'])) for u in c['batteries']),D(0))+sum((D(str(x[f"renewable_power['{b}',{t}]"])) for b in e['buses']),D(0));load=sum((D(str(e['load_mw'][b,t])) for b in e['buses']),D(0));tie=sum((D(str(e['physical_tie_mw'][b,t])) for b in e['buses']),D(0));physical.append(dict(tick=t,supply_MW=supply,load_MW=load,fixed_tie_MW=tie,residual_MW=supply+tie-load))
 need(max(abs(z['residual_MW']) for z in physical)<D('1e-6'),'12-cell physical balance')
 result=dict(schema='gd.short.PRICE.hour.v1',input_binding=binding,executed_cells=12,quarter_prices=4,rows=rows,hour_prices=prices,physical_cells=physical,normalizations=normalizations,total_energy_component_CNY=sum((z['energy_amount_CNY'] for z in rows),D(0)),assumptions=assumptions,formal_bill=None,payable=False,safety_release=False,remaining_hours='unpriced_not_filled',source_pins={str(p.resolve()):sha(p) for p in [*paths,Path(execution),da/'ARTIFACTS.json',mods[0],Path(__file__)]})
 Path(output).write_text(json.dumps(result,default=encode,indent=2));return dict(status='accepted',native_calls=0,rows=len(rows),executed_cells=12,total=str(result['total_energy_component_CNY']),formal_bill=None)

