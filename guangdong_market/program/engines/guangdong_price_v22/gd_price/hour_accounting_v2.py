"""One complete counterfactual hour through retained Decimal services, never a bill."""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal as D
import sys,json
from domain import read,need,sha,digest,write
B=Path(__file__).resolve().parent;R=B/'vendor'
sys.path[:0]=[str(R/'guangdong_r75_simulated_settlement_20261112')]
# Select exactly the retained arithmetic implementation, not code from a receipt.
mods=[R/'guangdong_r75_daily_simulated_settlement_20261112/arithmetic.py'];sys.path.insert(0,str(mods[0].parent))
from arithmetic import settle_hour,encode,PRICE_KIND
sys.path.insert(0,str(R/'guangdong_r47_price_settlement_contract_20261112'))
from r47_price import PriceLimitSpec,price_candidate,hourly_candidate
sys.path.insert(0,str(B/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_october_job_codec import loads

def compute(run,da_run,output,*,price_manifest,hour_index=0):
 from hour_binding import bind
 paths,binding=bind(run,da_run,price_manifest,hour_index=hour_index)
 run=Path(run);da=Path(da_run);handoff=run/f'handoff/W{4*(hour_index+1)}';receipt=read(handoff/'RECEIPT.json');req=read(handoff/'request.json');h=read(handoff/'HANDOFF.json')
 for p,v in h['pins'].items():need(sha(p)==v,'handoff evidence changed')
 need(receipt['executed_end_tick']>=12*(hour_index+1) and receipt['issued_end_tick']>=receipt['executed_end_tick'] and receipt['safety_release'] is False and receipt['AGC_AC_clearance']=='not_verified','exact counterfactual first hour, not safety clearance')
 need(req['state']['receipt_sha256']==digest(receipt) and receipt['prefix_values_sha256']==digest(req['state']['fixed_values']),'bound execution receipt')
 x=req['state']['fixed_values'];start=datetime.fromisoformat(req['origin'])+timedelta(hours=hour_index);need(datetime.fromisoformat(receipt['at'])==datetime.fromisoformat(req['origin'])+timedelta(minutes=5*receipt['executed_end_tick']),'consistent execution clock')
 casefile=run/'seed/case.json';need(sha(casefile)==req['case_sha256'],'case same');c=loads(casefile.read_bytes());e=c['energy_inputs'];a=read(da/'ARTIFACTS.json');s=a['stages'];
 for stage in ('da_sced','da_price'):
  for z in s[stage].values():
   if isinstance(z,dict) and 'path' in z:need(sha(da/z['path'])==z['sha256'],'DA artifact changed')
 cells=read(da/s['da_sced']['physical_solution']['path'])['physical_cells'];da_raw=read(da/s['da_price']['raw_prices']['path'])['raw_unclipped_LMP']
 records=[]
 for t,p in enumerate(paths):
  z=read(p);ctx=z.get('context',z);need(ctx['run_id']==req['run_id'] and ctx['day_quarter']==4*hour_index+t and ctx['start']==(start+timedelta(minutes=15*t)).isoformat(),'four bound same-run quarters')
  for file,v in z['source_pins'].items():need(sha(file)==v,'price source changed')
  need(z['KKT']['passed'] and z['raw_prices'] is not None and len(z['raw_prices'])==39,'real complete raw price');records.append(z)
 spec=PriceLimitSpec('GD','both','CNY/MWh',D(-50),D(1800),start,None,'research://explicit-price-bound-assumption','research_bounds_not_current_official_certification','synthetic',start,True,'not_found',False)
 prices={'DA':{},'RT':{}};trace=[]
 for node in da_raw:
  for stage in prices:
   qs=[]
   for t in range(4):
    raw=da_raw[node][4*hour_index+t] if stage=='DA' else records[t]['raw_prices'][node];pin=s['da_price']['response']['sha256'] if stage=='DA' else sha(paths[t]);st=start+timedelta(minutes=15*t)
    qs.append(price_candidate(spec=spec,status='success',run_id=req['run_id']+':'+stage,node=node,start=st,end=st+timedelta(minutes=15),stage=stage,raw_price=D(str(raw)),evidence_id=stage+pin,evidence_sha256=pin))
   hourly=hourly_candidate(qs);value=hourly['bounded_hourly_candidate_price'];need(value==sum((max(D(-50),min(D(1800),D(str(q['raw_price'])))) for q in qs),D(0))/4,'independent Decimal hourly crosscheck')
   prices[stage][node]=dict(kind=PRICE_KIND,channel='simulation',node=node,start=start.isoformat(),stage=stage,value=value,source_sha256=binding['quarter_sources_sha256'] if stage=='RT' else s['da_price']['response']['sha256'],aggregation='quarter_clip_then_arithmetic_mean_four',official_publication=False,formal_bill=None);trace+=qs
 assumptions=dict(channel='simulation',contracts=[],known_empty_contracts=True,participation='participates',actual_meter=None,scope='Explicit counterfactual participating accounts, empty LT/base/agency contracts, synthetic no-deviation execution without AGC/AC clearance; not real meter or registry')
 inventory=[(g,str(d['bus']),'coal' if g in ('C1','C2','C3') else 'gas') for g,d in e['thermal'].items()]+[(u,str(d['bus']),'independent_storage') for u,d in c['batteries'].items()]+[('VRE:'+str(b),str(b),'vre') for b in e['buses']]
 rows=[];normalizations=[];physical=[]
 for resource,node,tech in inventory:
  u=resource.split(':')[-1]
  for direction in (('discharge','charge') if tech=='independent_storage' else ('discharge',)):
   if tech=='independent_storage':qd=sum((D(str(cells[t]['battery'][u][direction+'_mw'])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12;qe=sum((D(str(x[f'battery.{direction}[{u},{t}]'])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12;sign=-1 if direction=='charge' else 1;qd*=sign;qe*=sign
   elif tech=='vre':qd=sum((D(str(cells[t]['renewable'][u]['dispatched_mw'])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12;qe=sum((D(str(x[f"renewable_power['{u}',{t}]"])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12
   else:qd=sum((D(str(cells[t]['thermal'][u]['mean_mw'])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12;qe=sum((D(str(x[f'power[{u},{t}]'])) for t in range(12*hour_index,12*(hour_index+1))),D(0))/12
   raw=(qd,qe)
   vals=[qd,qe]
   for j,v in enumerate(vals):
    if (direction=='charge' and v>0) or (direction!='charge' and v<0):need(abs(v)<=D('1e-9'),'directional residual too large');normalizations.append(dict(resource=resource,direction=direction,kind=['DA','execution'][j],raw=str(v),delta=str(-v)));vals[j]=D(0)
   qd,qe=vals;z=settle_hour(resource=resource,node=node,technology=tech,direction=direction,start=start,qda=qd,qexecuted=qe,prices={k:prices[k][node] for k in prices},assumptions=assumptions)
   expected=qd*prices['DA'][node]['value']+(qe-qd)*prices['RT'][node]['value'];need(abs(z['energy_amount_CNY']-expected)<D('1e-18'),'Decimal independent two-settlement crosscheck');z['raw_signed_MWh']=raw;rows.append(z)
 for t in range(12*hour_index,12*(hour_index+1)):
  supply=sum((D(str(x[f'power[{g},{t}]'])) for g in e['thermal']),D(0))+sum((D(str(x[f'battery.discharge[{u},{t}]']))-D(str(x[f'battery.charge[{u},{t}]'])) for u in c['batteries']),D(0))+sum((D(str(x[f"renewable_power['{b}',{t}]"])) for b in e['buses']),D(0));load=sum((D(str(e['load_mw'][b,t])) for b in e['buses']),D(0));tie=sum((D(str(e['physical_tie_mw'][b,t])) for b in e['buses']),D(0));physical.append(dict(tick=t,supply_MW=supply,load_MW=load,fixed_tie_MW=tie,residual_MW=supply+tie-load))
 need(max(abs(z['residual_MW']) for z in physical)<D('1e-6'),'12-cell physical balance')
 result=dict(schema='gd.first_hour.counterfactual.Decimal.v2',input_binding=binding,hour_index=hour_index,executed_cells=12,quarter_prices=4,rows=rows,hour_prices=prices,physical_cells=physical,normalizations=normalizations,total_energy_component_CNY=sum((z['energy_amount_CNY'] for z in rows),D(0)),assumptions=assumptions,formal_bill=None,payable=False,safety_release=False,remaining_hours='unpriced_not_filled',source_pins={str(p.resolve()):sha(p) for p in [*paths,Path(price_manifest),B/'hour_binding.py',run/'seed/SOURCE_PINS.json',run/'seed/request.json',casefile,handoff/'RECEIPT.json',handoff/'request.json',handoff/'HANDOFF.json',da/'ARTIFACTS.json',mods[0],Path(__file__)]})
 Path(output).write_text(json.dumps(result,default=encode,indent=2));return dict(rows=len(rows),executed_cells=12,total=str(result['total_energy_component_CNY']),formal_bill=None)
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--da-run',required=True);p.add_argument('--output',required=True);p.add_argument('--price-manifest',required=True);a=p.parse_args();print(compute(a.run,a.da_run,a.output,price_manifest=a.price_manifest))
