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
from sichuan_v5_dadu_progress import report
from sichuan_v5_dadu_run import (_bytes, _hash, _market_offers, _policy, _slack, _uncertainty_boundary,
                                declaration_path, declaration_record, default_declaration, source_hashes)
from sichuan_v5_energy import PERIODS, ClearingError, day_ahead
from sichuan_v5_hydro import advance_cascade, initial_cascade_state

RUN = re.compile(r'(wet_low|dry_peak|forecast_error)_\d{8}T\d{12}Z_[0-9a-f]{64}')
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


def review_filing(case, company):
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
            bounds[t][uid] = (low, ceiling)
    return bounds


def audit_day(case, rows, bounds):
    """Replay river travel and reservoir levels independently of the pricing LP."""
    state = initial_cascade_state(case.cascade)
    max_volume = max_arrival = max_power = max_line = max_balance = 0.
    units = {r.id:r for r in case.resources}
    for t, row in enumerate(rows):
        h = row['hydro']
        step = advance_cascade(case.cascade,state,{u:row['power'][u] for u in h},
                               {u:v['water_spill_m3s'] for u,v in h.items()})
        state = step.state
        balance = {b:row['shed'][b] - row['spill'][b] - d for b,d in row['demand'].items()}
        for u, p in row['power'].items():
            balance[units[u].bus] += p
        for line in case.lines:
            flow = row['flows'][line.id]
            balance[line.source] -= flow
            balance[line.target] += flow
            max_line = max(max_line, abs(flow)-line.limit_mw)
        max_balance = max(max_balance, *(abs(v) for v in balance.values()))
        for uid, water in h.items():
            low, high = bounds[t][uid]
            p = row['power'][uid]
            max_power = max(max_power, low-p, p-high)
            max_volume = max(max_volume, abs(state.volume_m3[uid] - water['storage_m3']))
            max_arrival = max(max_arrival, abs(step.upstream_arrival_m3[uid] - water['upstream_arrival_m3']))
    if max(max_volume,max_arrival)>0.05 or max(max_balance,max_line,max_power)>1e-4:
        raise ClearingError('日前结果的节点平衡、线路、能力或梯级水量复算未通过')
    return dict(max_storage_error_m3=max_volume, max_arrival_error_m3=max_arrival,
                max_node_residual_mw=max_balance, max_filing_violation_mw=max_power,
                max_line_overload_mw=max_line, max_slack_mw=max(map(_slack,rows)),
                terminal_transit=[dict(upstream=u, downstream=d, pending_m3=list(queue))
                                  for (u,d),queue in state.in_transit_m3.items()])


def run_day(root, *, scenario, declaration_sha256, awards=(), progress=None):
    report(progress,'inputs',0,1,'载入独立日前预测与已确认申报')
    case, meta = build_case(scenario,39)
    company = default_declaration(case,meta)
    frozen = declaration_record(company)
    path = declaration_path(root,company)
    if (declaration_sha256 != company['declaration_sha256'] or not path.is_file()
            or path.read_bytes() != _bytes(frozen)):
        raise ClearingError('未找到与本场景完全一致的已冻结申报')
    report(progress,'inputs',1,1,'已核对冻结申报与场景')
    bounds = review_filing(case,company)
    approved = apply_company_submission(case,company)
    report(progress,'company',19,19,'19 站、18 个河段与全部时段的申报边界已审核')
    records = [a if isinstance(a,Award) else Award(**a) for a in awards]
    eligible = {r.id for r in case.resources if r.kind in ('thermal','hydro')}
    if any(a.unit not in eligible for a in records):
        raise ClearingError('调频或备用中标占位仅接受登记的水电或煤机')
    holds, disabled = prepare_energy_boundary(unit_ids=[r.id for r in case.resources],
                                              awards=records,storage_market={})
    policy = _policy(case,meta['month'],progress=progress)
    market,raw,effective,_ = _market_offers(approved,policy)
    market,margin = _uncertainty_boundary(market,scenario)
    scuc = []
    report(progress,'da',0,2,'求解 96 点日前机组组合 SCUC')
    rows = day_ahead(market, hydro_bounds=bounds, holds=holds, spot_disabled=disabled,
                     equal_offer_priority=True, on_scuc_dispatch=scuc.extend,
                     on_commitment_solved=lambda:report(progress,'da',1,2,'组合已固定，求解 SCED、节点参考价与同价分配'))
    report(progress,'da',2,2,'日前机组组合和固定组合经济调度完成')
    report(progress,'publish',0,2,'逐段复算节点和梯级水量平衡、日末库存及在途水量')
    audit = audit_day(market,rows,bounds)
    result = dict(schema=SCHEMA, scenario=scenario, company=frozen, bounds=bounds,
                  assumptions={k:meta[k] for k in ('rules_pdf_sha256','hydraulic_limits_note','network_assumption','pv_category','gas_plan_note')},
                  policy=policy, margin=margin, awards=[asdict(a) for a in records],
                  inputs=dict(stations=meta['stations'],assets=meta['assets'],lines=[asdict(l) for l in market.lines],
                              available=market.available,fixed_schedule=market.fixed_schedule,
                              resources=[asdict(r) for r in market.resources]),
                  raw_offers=raw,effective_offers=effective,
                  scuc=[dict(quarter=r['quarter'],online=r['online'],power=r['power']) for r in scuc],
                  da=rows, audit=audit)
    signature = source_hashes()
    signature['app/sichuan_v5_dadu_da.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
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
