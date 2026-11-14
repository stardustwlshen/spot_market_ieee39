"""Immutable research scenario compilation. No solver or result input is used."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import argparse,hashlib,json,math,shutil,sys
B=Path(__file__).resolve().parent
EXAMPLE=B/'guangdong_market_delivery_20261112/examples/ieee39_research'
PIPE=B/'guangdong_pipeline_20261112'
SCHEMA='gd.user_scenario.spec.v1'
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes(),parse_constant=lambda s:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
def need(v,s):
    if not v:raise ValueError(s)
def codec():
    sys.path.insert(0,str(B/'guangdong_market_delivery_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
    from smm.gd_october_job_codec import loads,dumps
    from smm.gd_october_research_forecast import prior_value
    return loads,dumps,prior_value
def base_paths():
    return {'case':EXAMPLE/'case.json','grid':EXAMPLE/'grid.json','policy':EXAMPLE/'policy.json','agc':EXAMPLE/'agc.json','pricing_spec':PIPE/'examples/research_pricing_spec.json','rt_assets':PIPE/'explicit_rt_assets/ASSETS.json',**{k:PIPE/'explicit_rt_assets'/n for k,n in [('static','static.json'),('preparation','preparation.json'),('priors','priors.codec.json')]}}
def defaults():
    p=base_paths();s=read(p['static']);policy=read(p['policy'])
    offers={k:[z['price'] for z in u['original_offer']] for k,u in s['resources'].items() if 'original_offer' in u}
    return dict(schema=SCHEMA,name='IEEE39 独立研究场景',description='基于保留的合成预测、准备初态与报价；不是广东实测运营数据。',solar_capacity_factor=1.0,thermal_offer_prices=offers,minimum_adjustable_prices={k:policy['minimum_adjustable_prices'][k]['yuan_per_mwh'] for k in offers})
def checked_spec(v):
    d=defaults();need(type(v)is dict and set(v)==set(d),'场景字段缺失或不支持；禁止静默忽略')
    need(v['schema']==SCHEMA,'unsupported scenario schema')
    need(type(v['name'])is str and 0<len(v['name'].strip())<=100,'场景名称需 1–100 字符')
    need(type(v['description'])is str and len(v['description'])<=2000,'场景说明过长')
    def number(x,lo,hi):need(type(x)in(int,float)and math.isfinite(x)and lo<=x<=hi,'数值须有限且在明确研究界面范围内')
    number(v['solar_capacity_factor'],0.,1.)
    for k in ('thermal_offer_prices','minimum_adjustable_prices'):need(type(v[k])is dict and set(v[k])==set(d[k]),'完整热机资源集合 required')
    for g,old in d['thermal_offer_prices'].items():
        prices=v['thermal_offer_prices'][g];need(type(prices)is list and len(prices)==len(old),'完整热机分段数量 required')
        for x in prices:number(x,0.,1800.)
        need(all(a<=b for a,b in zip(prices,prices[1:])),'分段报价必须非递减')
        number(v['minimum_adjustable_prices'][g],0.,1800.)
    return deepcopy(v)
def solar_means(prior,prior_value,day_start,n):
    start=datetime.fromisoformat(day_start);out={}
    for rid,r in prior['resources'].items():
        for t in range(n):
            q,k=divmod(t,3)
            a=prior_value(prior,rid,start+timedelta(minutes=15*q));z=prior_value(prior,rid,start+timedelta(minutes=15*(q+1)))
            key=(str(r['bus']),t);out[key]=out.get(key,0.)+a+(z-a)*(k+.5)/3
    return out
def compile_scenario(spec,output):
    spec=checked_spec(spec);out=Path(output).resolve();need(not out.exists(),'另存目录必须不存在；不能覆盖原场景或已运行输入')
    loads,dumps,prior_value=codec();paths=base_paths();pins={str(p.relative_to(B)):sha(p) for p in paths.values()};case=loads(paths['case'].read_bytes());original=deepcopy(case);static=read(paths['static']);policy=read(paths['policy']);prior=loads(paths['priors'].read_bytes());base=defaults();changes=[]
    for g,prices in spec['thermal_offer_prices'].items():
        u=case['energy_inputs']['thermal'][g];offer=static['resources'][g]['original_offer']
        for i,(z,x) in enumerate(zip(offer,prices)):
            if z['price']!=x:changes.append(dict(field=f'thermal.{g}.offer[{i}]',old=z['price'],new=x,unit='yuan/MWh'))
            z['price']=x
        u['base_bid_yuan_per_h']={t:u['bid_base_mw']*prices[0] for t in u['base_bid_yuan_per_h']}
        for seg,price in zip(u['segments'],prices[1:]):seg['price_yuan_per_mwh']={t:price for t in seg['price_yuan_per_mwh']}
        # Existing event-energy decomposition explicitly prices the curve at first incremental block.
        # Recompute that derived cost, never leave stale old-price startup/shutdown curves.
        for kind in ('startup','shutdown'):
            for row in case['curves'][g][kind]:row['cost']=case['no_load_cost'][g]+row['mean']*prices[1]
        fee=spec['minimum_adjustable_prices'][g];f=policy['minimum_adjustable_prices'][g]
        if f['yuan_per_mwh']!=fee:changes.append(dict(field=f'minimum_adjustable_prices.{g}',old=f['yuan_per_mwh'],new=fee,unit='yuan/MWh'))
        f.update(yuan_per_mwh=fee,source='User-edited independent research minimum-adjustable-output bid; not actual operator submission')
    factor=spec['solar_capacity_factor'];saved=prior['contract']['sources']['prior:solar_mw'];solar=json.loads(saved['artifact']);old_solar=deepcopy(solar)
    n=len(case['energy_inputs']['horizon']['periods']);old_values=solar_means(old_solar,prior_value,case['contract']['day_start'],n)
    # The bundled wind and solar buses are disjoint. Refuse a different template rather than scale wind too.
    wind=json.loads(prior['contract']['sources']['prior:wind_mw']['artifact'])
    need(not ({str(v['bus']) for v in solar['resources'].values()} & {str(v['bus']) for v in wind['resources'].values()}),'该光伏编辑器不支持风光同节点模板')
    for key,value in old_values.items():need(abs(original['energy_inputs']['renewable_mw'][key]-value)<=1e-8,'原 DA 光伏与因果先验的15→5分钟均值不一致')
    if factor!=1.:
        changes.append(dict(field='solar_capacity_factor',old=1.,new=factor,unit='dimensionless'))
        for r in solar['resources'].values():r['scale_mw']*=factor
        values=solar_means(solar,prior_value,case['contract']['day_start'],n)
        for key,value in values.items():case['energy_inputs']['renewable_mw'][key]=value
        saved['artifact']=canonical(solar);saved['sha256']=hashlib.sha256(saved['artifact']).hexdigest()
    # Same explicitly simulated vintage, no future actual data and no changed midnight/preparation input.
    need(solar['uses_future_actual']is False and solar['available_at']==old_solar['available_at'],'synthetic vintage unchanged')
    for (bus,t),v in old_values.items():
        if t<3 or 288<=t<309:need(case['energy_inputs']['renewable_mw'][bus,t]==v==0.,'光伏编辑不能改变午夜准备/次日初始尾段')
    for key in ('reserve','batteries','battery_contract','no_load_cost'):need(case[key]==original[key],'锁定物理输入被修改 '+key)
    for key in ('load_mw','physical_tie_mw','lines','sections'):need(case['energy_inputs'][key]==original['energy_inputs'][key],'锁定网络/负荷被修改 '+key)
    note=dict(schema=SCHEMA,spec_sha256=digest(spec),base_input_pins=pins,description='Independent user research edit; original files unchanged. Solar capacity is an explicit forecast hypothesis, not installed/qualified capacity. Offers are synthetic, constant over retained 48h scope.',changed_fields=changes,actual_data=False,formal_submission=False)
    case['contract']['user_scenario']=note
    case['contract']['case_assumptions']['scenario_numbers_unchanged']=not bool(changes)
    case['contract']['case_assumptions']['original_scenario_preserved_in_separate_files']=True
    out.mkdir(parents=True);(out/'rt_assets').mkdir()
    def put(path,data):
        with (out/path).open('xb') as f:f.write(data)
    put('case.json',dumps(case));put('grid.json',paths['grid'].read_bytes());put('policy.json',canonical(policy));put('agc.json',paths['agc'].read_bytes());put('pricing_spec.json',paths['pricing_spec'].read_bytes());put('spec.json',canonical(spec))
    put('rt_assets/static.json',canonical(static));put('rt_assets/preparation.json',paths['preparation'].read_bytes());put('rt_assets/priors.codec.json',dumps(prior))
    assets=read(paths['rt_assets']);assets['basis']='User-compiled independent research inputs; no actual telemetry or results; original preparation retained and checked unchanged';assets['user_spec_sha256']=digest(spec)
    for r in assets['files'].values():r['sha256']=sha(out/'rt_assets'/r['path'])
    put('rt_assets/ASSETS.json',canonical(assets));put('bundle.json',canonical(dict(schema='gd.delivery.scuc_inputs.v1',case_file='case.json',grid_file='grid.json',policy_file='policy.json',agc_file='agc.json',scenario_manifest_file='SCENARIO.json',basis='Independent user research scenario; no formal operating data')))
    files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    manifest=dict(schema='gd.user_scenario.v1',name=spec['name'],description=spec['description'],spec_file='spec.json',entries={'bundle':'bundle.json','pricing_spec':'pricing_spec.json','rt_assets':'rt_assets/ASSETS.json'},files=files,research_only=True,compiler_sha256=sha(__file__),base_input_pins=pins,changed_fields=changes,locked_constraints={'positive_reserve_mw':400,'accident_reserve_mw':400,'original_AGC_awards':'unchanged synthetic five thermal10MW each','new_BESS_VRE_ancillary_qualification':False,'load_network_midnight_preparation_unchanged':True,'HiGHS_relative_gap':.001,'solar_semantics':'synthetic forecast capacity derating0..1; identical declared causal vintage; 15min knots interpolated to5min interval means over original48h','offer_semantics':'0..1800 is UI research range, not current official parameter certification; full48h constant bid research'},validation_status='compiled_and_cross_source_checked_not_optimized',native_calls=0)
    manifest['identity']=digest(manifest);put('SCENARIO.json',canonical(manifest))
    need(all(sha(B/p)==h for p,h in pins.items()),'原模板意外改变')
    return validate_scenario(out)
def validate_scenario(path):
    root=Path(path).resolve();m=read(root/'SCENARIO.json')
    need(m.get('schema')=='gd.user_scenario.v1' and m.get('research_only')is True,'unsupported scenario receipt')
    need(m.get('identity')==digest({k:v for k,v in m.items() if k!='identity'}),'场景身份发生变化')
    need(m.get('files') and set(m.get('entries',{}))=={'bundle','pricing_spec','rt_assets'},'场景依赖不完整')
    for p,h in m['files'].items():
        q=Path(p);need(not q.is_absolute()and '..'not in q.parts,'场景路径逃逸')
        z=(root/q).resolve();need(z.is_relative_to(root)and z.is_file()and sha(z)==h,'场景文件缺失或改变 '+p)
    for p in m['entries'].values():need(p in m['files'],'场景入口未绑定')
    checked_spec(read(root/m['spec_file']))
    assets=read(root/m['entries']['rt_assets'])
    for r in assets['files'].values():
        p=root/'rt_assets'/r['path'];need(p.resolve().is_relative_to(root)and sha(p)==r['sha256'],'RT assets pin mismatch')
    return m
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['defaults','compile','validate']);p.add_argument('--spec');p.add_argument('--output');p.add_argument('--scenario');a=p.parse_args()
    if a.command=='defaults':out=defaults()
    elif a.command=='compile':need(a.spec and a.output,'--spec and --output required');out=compile_scenario(read(a.spec),a.output)
    else:need(a.scenario,'--scenario required');out=validate_scenario(a.scenario)
    print(json.dumps(out,ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
