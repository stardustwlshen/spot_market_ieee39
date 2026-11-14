"""Installed-relative, isolated zero-native hourly AGC host adapter."""
from pathlib import Path
import json,subprocess,sys,os
B=Path(__file__).resolve().parent
HOST=B/'gd_hourly_agc_runtime/host.py'
def need(ok,msg):
 if not ok:raise ValueError(msg)
def read(p):return json.loads(Path(p).read_bytes())
def call(args):
 q=subprocess.run([sys.executable,'-B',str(HOST),*map(str,args)],capture_output=True,text=True,timeout=600,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
 need(q.returncode==0,'操作失败，未授权求解；保留记录，不复用旧成功：'+q.stderr[-1200:])
 try:return json.loads(q.stdout.strip().splitlines()[-1])
 except Exception:raise ValueError('宿主未返回完整状态，不宣称成功')
def register(run,source_window,hour,index,confirmed=False):
 need(confirmed,'须先确认研究假设');need(bool(str(run).strip()),'先选择已有实时运行');index=Path(index).expanduser().resolve();need(not index.exists(),'输出已存在，拒绝覆盖；请换新回执目录')
 call(['register','--run',run,'--source-window',source_window,'--hour',hour,'--index',index]);r=read(index/'RECEIPT.json');i=read(index/'INDEX.json');need(i['native_calls']==0 and r['safety_release']is False,'只接受0求解研究回执')
 return dict(status='研究小时竞价回执已生成',run=str(Path(run).resolve()),index=str(index),source_window=source_window,hour=hour,quotes_and_qualification=r['input']['units'],research_parameters=r['input'].get('parameters',{}),awards_mw=r['result']['awards_mw'],clearing_price=r['result']['price'],source_cutoff=r['verified_plan']['cutoff'],hypothetical_available_at=r['verified_plan']['available_at'],auction_as_of=r['context']['as_of'],hour_start=r['context']['hour_start'],already_consumed=False,safety_release=False,native_calls=0)
def build(run,window,index,out,confirmed=False):
 need(confirmed,'须先确认纯构模且不求解');need(Path(index,'INDEX.json').is_file(),'先生成同源回执索引');out=Path(out).expanduser().resolve();need(not out.exists(),'输出已存在，拒绝覆盖；请换新构模目录')
 call(['build','--run',run,'--window',window,'--index',index,'--output',out]);proof=read(out/'HOST_AGC.json');s=read(out/'SUMMARY.json');need(proof['native_calls']==0,'不得自动求解')
 return dict(status='独立freeSCUC输入已构建，尚未求解',run=str(Path(run).resolve()),window=window,output=str(out),columns=s['columns'],rows=s['rows'],domain_start=s['domain_start'],binding=proof,old_SCED_consumed=False,safety_release=False,native_calls=0)
