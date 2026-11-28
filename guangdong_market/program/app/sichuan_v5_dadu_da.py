"""Day-ahead energy clearing and independently replayable Dadu publication."""
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from sichuan_v5_ancillary import Award, prepare_energy_boundary
from sichuan_v5_company import apply_company_submission
from sichuan_v5_dadu_case import build_case
from sichuan_v5_dadu_operations import DayOperations, with_operations
from sichuan_v5_dadu_progress import report
from sichuan_v5_dadu_run import (_bytes, _hash, _market_offers, _policy, _slack, _uncertainty_boundary,
                                declaration_path, declaration_record, default_declaration, source_hashes)
from sichuan_v5_energy import PERIODS, ClearingError, day_ahead
from sichuan_v5_hydro import advance_cascade, initial_cascade_state
from sichuan_v5_pumped_storage import PSH, audit_pumped, build_day_case

SEASONS = {'wet':'丰水期', 'dry':'枯水期'}
ERROR_LEVELS = {'normal':'正常预测误差', 'extreme':'极端预测误差'}
SEASON_TEMPLATE = {'wet':'wet_low', 'dry':'dry_peak'}
NET_ERROR_SIGMA = {'normal':.01, 'extreme':.03}
DAY_SCENARIOS = {f'{season}_{level}':(season,level) for season in SEASONS for level in ERROR_LEVELS}
LEGACY_SCENARIOS = ('wet_low','dry_peak','forecast_error')
RUN = re.compile('('+'|'.join((*DAY_SCENARIOS,*LEGACY_SCENARIOS))+r')_\d{8}T\d{12}Z_[0-9a-f]{64}')
SCHEMA = 'sichuan.dadu19.day_ahead.v1'


def list_days(root, scenario=None):
    folder = Path(root)/'dadu19_da'
    names = (p.name[:-8] for p in folder.glob('*.json.gz'))
    return sorted((name for name in names if RUN.fullmatch(name) and
                   (scenario is None or name.startswith(scenario+'_'))),
                  key=lambda name: name.rsplit('_', 2)[1], reverse=True)


def read_day(root, name):
    if not isinstance(name, str) or not RUN.fullmatch(name):
        raise ValueError('日前运行编号无效')
    packed = (Path(root)/'dadu19_da'/(name+'.json.gz')).read_bytes()
    if hashlib.sha256(packed).hexdigest() != name.rsplit('_', 1)[1]:
        raise ValueError('日前结果校验失败')
    result = json.loads(gzip.decompress(packed))
    if result.get('schema') != SCHEMA or len(result['da']) != PERIODS:
        raise ValueError('日前结果不完整')
    return result


def review_filing(case, company, operations=None):
    hydraulic = asdict(case.cascade)
    for key in ('reservoirs', 'reaches'):
        if _hash(hydraulic[key]) != _hash(company['hydraulic_declaration'][key]):
            raise ClearingError('申报水库或河段与市场登记参数不一致')
    ids = [r.id for r in case.cascade.reservoirs]
    offers = company['offers']
    bounds = [{} for _ in range(PERIODS)]
    for uid in ids:
        lower, upper = offers[uid]['minimum_mw'], offers[uid]['available_mw']
        if len(lower) != PERIODS or len(upper) != PERIODS:
            raise ClearingError(f'{uid} 能力申报缺少时段')
        for t, (low, high) in enumerate(zip(lower, upper)):
            if not all(isinstance(x,(int,float)) and math.isfinite(x) and x >= 0 for x in (low,high)):
                raise ClearingError(f'{uid} 第 {t+1} 段申报值无效')
            ceiling = min(high, case.available[t][uid])
            if low > ceiling:
                raise ClearingError(f'{uid} 第 {t+1} 段申报与技术可用能力无交集')
            if operations and operations.island(uid,t) > case.available[t][uid]:
                raise ClearingError(f'{uid} 第 {t+1} 段孤网计划超过市场侧技术可用容量')
            bounds[t][uid] = operations.grid_bounds(uid,t,low,ceiling) if operations else (low,ceiling)
    return bounds


def audit_day(case, rows, bounds, operations=None):
    """Replay river travel and reservoir levels independently of the pricing LP."""
    state = initial_cascade_state(case.cascade)
    flow_error = level_error = island_mwh = 0.
    max_volume = max_arrival = max_power = max_line = max_balance = 0.
    units = {r.id:r for r in case.resources}
    for t, row in enumerate(rows):
        h = row['hydro']
        local = {u:operations.island(u,t) if operations else 0. for u in h}
        island_mwh += .25*sum(local.values())
        step = advance_cascade(case.cascade,state,{u:row['power'][u]+local[u] for u in h},
                               {u:v['water_spill_m3s'] for u,v in h.items()})
        state = step.state
        balance = {b:row['shed'][b] - row['spill'][b] - d for b,d in row['demand'].items()}
        for u, p in row['power'].items():
            balance[units[u].bus] += p
        for u, pumping in row['charge'].items():
            balance[units[u].bus] -= pumping
        for line in case.lines:
            flow = row['flows'][line.id]
            balance[line.source] -= flow
            balance[line.target] += flow
            max_line = max(max_line, abs(flow)-line.limit_mw)
        max_balance = max(max_balance, *(abs(v) for v in balance.values()))
        for uid, water in h.items():
            s = operations.slots.get((uid,t),{}) if operations else {}
            flow_error = max(flow_error,s.get('release',0)-water['release_m3s'],
                             abs(step.turbine_m3s[uid]-water['turbine_m3s']))
            level_error = max(level_error,s.get('level_min_m3',0)-state.volume_m3[uid],
                              state.volume_m3[uid]-s.get('level_max_m3',math.inf))
            low, high = bounds[t][uid]
            p = row['power'][uid]
            max_power = max(max_power, low-p, p-high)
            max_volume = max(max_volume, abs(state.volume_m3[uid] - water['storage_m3']))
            max_arrival = max(max_arrival, abs(step.upstream_arrival_m3[uid] - water['upstream_arrival_m3']))
    if max(max_volume,max_arrival,level_error)>0.05 or max(max_balance,max_line,max_power,flow_error)>1e-4:
        raise ClearingError('日前结果的节点平衡、线路、能力或梯级水量复算未通过')
    return dict(max_flow_error_m3s=flow_error,max_level_bound_error_m3=level_error,island_energy_mwh=island_mwh,
                max_storage_error_m3=max_volume, max_arrival_error_m3=max_arrival,
                max_node_residual_mw=max_balance, max_filing_violation_mw=max_power,
                max_line_overload_mw=max_line, max_slack_mw=max(map(_slack,rows)),
                terminal_transit=[dict(upstream=u, downstream=d, pending_m3=list(queue))
                                  for (u,d),queue in state.in_transit_m3.items()])


def run_day(root, *, scenario, declaration_sha256, awards=(), progress=None):
    report(progress,'inputs',0,1,'载入独立日前预测与已确认申报')
    if scenario not in DAY_SCENARIOS and scenario not in LEGACY_SCENARIOS:
        raise ClearingError('无法识别的日前场景')
    season, level = DAY_SCENARIOS.get(scenario, (None,None))
    template = SEASON_TEMPLATE[season] if season else scenario
    case, meta = build_day_case(template,39)
    company = default_declaration(case,meta)
    if not isinstance(declaration_sha256,str) or not re.fullmatch('[a-f0-9]{64}',declaration_sha256):
        raise ClearingError('未找到与本场景完全一致的已冻结申报')
    path = declaration_path(root,dict(declaration_sha256=declaration_sha256))
    if not path.is_file():
        raise ClearingError('未找到与本场景完全一致的已冻结申报')
    contents = path.read_bytes()
    if declaration_sha256 != company['declaration_sha256']:
        saved = json.loads(contents)
        company = with_operations(company,saved.get('operations',{}).get('entries',[]))
    frozen = declaration_record(company)
    if company['declaration_sha256'] != declaration_sha256 or contents != _bytes(frozen):
        raise ClearingError('冻结申报内容或场景校验失败')
    report(progress,'inputs',1,1,'已核对冻结申报与场景')
    operations = DayOperations(company,meta['stations'])
    active = operations if operations.slots else None
    bounds = review_filing(case,company,active)
    approved = apply_company_submission(case,company)
    report(progress,'company',19,19,'19 站、18 个河段与全部时段的申报边界已审核')
    records = [a if isinstance(a,Award) else Award(**a) for a in awards]
    eligible = {r.id for r in case.resources if r.kind in ('thermal','hydro')}
    if any(a.unit not in eligible for a in records):
        raise ClearingError('调频或备用中标占位仅接受登记的水电或煤机')
    holds, disabled = prepare_energy_boundary(unit_ids=[r.id for r in case.resources],
                                              awards=records,storage_market={})
    policy = _policy(case,meta['month'],progress=progress,operations=active)
    market,raw,effective,_ = _market_offers(approved,policy)
    market,margin = _uncertainty_boundary(market,scenario,error_sigma=NET_ERROR_SIGMA.get(level))
    scuc = []
    report(progress,'da',0,2,'求解 96 点日前机组组合 SCUC')
    rows = day_ahead(market, operations=active, hydro_bounds=bounds, holds=holds, spot_disabled=disabled,
                     equal_offer_priority=True, on_scuc_dispatch=scuc.extend,
                     on_commitment_solved=lambda:report(progress,'da',1,2,'组合已固定，求解 SCED、节点参考价与同价分配'))
    report(progress,'da',2,2,'日前机组组合和固定组合经济调度完成')
    report(progress,'publish',0,2,'逐段复算节点和梯级水量平衡、日末库存及在途水量')
    audit = audit_day(market,rows,bounds,active)
    audit['pumped_storage'] = audit_pumped(market,rows)
    audit_pumped(market,scuc)
    result = dict(schema=SCHEMA, scenario=scenario, company=frozen, bounds=bounds,
                  assumptions={k:meta[k] for k in ('rules_pdf_sha256','hydraulic_limits_note','network_assumption','pv_category','gas_plan_note')},
                  policy=policy, margin=margin, awards=[asdict(a) for a in records],
                  inputs=dict(stations=meta['stations'],assets=meta['assets'],lines=[asdict(l) for l in market.lines],
                              available=market.available,fixed_schedule=market.fixed_schedule,pumped_storage=meta['pumped_storage'],
                              resources=[asdict(r) for r in market.resources]),
                  raw_offers=raw,effective_offers=effective,
                  scuc=[dict(quarter=r['quarter'],online=r['online'],power=r['power'],charge=r['charge'],energy=r['energy']) for r in scuc],
                  da=rows, audit=audit)
    if season:
        result['scenario_settings'] = dict(season=season, forecast_error=level,
                                           month=meta['month'], seasonal_template=template)
    signature = source_hashes()
    signature['app/sichuan_v5_dadu_da.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    ops_file = Path(__file__).with_name('sichuan_v5_dadu_operations.py')
    signature['app/'+ops_file.name] = hashlib.sha256(ops_file.read_bytes()).hexdigest()
    pump_file = Path(__file__).with_name('sichuan_v5_pumped_storage.py')
    signature['app/'+pump_file.name] = hashlib.sha256(pump_file.read_bytes()).hexdigest()
    result['source_hashes'] = signature
    packed = gzip.compress(_bytes(result),mtime=0)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    name = scenario+'_'+stamp+'_'+hashlib.sha256(packed).hexdigest()
    folder = Path(root)/'dadu19_da'
    folder.mkdir(parents=True,exist_ok=True)
    temp = folder/(uuid4().hex+'.tmp')
    try:
        temp.write_bytes(packed)
        temp.replace(folder/(name+'.json.gz'))
    finally:
        temp.unlink(missing_ok=True)
    report(progress,'publish',2,2,'日前预出清结果已保存')
    return name, result
