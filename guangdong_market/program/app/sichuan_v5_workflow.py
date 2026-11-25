"""Sichuan-only execution/provenance. Neither writes nor reads Guangdong runs."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import json
import os
from pathlib import Path
import re
import uuid

from market_rule_profile import SICHUAN_V5, require_profile
from sichuan_v5_agc_stages import AGC_SEGMENTS, ROUNDING_TOL, reserve_offers, stages
from sichuan_v5_ancillary import Award, prepare_energy_boundary
from sichuan_v5_demo import fixture, forecast_callback, research_values
from sichuan_v5_energy import PERIODS, day_ahead, intraday, realtime
from sichuan_v5_intraday import Update
from sichuan_v5_price_review import parse_review_request, resolve_run_review


def json_bytes(record):
    def convert(value):
        if isinstance(value,Decimal):
            return str(value)
        if isinstance(value,frozenset):
            return sorted(value)
        raise TypeError(f'无法保存：{type(value).__name__}')
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False,default=convert).encode('utf-8')


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def archive_scenario(inputs):
    """Short recorded inputs for the archive picker, never used by the solver."""
    keys=('month','load_factor','coal_offer','requested_periods','storage_product')
    return {k:inputs[k] for k in keys if k in inputs}


def _sources():
    here=Path(__file__).resolve().parent
    sources=sorted(here.glob('sichuan_v5_*.py'))+[here/'market_rule_profile.py']
    return {f.name:digest(f.read_bytes()) for f in sources}


def run(root, *, month, load_factor=1., solar_factor=1., coal_offer=320., rt_error=.025,
        periods=4, storage_product='spot', regulation_up=0., reserve_up=0., intraday_update=None, hydro_cascade=False, reserve_gross=0., clear_agc=False, equal_offer_priority=False, progress=None):
    if type(equal_offer_priority) is not bool:
        raise ValueError('日前同价次序选项必须为布尔值')
    if storage_product not in ('spot','none'):
        raise ValueError('当前没有储能辅助服务测试和持续时长数据，只支持现货/不参与')
    if (type(periods) is not int or not 1<=periods<=PERIODS
            or not all(type(v) in (float,int) and 0.<=v<=100. for v in (regulation_up,reserve_up))):
        raise ValueError('运行范围或辅助服务占位无效')
    if (type(reserve_gross) not in (float,int) or not 0.<=reserve_gross<=300.
            or reserve_gross and reserve_up):
        raise ValueError('研究备用需求应在0–300 MW；不能同时手填备用占位')
    if type(clear_agc) is not bool or clear_agc and (not reserve_gross or regulation_up or reserve_up):
        raise ValueError('启用分段调频须启用研究备用，且不得同时手填调频／备用')
    if intraday_update is not None and not isinstance(intraday_update,Update):
        raise ValueError('日内优化必须使用显式生效时点和负荷边界声明')
    forecast=forecast_callback(rt_error)
    case, inputs = fixture(month, load_factor=load_factor, solar_factor=solar_factor, coal_offer=coal_offer,
                           hydro_cascade=hydro_cascade)
    if storage_product!='spot':
        case=replace(case,storage_plan=tuple({'ES1':0.} for _ in range(PERIODS)))
    awards=tuple(Award(t,'C1',regulation_up_mw=regulation_up,reserve_up_mw=reserve_up)
                 for t in range(PERIODS)) if regulation_up or reserve_up else ()
    holds,disabled=prepare_energy_boundary(unit_ids=tuple(r.id for r in case.resources),
                    storage_market={'ES1':(storage_product,)*PERIODS}, awards=awards)
    inputs.update(case=asdict(case), rt_error=rt_error, requested_periods=periods, storage_product=storage_product,
                  user_declared_ancillary_awards=[asdict(a) for a in awards],
                  research_reserve_gross_mw=reserve_gross,
                  clears_segmented_agc=clear_agc, equal_offer_priority=equal_offer_priority,
                  intraday_update=asdict(intraday_update) if intraday_update is not None else None,
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
        preclear=day_ahead(case,spot_disabled=disabled,**({'equal_offer_priority':True} if equal_offer_priority else {}))
        save('DA_PRECLEAR.json',preclear)
        reference=preclear
        if reserve_gross:
            from sichuan_v5_reserve_market import RegulationAward, clear_reserve
            # Separate formal-commitment research proxy after DA preclear.
            # Both synthetic stages see the same disclosed fixture, with no
            # production/interprovincial updates claimed.
            tell('计算独立的日前正式组合研究代理，随后分段出清调频和备用…' if clear_agc
                 else '计算独立的日前正式组合研究代理，随后核验调频声明、预出清备用…')
            formal=intraday(case,0,None,spot_disabled=disabled)
            save('DA_COMMITMENT_RESEARCH.json',formal)
            if clear_agc:
                sec=stages(case,formal,reserve_gross,
                           publish_regulation=lambda rows:save('DA_AGC_RESEARCH.json',rows),
                           publish_reserve=lambda rows:save('DA_RESERVE_RESEARCH.json',rows))
                awards=tuple(Award(t,key,float(a),float(b),float(c))
                             for t,w in sec.items() for key,(a,b,c) in sorted(w.items()))
            else:
                dec=lambda x:Decimal(str(x))
                reg=(RegulationAward('C1',dec(regulation_up),Decimal(0),dec(regulation_up),'declared_C1_regulation'),) if regulation_up else ()
                draft=clear_reserve(dec(reserve_gross),dec(regulation_up),reserve_offers(case),reg,
                                 duration_hours=Decimal(24),regulation_stage_evidence_id='synthetic_user_declared_prior_regulation')
                save('DA_RESERVE_RESEARCH.json',asdict(draft))
                if draft.unmet_mw:
                    tell(f'研究备用缺额 {draft.unmet_mw} MW，不开始后续实时。')
                    raise ValueError('研究备用需求未满足，未运行实时；已保存预出清和备用记录')
                awards=tuple(Award(t,key,float(a),float(b),float(c)) for t in range(PERIODS)
                             for key,(a,b,c) in sorted(draft.energy_holds.items()))
            holds,disabled=prepare_energy_boundary(unit_ids=tuple(r.id for r in case.resources),
                    storage_market={'ES1':(storage_product,)*PERIODS},awards=awards)
            reference=formal
        if awards or reserve_gross:
            tell('校核分段调频／备用容量占位…' if clear_agc else '校核外部声明的调频／备用容量占位…')
            constrained=day_ahead(case,fixed_commitment=reference,holds=holds,spot_disabled=disabled)
            if reserve_gross:
                if any(max((*r['shed'].values(),*r['spill'].values(),r['reliability_reserve_short_mw']))>1.e-6
                       for r in constrained):
                    raise ValueError('备用研究中标后的日前 SCED 有可靠性松弛，不采用')
                save('DA_POST_ANCILLARY_SCED_RESEARCH.json',constrained)
                reference=constrained
        tell('正在执行 15 分钟滚动出清和节点定价…')
        def record_intraday(start,carry,planned):
            thermal={resource.id for resource in case.resources if resource.kind=='thermal'}
            activation={'effective_quarter':start,'prior_executed_quarter':None if carry is None else carry['quarter'],
                        'thermal_mw': {} if carry is None else {r:carry['power'][r] for r in sorted(thermal)},
                        'thermal_online': {} if carry is None else carry['online'],
                        'thermal_state_age_quarters': {} if carry is None else carry['online_age'],
                        'storage_end_soc_mwh': {} if carry is None else carry['energy'],
                        'hydro_prior_executed_mwh': {} if carry is None else carry['hydro_used']}
            if carry is not None and case.cascade is not None:
                water=carry['cascade_state']
                activation['hydro_cascade_state']={'period':water.period,'volume_m3':dict(water.volume_m3),
                    'in_transit':[{'upstream':u,'downstream':d,'next_arrivals_m3':list(a)}
                                  for (u,d),a in sorted(water.in_transit_m3.items())]}
            tell(f'日内负荷边界从第{start+1}段起生效，已更新其后物理计划；不生成价格。')
            save('ID_PLAN.json',dict(schema='ieee39.sichuan_v5.intraday.v1',
                                    activation=activation, rows=planned, is_market_settlement=False))
        rt=realtime(case,reference,executed_periods=periods,holds=holds,spot_disabled=disabled,
                    window_inputs=forecast,intraday_update=intraday_update,intraday_result=record_intraday)

        money=[research_values(case,row) for row in rt]
        save('RT_EXECUTED.json',rt)
        save('RT_RESEARCH_VALUES.json',money)
        status='full_day_research' if periods==PERIODS else 'partial_day_research'
        save('RECEIPT.json',dict(schema=('ieee39.sichuan_v5.receipt.v3' if clear_agc else 'ieee39.sichuan_v5.receipt.v2'),rules=SICHUAN_V5.identity(),status=status,
             quarters=periods, equal_offer_priority=equal_offer_priority, formal_settlement=False, price_fallback_applied=False,
             scenario_hint=archive_scenario(inputs),
             **({'agc_stage':'segmented_research_cleared'} if clear_agc else {}),
             reserve_stage='research_cleared' if reserve_gross else 'not_enabled',
             intraday_status=('executed' if 'ID_PLAN.json' in files else
                              'not_reached' if intraday_update else 'not_declared'), data_files=files.copy()))
    except Exception as exc:
        save('FAILURE.json',dict(rules=SICHUAN_V5.identity(),error=str(exc),failed_research=True,automatic_restart=False,
                                 input_sha256=files['INPUT.json'],scenario_hint=archive_scenario(inputs)))
        exc.archive_name=name
        raise
    return name, load(directory,name)


RECENT_ARCHIVE_LIMIT = 200
ARCHIVE_ID_PATTERN = re.compile(r'scv5_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}')


def archive_path(root, name):
    """Resolve only an existing, ordinary archive dir inside the configured root."""
    directory=Path(root).resolve()
    if (directory.name!=SICHUAN_V5.results_folder or not isinstance(name,str)
            or not ARCHIVE_ID_PATTERN.fullmatch(name)):
        raise ValueError('不是四川研究档案')
    path=directory/name
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
        raise ValueError('不跟随结果目录链接')
    if not path.is_dir():
        raise ValueError('没有找到这项四川研究档案')
    if path.resolve(strict=True)!=path:
        raise ValueError('不跟随结果目录链接')
    return path


def archive_names(root):
    path=Path(root).resolve()
    if path.name!=SICHUAN_V5.results_folder or not path.is_dir():
        return []
    # Never create a result or launch a worker on archive browsing.
    return sorted((f.name for f in path.iterdir() if ARCHIVE_ID_PATTERN.fullmatch(f.name)
                   and not f.is_symlink() and not getattr(f,'is_junction',lambda:False)()
                   and f.is_dir()),reverse=True)[:RECENT_ARCHIVE_LIMIT]


def load(root,name, *, _capture_verified_bytes=None):
    path=archive_path(root,name)
    def read(key,expected=None):
        p=path/key
        if p.is_symlink() or not p.is_file() or p.stat().st_size>64*1024*1024:
            raise ValueError('缺少可核验的结果：'+key)
        data=p.read_bytes()
        if expected is not None and digest(data)!=expected:
            raise ValueError('结果校验不匹配：'+key)
        record=json.loads(data)
        if _capture_verified_bytes is not None and key!='FAILURE.json':
            _capture_verified_bytes(key,data)
        return record
    if not (path/'RECEIPT.json').exists():
        reason=read('FAILURE.json')
        require_profile(reason.get('rules'),SICHUAN_V5)
        raise ValueError('未完成的研究任务：'+reason['error'])
    receipt=read('RECEIPT.json')
    require_profile(receipt.get('rules'),SICHUAN_V5)
    required={'INPUT.json','DA_PRECLEAR.json','RT_EXECUTED.json','RT_RESEARCH_VALUES.json'}
    files=receipt['data_files']
    schema=receipt.get('schema')
    segmented=schema=='ieee39.sichuan_v5.receipt.v3'
    modern=schema in ('ieee39.sichuan_v5.receipt.v2','ieee39.sichuan_v5.receipt.v3')
    if segmented:
        if receipt.get('agc_stage')!='segmented_research_cleared' or receipt.get('reserve_stage')!='research_cleared':
            raise ValueError('分段调频与备用研究状态无效')
        required.add('DA_AGC_RESEARCH.json')
    if modern and receipt.get('reserve_stage')=='research_cleared':
        required.update(('DA_COMMITMENT_RESEARCH.json','DA_RESERVE_RESEARCH.json',
                         'DA_POST_ANCILLARY_SCED_RESEARCH.json'))
    if modern and receipt.get('intraday_status')=='executed':
        required.add('ID_PLAN.json')
    if (set(files)!=required or not all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{64}',v) for v in files.values())
            or not (modern or schema=='ieee39.sichuan_v5.receipt.v1')
            or receipt.get('formal_settlement') is not False):
        raise ValueError('四川研究凭证格式有误')
    data={key:read(key,files[key]) for key in sorted(required)}
    require_profile(data['INPUT.json'].get('rules'),SICHUAN_V5)
    if 'scenario_hint' in receipt and receipt['scenario_hint']!=archive_scenario(data['INPUT.json']):
        raise ValueError('任务摘要与已校验输入不一致')
    priority=data['INPUT.json'].get('equal_offer_priority',False)
    if type(priority) is not bool or receipt.get('equal_offer_priority',False) is not priority:
        raise ValueError('日前同价次序研究选项与运行档案不匹配')
    n=receipt['quarters']
    if (type(n) is not int or not 1<=n<=PERIODS
            or receipt.get('status')!=('full_day_research' if n==PERIODS else 'partial_day_research')
            or data['INPUT.json']['requested_periods']!=n
            or any(len(data[key])!=count or any(row.get('quarter')!=i for i,row in enumerate(data[key]))
               for key,count in [('DA_PRECLEAR.json',PERIODS),('RT_EXECUTED.json',n),('RT_RESEARCH_VALUES.json',n)])):
        raise ValueError('四川研究结果时段记录缺项或顺序有误')
    reserve_result=data.get('DA_RESERVE_RESEARCH.json')
    agc_result=data.get('DA_AGC_RESEARCH.json')
    if segmented!=bool(data['INPUT.json'].get('clears_segmented_agc',False)):
        raise ValueError('调频研究档案与选项来源不匹配')
    if reserve_result is not None:
        commitments=data['DA_COMMITMENT_RESEARCH.json']
        constrained=data['DA_POST_ANCILLARY_SCED_RESEARCH.json']
        if (not data['INPUT.json'].get('research_reserve_gross_mw')
                or (not segmented and Decimal(reserve_result['unmet_mw'])!=0)
                or any(len(rows)!=PERIODS or any(row.get('quarter')!=i for i,row in enumerate(rows))
                       for rows in (commitments,constrained))
                or any('raw_nodal_prices' in row for row in commitments)
                or any(before['online']!=after['online'] for before,after in zip(commitments,constrained))):
            raise ValueError('研究备用凭证与组合或容量边界不符')
        if segmented:
            _check_segmented(agc_result,reserve_result,constrained,data['RT_EXECUTED.json'],
                             data['INPUT.json']['research_reserve_gross_mw'])
    elif modern and data['INPUT.json'].get('research_reserve_gross_mw'):
        raise ValueError('缺少研究备用出清凭证')
    id_result=data.get('ID_PLAN.json')
    if modern:
        configured=data['INPUT.json']['intraday_update']
        update=Update(**configured) if configured is not None else None
        activation=update.effective_quarter if update else None
        expected='not_declared' if update is None else 'executed' if activation<n else 'not_reached'
        if receipt.get('intraday_status')!=expected:
            raise ValueError('日内优化状态未绑定运行时段')
        if id_result is not None:
            rows=id_result['rows']
            if activation:
                original=data['RT_EXECUTED.json'][activation-1]
                handoff=id_result['activation']
                if (handoff.get('thermal_online')!=original['online']
                        or handoff.get('thermal_mw')!={r:original['power'][r] for r in original['online']}
                        or handoff.get('storage_end_soc_mwh')!=original['energy']):
                    raise ValueError('日内参考状态未绑定此前已执行结果')
                if original.get('hydro'):
                    water=handoff.get('hydro_cascade_state')
                    if (not isinstance(water,dict) or water.get('period')!=activation
                            or set(water.get('volume_m3',{}))!=set(original['hydro'])
                            or any(abs(water['volume_m3'][r]-z['storage_m3'])>1.e-5
                                   for r,z in original['hydro'].items())):
                        raise ValueError('日内水力状态未绑定此前已执行库容')
                    links=data['INPUT.json']['case']['cascade']['reaches']
                    by_link={(v['upstream'],v['downstream']):v['next_arrivals_m3']
                             for v in water['in_transit']}
                    if len(by_link)!=len(links) or len(by_link)!=len(water['in_transit']):
                        raise ValueError('日内河段在途水量记录有误')
                    for link in links:
                        u,d=link['upstream'],link['downstream']
                        lag=link['travel_quarters']
                        pending=by_link[(u,d)]
                        expected=[]
                        for at in range(activation,activation+lag):
                            origin=at-lag
                            if origin<0:
                                expected.append(link['initial_arrivals_m3'][at])
                            else:
                                sample=data['RT_EXECUTED.json'][origin]['hydro'][u]
                                expected.append(900*(sample['turbine_m3s']+sample['water_spill_m3s']))
                        if len(pending)!=lag or any(abs(x-y)>1.e-5 for x,y in zip(pending,expected)):
                            raise ValueError('日内河段在途水量未绑定先前已执行流量')
            if (id_result.get('schema')!='ieee39.sichuan_v5.intraday.v1'
                    or id_result.get('is_market_settlement') is not False
                    or id_result['activation'].get('effective_quarter')!=activation
                    or id_result['activation'].get('prior_executed_quarter')!=(None if activation==0 else activation-1)
                    or len(rows)!=PERIODS-activation
                    or any(row.get('quarter')!=t or 'raw_nodal_prices' in row or 'net_amount' in row
                           for t,row in enumerate(rows,activation))):
                raise ValueError('日内物理计划时段或非结算口径无效')
    run_data={'receipt':receipt, 'inputs':data['INPUT.json'], 'da':data['DA_PRECLEAR.json'],
              'intraday':id_result,'reserve':reserve_result,'agc':agc_result,
              'da_post_ancillary':data.get('DA_POST_ANCILLARY_SCED_RESEARCH.json'),
              'rt':data['RT_EXECUTED.json'],'values':data['RT_RESEARCH_VALUES.json']}
    reviews=[]
    reviewed_values=list(run_data['values'])
    for q in range(n):
        filename=_review_filename(q)
        if (path/filename).exists() or (path/filename).is_symlink():
            sidecar=read(filename)
            if sidecar.get('quarter')!=q:
                raise ValueError('价格复核档案时段不匹配')
            checked=_resolve_review(name,run_data,parse_review_request(sidecar.get('request')))
            if sidecar!=json.loads(json_bytes(checked)):
                raise ValueError('价格复核档案与原运行、证据或试算结果不一致')
            reviews.append(sidecar)
            reviewed_values[q]=sidecar['values']
    return dict(run_data,price_reviews=reviews,reviewed_values=reviewed_values)


def evidence_zip(root,name):
    """Package only the original bytes validated by one successful load."""
    payload={}
    total=0
    def capture(key,raw):
        nonlocal total
        total+=len(raw)
        if total>32*1024*1024:
            raise ValueError('原始资料超过 32 MiB 网页打包上限，请备份本地单个运行目录')
        payload[key]=raw
    load(root,name,_capture_verified_bytes=capture)
    output=BytesIO()
    with ZipFile(output,'w',compression=ZIP_DEFLATED) as zip_file:
        for filename,raw in sorted(payload.items()):
            zip_file.writestr(f'{name}/{filename}',raw)
    return output.getvalue()


def _review_filename(quarter):
    return f'RT_PRICE_REVIEW_Q{quarter:02d}.json'


def _resolve_review(name,data,request):
    return resolve_run_review(name,data['receipt']['data_files']['RT_EXECUTED.json'],
                              data['inputs'],data['rt'],data['da'],request)


def review(root,name,request):
    """Create a separate immutable research review; never rewrite the run files."""
    data=load(root,name)
    result=_resolve_review(name,data,request)
    target=Path(root).resolve()/name
    output=target/_review_filename(result['quarter'])
    staged=target/f'.pending_rt_price_review_{uuid.uuid4().hex}'
    payload=json_bytes(result)
    try:
        with staged.open('xb') as stream:
            stream.write(payload)
        try:
            os.link(staged,output)
        except FileExistsError as exc:
            raise ValueError('该执行时段已有不可覆盖的价格复核档案') from exc
    finally:
        staged.unlink(missing_ok=True)
    return load(root,name)


def _check_segmented(agc_rows,reserve_rows,da_rows,rt_rows,reserve_gross):
    def fail():
        raise ValueError('分段调频凭证与备用抵扣或能量占位不一致')
    if not isinstance(agc_rows,list) or not isinstance(reserve_rows,list) or len(agc_rows)!=len(AGC_SEGMENTS) or len(reserve_rows)!=len(AGC_SEGMENTS):
        fail()
    for (start,stop,demand),g,r in zip(AGC_SEGMENTS,agc_rows,reserve_rows):
        if any(q.get('start')!=start or q.get('stop')!=stop for q in (g,r)):
            fail()
        agc,res=(q['result'] for q in (g,r))
        credit=Decimal(agc['system_regulation_credit_mw'])
        awards=agc['awarded']
        compiled=res['energy_holds']
        if (Decimal(agc['demand_mw'])!=demand or not 0<=Decimal(agc['unmet_mw'])<=ROUNDING_TOL
                or not 0<=Decimal(res['unmet_mw'])<=ROUNDING_TOL
                or abs(sum((Decimal(a['reserve_credit_mw']) for a in awards.values()),Decimal(0))-credit)>ROUNDING_TOL
                or abs(Decimal(res['net_demand_mw'])-max(Decimal(0),Decimal(str(reserve_gross))-credit))>ROUNDING_TOL
                or Decimal(res['duration_hours'])!=Decimal(stop-start)/4
                or res['regulation_stage_evidence_id']!=agc['stage_evidence_id']
                or any(max(abs(Decimal(a[k])-Decimal(a['reserve_credit_mw']))
                           for k in ('hold_up_mw','hold_down_mw'))>ROUNDING_TOL
                       or abs(Decimal(a['first_mw'])+Decimal(a['second_mw'])-Decimal(a['reserve_credit_mw']))>ROUNDING_TOL
                       or a['evidence_id']!=agc['stage_evidence_id']+':'+unit
                       for unit,a in awards.items())
                or set(compiled)!=set(awards)|set(res['awards'])):
            fail()
        for unit,(up,down,standby) in compiled.items():
            award=awards.get(unit)
            reserve_award=res['awards'].get(unit)
            if (Decimal(up)!=(Decimal(award['hold_up_mw']) if award else 0)
                    or Decimal(down)!=(Decimal(award['hold_down_mw']) if award else 0)
                    or Decimal(standby)!=(Decimal(reserve_award['regular_mw'])+Decimal(reserve_award['supplemental_mw']) if reserve_award else 0)):
                fail()
        expected={unit:[float(up)+float(standby),float(down)] for unit,(up,down,standby) in compiled.items()
                  if Decimal(up) or Decimal(down) or Decimal(standby)}
        if any(row['ancillary_holds_mw']!=expected for row in (*da_rows[start:stop],*rt_rows[start:stop])):
            fail()
