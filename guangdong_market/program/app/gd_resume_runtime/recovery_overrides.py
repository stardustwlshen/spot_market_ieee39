"""Explicit new acceptance of preserved raw, never restoration of missing old bytes."""
from pathlib import Path
import json
from copy import deepcopy
from content_hash import sha
from .recovery_branch import pinned_value
read=lambda p:json.loads(Path(p).read_bytes())
def need(v,m):
 if not v:raise ValueError(m)
def validate_price(path,source):
 path=Path(path).resolve();z=read(path);current=Path(source)/'pipeline/price/q15/ACCEPTED.json'
 need(z.get('schema')=='gd.PRICE.reaccepted_preserved_raw.v1','恢复价格必须显式新接受schema')
 for k in ('strict_full_model_equal','names_equal','KKT_exactly_equal_original','prices_exactly_equal_original'):need(z.get(k)is True,'恢复价格证明缺少 '+k)
 need(z['new_native_calls']==0 and z['original_optimize_calls']==1 and z['original_records_modified']is False and z['original_source_restored']is False,'不允许伪造旧恢复或新优化')
 old=Path(z['original_accepted_path']).resolve()
 need(current.resolve()in (old,path),'恢复价格当前视图不是原始接受或同一新证明')
 need(pinned_value(z['source_pins'],old)==sha(old),'恢复价格原始接受摘要改变或未绑定')
 original=read(old)
 prep=(old.parent/'prepared').resolve()
 need(Path(z['original_prepared']).resolve()==prep and (path.parent/'prepared').resolve()==prep,'新视图必须保留原生prepared身份')
 for name in ('request','response'):need(Path(z['preserved_native_'+name]).resolve()==prep/'native_once'/f'{name}.json','恢复价格原生身份不同')
 need(z['raw_prices']==original['raw_prices']and z['KKT']==original['KKT']and z['KKT']['passed']is True,'恢复价格不等于保留原解结果')
 for k in ('run_id','short_run','day_quarter','SCED_response_sha256'):need(z['context'][k]==original['context'][k],'恢复价格物理身份改变')
 need(z['context']['day_quarter']==15 and z['formal']is False and z['payable']is False and z['safety_release']is False and z['formal_bill']is None,'恢复价格不可正式结算')
 for f,h in z['source_pins'].items():need(sha(f)==h,'恢复价格证据改变：'+f)
 return z

def load(path,source):
 path=Path(path).resolve();m=read(path)
 need(m.get('schema')=='gd.resume.recovery_overrides.v1' and Path(m['source_output']).resolve()==Path(source).resolve(),'恢复视图来源错误')
 need(set(m['prices'])=={'15'}and set(m['hours'])=={'3'},'恢复覆盖仅限缺失元数据关联q15/HOUR3')
 for f,h in m['source_pins'].items():need(sha(f)==h,'恢复覆盖证据改变：'+f)
 a=Path(m['prices']['15']);z=validate_price(a,source);hour=Path(m['hours']['3']);hz=read(hour);old=read(Path(source)/'pipeline/price/HOUR3.json')
 need(pinned_value(m['source_pins'],a)==sha(a)and pinned_value(m['source_pins'],hour)==sha(hour),'恢复覆盖未绑定')
 # New arithmetic may change only provenance; economic data/clock remain equal.
 compare=deepcopy(hz)
 for node,item in old['hour_prices']['RT'].items():
  need(item['source_sha256']==old['input_binding']['quarter_sources_sha256']and hz['hour_prices']['RT'][node]['source_sha256']==hz['input_binding']['quarter_sources_sha256'],'小时价格来源摘要未指向对应四价输入')
  compare['hour_prices']['RT'][node]['source_sha256']=item['source_sha256']
 for k,v in old.items():
  if k not in ('source_pins','input_binding'):need(compare.get(k)==v,'恢复小时经济或范围字段改变：'+k)
 for k,v in old['input_binding'].items():
  if k!='quarter_sources_sha256':need(hz['input_binding'].get(k)==v,'恢复小时输入范围改变：'+k)
 for f,h in hz['source_pins'].items():need(sha(f)==h,'恢复小时证据改变：'+f)
 need(pinned_value(hz['source_pins'],a)==sha(a),'恢复小时未绑定新q15')
 pins={str(path):sha(path),**m['source_pins'],**z['source_pins'],**hz['source_pins']}
 return dict(manifest_path=str(path),manifest_sha256=sha(path),prices=m['prices'],hours=m['hours'],source_pins=pins)
