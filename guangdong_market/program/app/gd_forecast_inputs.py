"""Source-bound research forecast bridge. Explicit perfect-current-cell assumption."""
import json, hashlib
from pathlib import Path
from gd_forecast_scenarios import generate, vintage, digest

FILE='FORECAST_SCENARIO.json'
# Explicit new research classification; NOT an authenticated unit inventory.
DEFAULT_KIND={'30':'wind','37':'wind','33':'solar','4':'solar','8':'solar'}

def create(case, seed_dir, levels, *,random_seed=20261115):
    from gd_provincial15_model.adapter import convert_full_case
    view=convert_full_case(case);e=view['energy_inputs'];resources={}
    for bus in e['buses']:
        bus=str(bus);row=[e['renewable_mw'][bus,t] for t in range(96)]
        if max(row)==0:continue
        if bus not in DEFAULT_KIND:raise ValueError('新增新能源节点缺少风光类型声明：'+bus)
        kind=DEFAULT_KIND[bus]
        r=dict(kind=kind,bus=bus,capacity_mw=max(row)*1.3,day_ahead_mw=row,source='current seed exact three-5min mean; kind/capacity are explicit research assumptions')
        if kind=='solar':r['daylight']=[v>0 for v in row]
        resources['bus_'+bus]=r
    loads=[sum(e['load_mw'][b,t]for b in e['buses'])for t in range(96)]
    resources['system_load']=dict(kind='load',bus='system',capacity_mw=max(loads)*1.3,day_ahead_mw=loads,source='current seed load; capacity upper envelope1.3xpeak research assumption')
    z=generate(resources,levels,random_seed)
    z['notice']='合成研究误差。实时首15分钟采用该时段实际均值（完美当前时段信息假设）；后7时段仅用此前观测更新预测。并非严格无未来信息。'
    z['classification_assumption']=DEFAULT_KIND
    z['load_bus_shares']={str(b):[e['load_mw'][b,t]/loads[t] if loads[t] else 0 for t in range(96)]for b in e['buses']}
    z['baseline_case_digest']=digest({'load':[[str(b),t,v]for (b,t),v in sorted(e['load_mw'].items()) if t<96],'renewable':[[str(b),t,v]for (b,t),v in sorted(e['renewable_mw'].items()) if t<96]})
    z.pop('bundle_digest',None);z['bundle_digest']=digest(z)
    p=Path(seed_dir)/FILE;p.write_text(json.dumps(z,ensure_ascii=False,indent=2,allow_nan=False))
    return z

def baseline_digest(case):
    e=case['energy_inputs']
    return digest({'load':[[str(b),t,v]for (b,t),v in sorted(e['load_mw'].items()) if t<96],'renewable':[[str(b),t,v]for (b,t),v in sorted(e['renewable_mw'].items()) if t<96]})

def read_bundle(seed_dir):
    p=Path(seed_dir)/FILE
    if not p.exists():return None
    z=json.loads(p.read_text());expected=z.pop('bundle_digest')
    if digest(z)!=expected:raise ValueError('forecast bundle digest mismatch')
    from smm.gd_october_job_codec import loads
    from gd_provincial15_model.adapter import convert_full_case
    baseline=convert_full_case(loads((Path(seed_dir)/'case.json').read_bytes()))
    if baseline_digest(baseline)!=z['baseline_case_digest']:raise ValueError('forecast baseline does not match bound seed')
    z['bundle_digest']=expected;return z

def apply_forecast(case,seed_dir,issue_tick):
    """Mutate 15min full case. First actual, future forecasts. No DA mutation."""
    z=read_bundle(seed_dir)
    if z is None:return case
    if type(issue_tick)is not int or not 0<=issue_tick<96:raise ValueError('invalid issue_tick')
    end=min(96,issue_tick+8);e=case['energy_inputs'];proof={}
    for name,r in z['resources'].items():
        # Remove actual trajectory before calling forecaster; only past prefix enters.
        prior={k:v for k,v in r.items() if k!='actual_mw'}
        f=vintage(prior,issue_tick,r['actual_mw'][:issue_tick],end-issue_tick)
        vals=f['forecast_mw'];vals[0]=r['actual_mw'][issue_tick]
        for t,v in zip(range(issue_tick,end),vals):
            if r['kind']=='load':
                for b in e['buses']:e['load_mw'][b,t]=v*z['load_bus_shares'][str(b)][t]
            else:e['renewable_mw'][str(r['bus']),t]=v
        proof[name]={'forecast':f,'current_cell_perfect_information':True}
    case['_forecast_proof']={'bundle_digest':z['bundle_digest'],'issue_tick':issue_tick,'notice':z['notice'],'resources':proof}
    return case

def apply_actual(case,seed_dir):
    """Executed-hour audit/settlement loads use synthetic realised path, not DA."""
    z=read_bundle(seed_dir)
    if z is None:return case
    e=case['energy_inputs']
    for r in z['resources'].values():
        for t,v in enumerate(r['actual_mw']):
            if r['kind']=='load':
                for b in e['buses']:e['load_mw'][b,t]=v*z['load_bus_shares'][str(b)][t]
            else:e['renewable_mw'][str(r['bus']),t]=v
    return case
