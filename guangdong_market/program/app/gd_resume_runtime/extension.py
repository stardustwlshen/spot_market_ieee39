"""Explicit scope change: prior pure arrays plus current owned numeric source checks."""
from pathlib import Path
from copy import deepcopy
import gzip,json
from .engine import read,sha,verify,need,write

def validate_proof(review):
 p=Path(review['comparison_manifest']);need(sha(p)==review['comparison_sha256'],'扩域比较清单改变');z=read(p);verify(z['source_pins'])
 need(z['schema']=='gd.hourly_extension.coefficient_equivalence.v1' and z['not_authorization'] is True and z['not_live_resume_acceptance'] is True,'完整纯比较证明格式错误')
 rows={r['window']:r for r in z['dispatch']};need(set(rows)==set(range(4)) and {r['quarter']for r in z['prices']}==set(range(4)),'缺少首4窗/4价格模型比较')
 chain=review['completed_chain']
 for w in range(4):
  r=rows[w];old=Path(chain['window_paths'][str(w)])/'prepared';new=Path(r['new_prepared'])
  need(read(old/'template.json')==read(new/'template.json') and read(old/'names.json')==read(new/'names.json'),'完整SCUC模型或名称不等：'+str(w))
 from gd_post_scuc_runtime.phase_runtime import fixed
 for w in range(4):
  r=rows[w];base=Path(r['SCED_rebuilt_base']);old=Path(chain['window_paths'][str(w)])
  need(z['source_pins'].get(str(base))==sha(base),'SCED比较基底未绑定')
  u=json.loads(gzip.decompress((old/'actual/SCUC/response.json.gz').read_bytes()));raw=json.loads(gzip.decompress((old/'actual/SCED/request.json.gz').read_bytes()));rebuilt=fixed(read(base),u['primal'])
  for key in ('variables','rows','objective'):need(rebuilt[key]==raw[key],'完整SCED数学不等：'+str(w)+' '+key)
  need(read(r['SCED_new_names'])==read(r['SCED_old_names']),'SCED完整名称不等')
 for r in z['prices']:
  old=Path(chain['price_paths'][str(r['quarter'])]).parent/'prepared';new=Path(r['new_prepared'])
  need(read(old/'template.json')==read(new/'template.json') and read(old/'names.json')==read(new/'names.json'),'完整PRICE模型或名称不等')
 return z

def compare_price(x,q,old_accepted,output):
 """No second model build: compare current typed causal numbers with proved source."""
 need(q in range(4),'仅首次四季度扩域比较');snap=x.prices.source.snapshot(q-1);data=snap.verify();old=read(old_accepted);ctx=old['context']
 source=Path(ctx['typed_source']);contextfile=Path(old_accepted).parent/'prepared/CONTEXT.json';verify(old['source_pins']);need(old['source_pins'].get(str(contextfile))==sha(contextfile) and read(contextfile)==ctx,'旧价格context来源缺少绑定');need(ctx['source_pins'].get(str(source))==sha(source),'旧价格typed来源缺少传递绑定');prior=read(source)
 for k in ('values','effective_agc'):need(data[k]==prior[k],'当前拥有的数值来源与已证明PRICE输入不同：'+k)
 from .context_equivalence import compare
 compare(prior['base_run'],data['base_run'])
 for k in ('selected_DA','window','executed_end','issued_end'):need(data[k]==prior[k],'当前价格来源或执行范围不同：'+k)
 need(data.get('reference_revision')==prior.get('reference_revision'),'参考修订来源不同')
 current_request=deepcopy(data['request']);old_request=deepcopy(prior['request'])
 if q>0:
  from cycle_boundary_contract import validate
  import sys
  codec_root=Path(__file__).resolve().parents[2]/'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'
  sys.path.insert(0,str(codec_root))
  from smm.gd_october_job_codec import loads
  case=loads((Path(data['base_run'])/'seed/case.json').read_bytes())
  for snapshot in (data,prior):
   contract=snapshot['request']['rolling_cycle_boundary'];verify(contract['pins'])
   validate(contract,snapshot['request'],snapshot['accepted'],case,snapshot['values'],report=snapshot['reports'][-1])
  # Only proven file locations and their content bindings are metadata here.
  for request in (current_request,old_request):
   for key in ('prepared','actual','pins'):request['rolling_cycle_boundary'].pop(key)
 need(current_request==old_request,'完整数学request不等（含DA/状态/备用/终端SOC/逐日循环累计）')
 need(data['accepted']['SCED_response_sha256']==prior['accepted']['SCED_response_sha256'],'当前价格SCED来源不同')
 out=Path(output);out.mkdir(exist_ok=True);sf=out/f'q{q}_SOURCE.json';write(sf,data)
 write(out/f'q{q}_EQUAL.json',dict(quarter=q,current_owned_numeric_source_equal=True,no_repeated_model_build=True,native_calls=0,old_accepted_sha256=sha(old_accepted),old_typed_source_sha256=sha(source),new_source_sha256=sha(sf),metadata_may_differ=True))
