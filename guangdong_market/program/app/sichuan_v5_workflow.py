"""Sichuan-only execution/provenance. Neither writes nor reads Guangdong runs."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from market_rule_profile import SICHUAN_V5, require_profile
from sichuan_v5_ancillary import Award, prepare_energy_boundary
from sichuan_v5_demo import fixture, forecast_callback, research_values
from sichuan_v5_energy import PERIODS, day_ahead, realtime


def json_bytes(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _sources():
    here=Path(__file__).resolve().parent
    sources=sorted(here.glob('sichuan_v5_*.py'))+[here/'market_rule_profile.py']
    return {f.name:digest(f.read_bytes()) for f in sources}


def run(root, *, month, load_factor=1., solar_factor=1., coal_offer=320., rt_error=.025,
        periods=4, storage_product='spot', regulation_up=0., reserve_up=0., progress=None):
    if storage_product not in ('spot','none'):
        raise ValueError('当前没有储能辅助服务测试和持续时长数据，只支持现货/不参与')
    if (type(periods) is not int or not 1<=periods<=PERIODS
            or not all(type(v) in (float,int) and 0.<=v<=100. for v in (regulation_up,reserve_up))):
        raise ValueError('运行范围或辅助服务占位无效')
    forecast=forecast_callback(rt_error)
    case, inputs = fixture(month, load_factor=load_factor, solar_factor=solar_factor, coal_offer=coal_offer)
    if storage_product!='spot':
        case=replace(case,storage_plan=tuple({'ES1':0.} for _ in range(PERIODS)))
    awards=tuple(Award(t,'C1',regulation_up_mw=regulation_up,reserve_up_mw=reserve_up)
                 for t in range(PERIODS)) if regulation_up or reserve_up else ()
    holds,disabled=prepare_energy_boundary(unit_ids=tuple(r.id for r in case.resources),
                    storage_market={'ES1':(storage_product,)*PERIODS}, awards=awards)
    inputs.update(case=asdict(case), rt_error=rt_error, requested_periods=periods, storage_product=storage_product,
                  user_declared_ancillary_awards=[asdict(a) for a in awards],
                  rules= SICHUAN_V5.identity(), runtime_sources=_sources())
    directory=Path(root).resolve()
    if directory.name!=SICHUAN_V5.results_folder:
        raise ValueError('四川研究结果只能保存到独立规则目录')
    name=datetime.now(timezone.utc).strftime('scv5_%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:12]
    target=directory/name
    target.mkdir(parents=True,exist_ok=False)
    files={}
    def save(key,obj):
        data=json_bytes(obj)
        with (target/key).open('xb') as f:
            f.write(data)
        files[key]=digest(data)
    def tell(message):
        if progress:
            progress(message)
    save('INPUT.json',inputs)
    try:
        tell('正在计算日前预出清和节点参考价（不结算）…')
        preclear=day_ahead(case,spot_disabled=disabled)
        save('DA_PRECLEAR.json',preclear)
        # V5 DA preclear is a reference. This prototype's single synthetic
        # preclear commitment stands in for the separate official commitment.
        if awards:
            tell('校核外部声明的调频／备用容量占位…')
            day_ahead(case,fixed_commitment=preclear,holds=holds,spot_disabled=disabled)
        tell('正在执行 15 分钟滚动出清和节点定价…')
        rt=realtime(case,preclear,executed_periods=periods,holds=holds,spot_disabled=disabled,window_inputs=forecast)
        money=[research_values(case,row) for row in rt]
        save('RT_EXECUTED.json',rt)
        save('RT_RESEARCH_VALUES.json',money)
        status='full_day_research' if periods==PERIODS else 'partial_day_research'
        save('RECEIPT.json',dict(schema='ieee39.sichuan_v5.receipt.v1',rules=SICHUAN_V5.identity(),status=status,
             quarters=periods, formal_settlement=False, price_fallback_applied=False, data_files=files.copy()))
    except Exception as exc:
        save('FAILURE.json',dict(rules=SICHUAN_V5.identity(),error=str(exc),failed_research=True,automatic_restart=False))
        raise
    return name, load(directory,name)


def archive_names(root):
    path=Path(root).resolve()
    if path.name!=SICHUAN_V5.results_folder or not path.is_dir():
        return []
    # Never create a result or launch a worker on archive browsing.
    return sorted((f.name for f in path.iterdir() if not f.is_symlink() and f.is_dir()
                   and re.fullmatch(r'scv5_\d{8}T\d{6}Z_[a-f0-9]{12}',f.name)),reverse=True)[:200]


def load(root,name):
    directory=Path(root).resolve()
    if directory.name!=SICHUAN_V5.results_folder or not re.fullmatch(r'scv5_\d{8}T\d{6}Z_[a-f0-9]{12}',name):
        raise ValueError('不是四川研究档案')
    path=directory/name
    if path.is_symlink():
        raise ValueError('不跟随结果目录链接')
    def read(key,expected=None):
        p=path/key
        if p.is_symlink() or not p.is_file() or p.stat().st_size>64*1024*1024:
            raise ValueError('缺少可核验的结果：'+key)
        data=p.read_bytes()
        if expected is not None and digest(data)!=expected:
            raise ValueError('结果校验不匹配：'+key)
        return json.loads(data)
    if not (path/'RECEIPT.json').exists():
        reason=read('FAILURE.json')
        require_profile(reason.get('rules'),SICHUAN_V5)
        raise ValueError('未完成的研究任务：'+reason['error'])
    receipt=read('RECEIPT.json')
    require_profile(receipt.get('rules'),SICHUAN_V5)
    required={'INPUT.json','DA_PRECLEAR.json','RT_EXECUTED.json','RT_RESEARCH_VALUES.json'}
    files=receipt['data_files']
    if (set(files)!=required or not all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{64}',v) for v in files.values())
            or receipt.get('schema')!='ieee39.sichuan_v5.receipt.v1'
            or receipt.get('formal_settlement') is not False):
        raise ValueError('四川研究凭证格式有误')
    data={key:read(key,files[key]) for key in sorted(required)}
    require_profile(data['INPUT.json'].get('rules'),SICHUAN_V5)
    n=receipt['quarters']
    if (type(n) is not int or not 1<=n<=PERIODS
            or receipt.get('status')!=('full_day_research' if n==PERIODS else 'partial_day_research')
            or data['INPUT.json']['requested_periods']!=n
            or any(len(data[key])!=count or any(row.get('quarter')!=i for i,row in enumerate(data[key]))
               for key,count in [('DA_PRECLEAR.json',PERIODS),('RT_EXECUTED.json',n),('RT_RESEARCH_VALUES.json',n)])):
        raise ValueError('四川研究结果时段记录缺项或顺序有误')
    return {'receipt':receipt, 'inputs':data['INPUT.json'], 'da':data['DA_PRECLEAR.json'],
            'rt':data['RT_EXECUTED.json'],'values':data['RT_RESEARCH_VALUES.json']}
