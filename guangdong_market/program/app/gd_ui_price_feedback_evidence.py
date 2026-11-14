"""Read-only receipt integrity and presentation checks; no solver invocation."""
from pathlib import Path
import json,hashlib,math,csv,io

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def pins(z):
 for p,h in z.items():need(Path(p).is_file()and sha(p)==h,'证据缺失或变化: '+p)
def unique(z,name):
 a=[Path(p)for p in z if Path(p).name==name];need(len(a)==1,'证据必须唯一: '+name);return a[0]
def csv_bytes(rows):
 s=io.StringIO();w=csv.DictWriter(s,fieldnames=list(rows[0])if rows else []);w.writeheader();w.writerows(rows);return s.getvalue().encode('utf-8-sig')

def price_comparison(path):
 s=read(path);pins(s['source_pins']);need(len(s['source_pins'])==4 and len(s['results'])==4,'需四份独立PRICE')
 need(s['official_interpretation_unresolved']is True and s['initial_seed_quarter_unpriced']is True and s['full_hour_prices_not_complete']is True and s['formal_market_price']is None and s['settlement_amount']is None and s['no_RT_dual']is True,'不得提升为正式价格/完整小时/结算')
 results={};prices=[];summaries={(x['window'],x['boundary']):x for x in s['results']};run=None
 for file,h in s['source_pins'].items():
  z=read(file);need(z['schema']=='gd.new_RT.independent_PRICE.result.v1'and z['status']=='verified_independent_research_raw_price','非独立研究价验收');pins(z['source_pins']);k=(z['window'],z['boundary_mode']);need(k not in results and k in summaries,'重复/错域窗口');sm=summaries[k]
  need(sm['source_result_sha256']==h and sm['KKT']==z['KKT']and sm['objective']==z['objective'],'汇总被篡改');need(z['KKT']['passed']is True and z['RT_dual_used']is False and z['payable']is False,'缺数学验收/不可付款界限')
  need(all(z[x]is None for x in ('market_price','settlement_price','formal_bill')),'研究价不得冒充正式账')
  run=z['run_id']if run is None else run;need(z['run_id']==run,'跨run价格')
  sp=z['source_pins'];req=unique(sp,'request.json');res=unique(sp,'response.json');rec=read(unique(sp,'RECEIPT.json'));ident=read(unique(sp,'IDENTITY.json'));ctx=read(unique(sp,'CONTEXT.json'));ready=read(unique(sp,'READY.json'));pins(ctx['source_pins'])
  for n,h2 in ready['artifact_pins'].items():need(sha(unique(sp,'READY.json').parent/n)==h2,'准备输入变化')
  need(ctx['run_id']==run and ctx['window']==z['window']and ctx['boundary_mode']==z['boundary_mode']and ctx['SCED_response_sha256']==z['source_SCED_response_sha256'],'跨源/域/组合')
  need(rec['run_id']==run and rec['stage']=='PRICE'and rec['returncode']==0 and rec['request_sha256']==ident['request_sha256']==sha(req)and rec['response_sha256']==sha(res),'实际PRICE回执不符')
  q=read(req);r=read(res);need({k:v for k,v in q.items()if k!='deadline'}==read(unique(sp,'template.json')),'实际数学模型不符');need(r['native_status']=='Optimal'and r['backend']=='highs'and r['optimize_calls']==1 and r['dual_valid']is True,'无有效独立PRICE')
  names=read(unique(sp,'names.json'));derived={n.split('[',1)[1].split(',')[0].strip("'"):v*4 for n,v in zip(names['constraints'],r['dual'])if n.startswith('balance[')and n.endswith(',0]')}
  need(len(derived)==39 and set(derived)==set(z['raw_prices'])and all(math.isfinite(v)and abs(v-z['raw_prices'][n])<=1e-8 for n,v in derived.items()),'节点价与原始对偶不符')
  for n,v in derived.items():prices.append(dict(window=z['window'],start=z['start'],end=z['end'],boundary=z['boundary_mode'],node=n,raw_research_yuan_MWh=v))
  results[k]=z
 rows=[]
 for w in (0,1):
  a=results[w,'current_sced_research'];b=results[w,'DA_reference_R61_compatible_research'];need(a['start']==b['start']and a['end']==b['end']and a['source_SCED_response_sha256']==b['source_SCED_response_sha256'],'两域非同窗/源')
  for node in sorted(a['raw_prices'],key=int):
   x=a['raw_prices'][node];y=b['raw_prices'][node];rows.append(dict(window=w,start=a['start'],end=a['end'],node=node,current_SCED_research=x,DA_R61_research=y,difference=x-y))
 need(len(rows)==s['comparison_rows']==78 and len(prices)==s['first_quarter_node_prices']==156,'行数不符')
 return dict(run_id=run,rows=rows,prices=prices,formal=False,settlement=None,missing_seed_quarter='00:00–00:15',complete_hour=False,official_domain_decision=False)

def security_feedback(path,base_request):
 z=read(path);need(z['schema']=='gd.agc.security_feedback.v1','非后继安全合同');pins(z['source_pins']);base=read(base_request)
 need(z['source_pins'].get(str(Path(base_request).resolve()))==sha(base_request)and z['base_binding']==base['binding'],'跨候选后继反馈：不得替换/重签其他票据')
 need(z['status']=='voltage_correction_pending'and all(z[k]is False for k in ('base_loop_acceptance_changed','automatic_reclear','dispatch_authorized','payable','formal_market_complete','market_dispatch')),'不能提升后继为发布/正式安全')
 candidates=[Path(p)for p,h in z['source_pins'].items()if h==z['successor_model_sha256']];need(len(candidates)==1,'后继数学绑定');q=read(candidates[0]);need(q['agc_joint_binding']==z['base_binding'],'后继模型跨源')
 responses=[Path(p)for p,h in z['source_pins'].items()if h==z['successor_response_sha256']];need(len(responses)==1,'后继解绑定');r=read(responses[0]);N=responses[0].parent
 for f in ('COMPLETED.json','IDENTITY.json','request.json'):need(str(N/f)in z['source_pins'],'缺实际后继回执')
 done=read(N/'COMPLETED.json');need(done['returncode']==0 and done['response_sha256']==sha(responses[0])and r['native_status']=='Optimal','后继实际解未完成')
 ac=read(candidates[0].parent/'AC_RESULT.json');need(str(candidates[0].parent/'AC_RESULT.json')in z['source_pins'],'AC未封存');need(z['capacity_review']==ac['capacity_review']and z['remaining_violations']==ac['AC']['violations'],'后继安全汇总变化')
 need(z['KKT']['passed']is True and ac['AC']['ac_snapshot_safe']is False and z['capacity_review']['research_safe']is False,'不得冒充安全通过')
 return z
