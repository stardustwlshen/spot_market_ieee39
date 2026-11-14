"""Bound, read-only pricing-information clock. Never alters historical cutoffs."""
from datetime import datetime
from pathlib import Path
import hashlib,json

def time(value):
 if not isinstance(value,str):raise ValueError('missing declared clock')
 x=datetime.fromisoformat(value)
 if x.utcoffset() is None:raise ValueError('timezone required')
 return x

def evaluate(accepted,*,source_sha256=None):
 ctx=accepted.get('context',{});raw=ctx.get('post_reference_pricing_availability')
 result=dict(schema='gd.price.information_time.v1',run_id=ctx.get('run_id'),quarter=ctx.get('day_quarter'),source_sha256=source_sha256,status='not_declared',SCUC_data_cutoff=None,pricing_input_available_at=None,label='未声明独立输入可用时标；不能推定因果可用',formal_use_allowed=False,preclear_use_allowed=False)
 if raw is None:return result
 if not isinstance(raw,dict):return dict(result,status='unknown')
 result.update(status='unknown',SCUC_data_cutoff=raw.get('SCUC_data_cutoff'),pricing_input_available_at=raw.get('pricing_input_available_at'))
 try:cutoff=time(result['SCUC_data_cutoff']);available=time(result['pricing_input_available_at'])
 except (ValueError,TypeError):return result
 if raw.get('not_retroactively_available') is not True:return result
 if available>cutoff:
  result.update(status='retrospective_research',label='相对'+cutoff.strftime('%H:%M')+'截止的事后研究价，最早'+available.strftime('%H:%M'))
 else:result.update(status='declared_research_clock',label='已声明研究输入可用时标；不等于正式出清许可')
 return result

def from_accepted(path):
 p=Path(path);raw=p.read_bytes()
 return evaluate(json.loads(raw),source_sha256=hashlib.sha256(raw).hexdigest())

def require_use(path,*,as_of,purpose,expected_run_id,expected_quarter,expected_source_sha256):
 """Explicit use check; only a bound research read may pass this gate."""
 z=from_accepted(path)
 if (z['run_id'],z['quarter'],z['source_sha256'])!=(expected_run_id,expected_quarter,expected_source_sha256):raise ValueError('pricing clock source/run/quarter mismatch')
 if z['status'] not in ('retrospective_research','declared_research_clock'):raise ValueError('unknown information clock; no timing permission inferred')
 if time(as_of)<time(z['pricing_input_available_at']):raise ValueError('pricing inputs not available as_of; original cutoff cannot be moved retrospectively')
 if purpose!='research_read':raise ValueError('post-reference price is research-only; formal settlement/preclear consumption forbidden')
 return dict(z,research_read_allowed=True,as_of=as_of,purpose=purpose)
