"""Fresh DA98 -> independent LMP -> provincial RT base. Never reuse a fixed DA result."""
from pathlib import Path
import json,hashlib,subprocess,sys,os
APP=Path(__file__).resolve().parent

def inputs_identity(source_base):
    root=Path(source_base).expanduser().resolve()
    paths=[root/'SHORT_CONTEXT.json',root/'INITIAL_CARRY.json',root/'seed/case.json',root/'seed/agc.json',root/'seed/request.json',root/'seed/policy.json']
    forecast=root/'seed/FORECAST_SCENARIO.json'
    if forecast.exists():paths.append(forecast)
    for p in paths:
        if not p.is_file():raise ValueError('缺少场景输入：'+str(p))
    # Fail before any native solve if the required RT bid policy is absent or incomplete.
    from gd_provincial_inputs import codec
    import math
    loads,_=codec();case=loads((root/'seed/case.json').read_bytes())
    policy=json.loads((root/'seed/policy.json').read_text())
    prices=policy.get('minimum_adjustable_prices',{})
    for g in case['energy_inputs']['thermal']:
        value=prices.get(g,{}).get('yuan_per_mwh')
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ValueError('缺少或无效的实时最小出力报价：'+g)
    context=json.loads((root/'SHORT_CONTEXT.json').read_text())
    for name,expected in context.get('seed_pins',{}).items():
        f=Path(name);f=f if f.is_absolute() else root/f
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest()!=expected:raise ValueError('场景输入来源校验失败：'+f.name)
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def commands(source_base,output,delta=.1):
    source_base=Path(source_base).resolve();out=Path(output).resolve()
    return [
      [sys.executable,'-B',str(APP/'gd_provincial_da_worker.py'),'--source-base',str(source_base),'--output',str(out/'da')],
      [sys.executable,'-B',str(APP/'gd_provincial_price_worker.py'),str(out/'da'),str(out/'da_price'),str(delta)],
      [sys.executable,'-B',str(APP/'gd_provincial_register_worker.py'),str(out/'da'),str(out/'da_price'),str(source_base/'seed/case.json')],
      [sys.executable,'-B',str(APP/'gd_provincial_base_worker.py'),str(source_base),str(out/'da/MANIFEST.json'),str(out/'rt')],
    ]

def prepare_fresh(source_base,output,*,confirmed=False,delta=.1,progress=None):
    if confirmed is not True:raise ValueError('请确认启动日前计算')
    if not isinstance(delta,(int,float)) or not 0<=delta<=1:raise ValueError('δ须在0至1之间')
    pins=inputs_identity(source_base);out=Path(output).resolve()
    if out.exists():raise ValueError('运行目录已存在，请使用新运行名；不覆盖或自动重算')
    out.mkdir(parents=True)
    def save(name,z):(out/name).write_text(json.dumps(z,ensure_ascii=False,indent=2,allow_nan=False))
    save('UI_INPUT.json',dict(source_base=str(Path(source_base).resolve()),source_pins=pins,pricing_delta=delta,formal=False,day_periods=98,settlement_periods=96,grid_minutes=15))
    labels=['日前机组组合及调度','日前节点电价','校验日前结果','准备实时初始状态']
    for i,(label,cmd) in enumerate(zip(labels,commands(source_base,out,delta))):
        if inputs_identity(source_base)!=pins:raise ValueError('输入在计算期间发生变化，已停止；旧结果保留')
        if progress:progress(i,label)
        save('PROGRESS.json',dict(stage=i,label=label,status='running'))
        q=subprocess.run(cmd,capture_output=True,text=True,timeout=8000,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1'})
        save('STEP_'+str(i)+'.json',dict(command=cmd,returncode=q.returncode,stdout=q.stdout,stderr=q.stderr))
        if q.returncode:
            save('PROGRESS.json',dict(stage=i,label=label,status='failed'))
            raise ValueError(label+'失败，结果已保留：'+q.stderr[-1200:])
    save('PROGRESS.json',dict(status='ready',label='日前完成，可启动实时',stage=4))
    return dict(status='provincial_fresh_DA_ready',da=str(out/'da'),run=str(out/'rt'),formal=False,native_calls=3)
