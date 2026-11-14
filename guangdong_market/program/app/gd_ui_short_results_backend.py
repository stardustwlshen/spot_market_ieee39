"""Read-only result reader: fixed bundled reader, no user-selected executable."""
from pathlib import Path
import sys,os,subprocess,json
B=Path(__file__).resolve().parent
def read_results(da,run,window,price=''):
 if not str(da).strip() or not str(run).strip():raise ValueError('请填写同源日前与短域运行目录')
 if type(window)is not int or not 0<=window<=94:raise ValueError('仅支持已安装 CLI1 窗口0–94')
 q=subprocess.run([sys.executable,'-B',str(B/'gd_ui_short_results_worker.py'),str(da),str(run),str(window),str(price)],capture_output=True,text=True,timeout=600,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
 try:z=json.loads(q.stdout.strip().splitlines()[-1])
 except Exception:raise ValueError('只读校验未完成：'+q.stderr[-500:])
 if q.returncode or z.get('status')!='verified_read_only':raise ValueError(z.get('error','只读校验拒绝'))
 if z['native_calls']!=0 or any(z[k]is not False for k in ('formal','safety_release','payable')):raise ValueError('研究结果状态不符')
 return z
