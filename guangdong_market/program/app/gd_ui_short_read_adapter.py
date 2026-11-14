"""Strict read adapter for the two fixed installed CLI1 copies; no native calls.
Absolute provenance is verified in the originating installed copy, never rewritten.
"""
from pathlib import Path
import sys,json,subprocess,re
B=Path(__file__).resolve().parent
ENGINE=B.parent/'engines/guangdong_short_price_v1/guangdong_short_price_user_cli_v1_20261112'
RTS={'rt':B.parent/'engines/guangdong_short_rt_v1/guangdong_rt_short_user_cli_v1_20261112/program.py','price':ENGINE.parent/'guangdong_rt_short_user_cli_v1_20261112/program.py'}

def own_reader(run):
 ctx=json.loads((Path(run)/'SHORT_CONTEXT.json').read_bytes())
 hits=[key for key,p in RTS.items()if str(p)in ctx.get('code_pins',{})]
 if len(hits)!=1:raise ValueError('仅支持固定安装的CLI1原始来源，不执行票据选择的代码')
 return hits[0]

def rt_read(run,w,kind):
 key=own_reader(run)
 p=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),kind,key,str(run),str(w)],capture_output=True,text=True,timeout=240)
 if p.returncode:raise ValueError('原安装来源严格重放拒绝：'+p.stderr[-600:])
 return json.loads(p.stdout.strip().splitlines()[-1])

def load_prefix(da,run,w):
 sys.path.insert(0,str(ENGINE));import loader
 def pair(r,k):
  z=rt_read(r,k,'pair');loader.need(z['status']=='accepted'and z['native_calls']==0,'已接受原RT结果必需');return z
 loader.preflight=pair
 values,proof,case=loader.inputs(da,run,w)
 carry=rt_read(run,w+1,'carry')
 loader.need(carry['executed_end_exclusive']==3*(w+1)and carry['issued_end_exclusive']==3*(w+2)and carry['formal']is False and carry['safety_release']is False,'执行/下发时标或研究属性不符')
 return values,proof,case,carry

def execution_view(da,run,hour,output):
 sys.path.insert(0,str(ENGINE));import loader
 da,run,ctx,req=loader.context(da,run);loader.need(type(hour)is int and 0<=hour<24,'hour0..23')
 # Validate the actual available prefix before reporting incomplete execution.
 available=sorted(int(p.name[1:])for p in (run/'handoff').glob('W*')if p.is_dir()and re.fullmatch('W[0-9]+',p.name))
 loader.need(available and available==list(range(1,max(available)+1)),'无连续研究交接回执，不能声称已执行')
 k=min(4*(hour+1),max(available));(accepted,r,x),proof,case,carry=load_prefix(da,run,k-1)
 end=12*(hour+1);loader.need(carry['executed_end_exclusive']>=end,f"同源严格重放通过，但仅 {carry['executed_end_exclusive']} 格已执行；目标小时需至 {end} 格（每小时12格），下发未执行不得核算")
 start=12*hour;values={key:v for key,v in x.items()if(m:=re.search(r'(\d+)\]$',key))and start<=int(m[1])<end}
 files=[run/'SHORT_CONTEXT.json',run/f'handoff/W{k}/HANDOFF.json',run/f'handoff/W{k}/CARRY.json',run/'seed/request.json',run/'seed/case.json',da/'ARTIFACTS.json',Path(__file__),ENGINE/'loader.py'];pins={str(p):loader.sha(p)for p in files};pins.update(proof['source_pins'])
 view=dict(schema='gd.short.PRICE.execution_view.v1',run_id=ctx['run_id'],run=str(run),selected_DA=str(da),hour=hour,origin=r['origin'],executed_end=carry['executed_end_exclusive'],issued_end=carry['issued_end_exclusive'],cells=list(range(start,end)),values=values,source_pins=pins,formal_bill=None,safety_release=False,read_adapter='fixed_original_install_context_v1')
 loader.write(output,view);return dict(status='prepared',native_calls=0,executed_cells=12,hour=hour,execution=str(output),formal=False,safety_release=False,payable=False)

if __name__=='__main__':
 if sys.argv[1]in ('pair','carry'):
  import importlib.util
  fixed=RTS[sys.argv[2]];spec=importlib.util.spec_from_file_location('fixed_installed_rt',fixed);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  r,c=m.context(sys.argv[3]);w=int(sys.argv[4])
  if sys.argv[1]=='carry':z=m.read(m.current_carry(r,c,w))
  else:z=m.preflight(r,w);m.need(z['status']=='accepted','accepted pair required')
  print(json.dumps(z))
 else:
  try:print(json.dumps(execution_view(sys.argv[2],sys.argv[3],int(sys.argv[4]),sys.argv[5])))
  except Exception as e:print(json.dumps(dict(status='blocked',error=str(e),native_calls=0,formal=False,safety_release=False,payable=False)));raise SystemExit(1)
