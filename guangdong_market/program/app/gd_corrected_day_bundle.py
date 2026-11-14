"""Lightweight immutable corrected-research-day view. No solver or context rewriting."""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal,localcontext
import json,csv,hashlib,shutil
FILES=('HOURS.csv','QUARTER_PRICES.csv','LEDGER.csv','EXECUTED_CELLS.csv','PRICE_RELAXATION_DIAGNOSTICS.json','DAY.json','SOURCE_INDEX.json')
def need(ok,msg):
 if not ok:raise ValueError(msg)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def csvread(p):
 with Path(p).open(newline='')as f:return list(csv.DictReader(f))
def dump(p,x):
 with Path(p).open('x')as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False)
def da_identity(da):
 da=Path(da).resolve();a=read(da/'ARTIFACTS.json');case=a['inputs']['case'];s=a['stages']['da_sced']['expanded_response']
 need(sha(da/case['path'])==case['sha256'],'selected DA case bytes changed');need(sha(da/s['path'])==s['sha256'],'selected DA SCED witness changed')
 return dict(run_id=a['run_id'],case_sha256=case['sha256'],SCED_response_sha256=s['sha256'])
def inspect_pending(aggregate):
 p=Path(aggregate)
 needed=('SUMMARY.json','HOURS.csv','QUARTER_PRICES.csv','LEDGER.csv','PRICE_RELAXATION_DIAGNOSTICS.json')
 missing=[n for n in needed if not(p/n).is_file()]
 return dict(status='pending'if missing else'files_present_not_yet_verified',missing=missing,native_calls=0,filled_missing_values=False)
def validate_tables(day,hours,quarters,ledger,cells,diagnostics):
 need(day['branch_id']=='short_cycle_tail_v1'and day['run_id'].endswith('-cycle-tail-v1'),'wrong corrected branch')
 need(day['hours']==24 and day['quarter_prices']==96 and day['executed_cells']==288 and day['rows']==1296,'incomplete day summary')
 need(len(hours)==24 and len(quarters)==96*39 and len(ledger)==1296 and len(cells)==288,'missing or duplicate day records')
 need([int(x['hour'])for x in hours]==list(range(24)),'hour sequence')
 need(sorted(int(x['tick'])for x in cells)==list(range(288)),'executed cells must exactly cover D, no issued future')
 origin=datetime.fromisoformat(hours[0]['start']);need(origin.utcoffset()==timedelta(hours=8)and origin.hour==origin.minute==origin.second==0,'D midnight clock')
 byq={q:[]for q in range(96)}
 for r in quarters:
  q=int(r['day_quarter']);need(q in byq,'quarter outside day');byq[q].append(r)
 nodes={r['node']for r in byq[0]};need(len(nodes)==39,'39 nodes required')
 byh={h:[]for h in range(24)}
 for r in ledger:
  h=int(r['hour']);need(h in byh,'ledger hour outside day');byh[h].append(r)
 with localcontext()as ctx:
  ctx.prec=80
  for q,rows in byq.items():
   need(len(rows)==39 and {r['node']for r in rows}==nodes,'missing/duplicate quarter node')
   for r in rows:
    need(datetime.fromisoformat(r['start'])==origin+timedelta(minutes=15*q)and datetime.fromisoformat(r['end'])==origin+timedelta(minutes=15*(q+1)),'quarter clock')
    need(Decimal(r['raw_research_CNY_per_MWh']).is_finite(),'nonfinite price')
  for h,row in enumerate(hours):
   need(datetime.fromisoformat(row['start'])==origin+timedelta(hours=h)and datetime.fromisoformat(row['end'])==origin+timedelta(hours=h+1),'hour clock')
   need(int(row['rows'])==54 and int(row['executed_cells'])==12 and len(byh[h])==54,'incomplete hour')
   need(len({(x['resource'],x['direction'])for x in byh[h]})==54,'duplicate account direction')
   total=sum((Decimal(x['energy_amount_CNY'])for x in byh[h]),Decimal(0));need(abs(total-Decimal(row['total_energy_component_CNY']))<=Decimal('1e-16'),'hour ledger total mismatch')
   for x in byh[h]:
    need(x['start']==row['start']and x['end']==row['end'],'ledger period mismatch')
    need(x.get('payable')in ('False',False)and x.get('channel')=='simulation' and x.get('actual_meter')in ('',None) and x.get('formal_bill')in ('',None),'not simulation rows')
    need(abs(Decimal(x['QDA_MWh'])*Decimal(x['DA_hour_price'])+(Decimal(x['simulated_executed_MWh'])-Decimal(x['QDA_MWh']))*Decimal(x['RT_hour_price'])-Decimal(x['energy_amount_CNY']))<=Decimal('1e-12'),'research two-settlement arithmetic mismatch')
    prices=[Decimal(r['raw_research_CNY_per_MWh'])for q in range(h*4,h*4+4)for r in byq[q]if r['node']==x['node']]
    need(len(prices)==4,'ledger node mismatch');average=sum((max(Decimal(-50),min(Decimal(1800),p))for p in prices),Decimal(0))/4
    need(abs(average-Decimal(x['RT_hour_price']))<=Decimal('1e-12'),'quarter/hour price mismatch')
  total=sum((Decimal(x['total_energy_component_CNY'])for x in hours),Decimal(0));need(total==Decimal(day['total_energy_component_CNY']),'day sum mismatch')
  for r in cells:
   residual=Decimal(r['supply_MW'])+Decimal(r['fixed_tie_MW'])-Decimal(r['load_MW']);need(abs(residual)<Decimal('1e-6')and abs(residual-Decimal(r['residual_MW']))<Decimal('1e-6'),'cell physical balance failure')
 need(len(diagnostics)==96 and [int(x['quarter'])for x in diagnostics]==list(range(96)),'price diagnostics incomplete; unknown must be explicit')
 return dict(status='complete_readonly_research_day',hours=24,quarter_prices=96,executed_cells=288,ledger_rows=1296,total_energy_component_CNY=day['total_energy_component_CNY'],formal_bill=None,payable=False,safety_release=False,native_calls=0)
def export_day(aggregate,output,*,da,context,expected_context_sha256):
 aggregate=Path(aggregate).resolve();output=Path(output).resolve();state=inspect_pending(aggregate)
 if state['status']=='pending':return state
 need(not output.exists(),'fresh bundle directory only');s=read(aggregate/'SUMMARY.json');need(s['status']=='full_day_research_accounting_receipts_verified','unknown aggregate status');need(s['formal_bill']is None and s['payable']is False and s['safety_release']is False,'unexpected formal aggregate')
 pins=s['source_pins'];need(sha(context)==expected_context_sha256 and pins.get(str(Path(context).resolve()))==expected_context_sha256,'final context not pinned by aggregate')
 c=read(context);need(c['run_id']==s['run_id']and c['branch_id']==s['branch_id'],'aggregate/context branch mismatch');need(Path(c['selected_DA']).resolve()==Path(da).resolve(),'different selected DA')
 identity=da_identity(da);need(identity['run_id']==c['selected_DA_run_id'],'selected DA run mismatch')
 for path,digest in pins.items():need(sha(path)==digest,'source bytes changed '+path)
 cells=[]
 for h,detail in enumerate(s['hours_detail']):
  hp=Path(detail['source']);need(pins.get(str(hp))==sha(hp),'hour receipt not pinned');hour=read(hp);need(len(hour['physical_cells'])==12,'physical hour cells missing');cells.extend(hour['physical_cells'])
 need([int(x['tick'])for x in cells]==list(range(288)),'executed cells missing or reordered')
 output.mkdir()
 for name in ('HOURS.csv','QUARTER_PRICES.csv','LEDGER.csv','PRICE_RELAXATION_DIAGNOSTICS.json'):shutil.copyfile(aggregate/name,output/name)
 with(output/'EXECUTED_CELLS.csv').open('x',newline='')as f:w=csv.DictWriter(f,fieldnames=list(cells[0]));w.writeheader();w.writerows(cells)
 day={k:v for k,v in s.items()if k not in ('source_pins','hours_detail')};day.update(DA_identity=identity,source_aggregate_sha256=sha(aggregate/'SUMMARY.json'),scope='fixed historical corrected research branch, not current edited scenario or app-native rerun',raw_source_bytes_verified_at_export=True,original_execution_context_sha256=expected_context_sha256)
 dump(output/'DAY.json',day);dump(output/'SOURCE_INDEX.json',dict(aggregate_path=str(aggregate),aggregate_sha256=sha(aggregate/'SUMMARY.json'),source_pins=pins,raw_files_embedded=False,raw_evidence_backup_required_for_independent_replay=True,source_context_preserved_not_rewritten=True))
 result=validate_tables(day,csvread(output/'HOURS.csv'),csvread(output/'QUARTER_PRICES.csv'),csvread(output/'LEDGER.csv'),csvread(output/'EXECUTED_CELLS.csv'),read(output/'PRICE_RELAXATION_DIAGNOSTICS.json'))
 manifest=dict(schema='gd.corrected_day.readonly_bundle.v1',files={n:sha(output/n)for n in FILES},DA_identity=identity,run_id=s['run_id'],branch_id=s['branch_id'],formal=False,source_scope='selected materialized view plus original raw source index, not raw evidence archive')
 dump(output/'MANIFEST.json',manifest);return dict(**result,manifest_sha256=sha(output/'MANIFEST.json'),bundle=str(output))
def load_bundle(bundle,*,expected_manifest_sha256,selected_DA_identity):
 p=Path(bundle);mp=p/'MANIFEST.json'
 if not mp.is_file():return dict(status='pending',reason='final verified day bundle not available',native_calls=0,filled_missing_values=False)
 need(sha(mp)==expected_manifest_sha256,'bundle manifest differs from registered snapshot');m=read(mp);need(m['schema']=='gd.corrected_day.readonly_bundle.v1'and set(m['files'])==set(FILES),'unknown bundle layout');need(m['DA_identity']==selected_DA_identity,'wrong DA: historical day cannot represent edited scenario')
 for name,h in m['files'].items():need((p/name).is_file()and sha(p/name)==h,'missing/changed bundle data '+name)
 day=read(p/'DAY.json');need(day['DA_identity']==m['DA_identity']and day['run_id']==m['run_id']and day['branch_id']==m['branch_id'],'mixed day identity')
 result=validate_tables(day,csvread(p/'HOURS.csv'),csvread(p/'QUARTER_PRICES.csv'),csvread(p/'LEDGER.csv'),csvread(p/'EXECUTED_CELLS.csv'),read(p/'PRICE_RELAXATION_DIAGNOSTICS.json'))
 return dict(**result,day=day,hour_rows=csvread(p/'HOURS.csv'),quarter_rows=csvread(p/'QUARTER_PRICES.csv'),cells=csvread(p/'EXECUTED_CELLS.csv'),raw_evidence_reverified_on_this_machine=False,view_integrity_verified=True)
