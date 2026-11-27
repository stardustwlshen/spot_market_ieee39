"""One-day Dadu market run with independent company and operator inputs."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from sichuan_v5_ancillary import Award, prepare_energy_boundary
from sichuan_v5_company import apply_company_submission, prepare_company_submission
from sichuan_v5_dadu_case import build_case, company_snapshot, forecast_callback
from sichuan_v5_dadu_policy import effective_offer, market_power_policy, operator_hydro_envelope, settle_realtime
from sichuan_v5_energy import DT, PERIODS, ClearingError, day_ahead, realtime, validate


SCHEMA = "sichuan.dadu19.run.v1"
RUN_ID = re.compile(r"dadu19_\d{8}T\d{6}Z_[0-9a-f]{12}")
BASE_COMMIT = "633e42a490ac58368b659017e0e27c72a022b912"
DATA_COMMIT = "012e603f979541efe4ed72c169cd29976fe87b6d"


def _bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _assumptions(case, corridor_scale, gas_scale):
    lines = tuple(replace(line, limit_mw=line.limit_mw*corridor_scale)
                  if line.id == "T25-37" else line for line in case.lines)
    schedule = tuple({key: value*gas_scale for key, value in row.items()}
                     for row in case.fixed_schedule)
    return replace(case, lines=lines, fixed_schedule=schedule)


def _policy(case, month):
    water_mw, water_mwh = operator_hydro_envelope(case)
    other_market = [u for u in case.resources if u.kind in ("thermal", "renewable")]
    other_mw = [sum(row.get(u.id, u.capacity_mw) for u in other_market) for row in case.available]
    supply = [hydro + other for hydro, other in zip(water_mw, other_mw)]
    net = [sum(load.values()) - sum(plan.values()) for load,plan in zip(case.demand,case.fixed_schedule)]
    other_mwh = DT * sum(other_mw)
    policy = market_power_policy(month, supply, net, [case.reserve_mw]*PERIODS,
                                 water_mwh + other_mwh)
    return dict(policy, operator_hydro_capacity_mw=water_mw, operator_hydro_supply_mwh=water_mwh,
                operator_market_capacity_mw=supply, operator_market_supply_mwh=water_mwh + other_mwh,
                capability_basis="每个时段分别求全梯级同时可达出力，全日可供电量独立优化。煤机和光伏按当期可用能力计入，气电计划抵减净需求。")


def _issued_window(current, t, stop, holds, hydraulic_static_sha):
    window = dict(quarter=t, stop=stop, demand=current.demand[t:stop],
                  available=current.available[t:stop], gas_plan=current.fixed_schedule[t:stop],
                  local_inflow_m3s=current.cascade.natural_inflow_m3s[t:stop])
    end = PERIODS if any(r.terminal_min_m3 is not None or r.terminal_max_m3 is not None
                         for r in current.cascade.reservoirs) else stop
    hydro = [u for u in current.resources if u.kind == "hydro"]
    tail = dict(start=stop, stop=end, static_sha256=hydraulic_static_sha,
                local_inflow_m3s=current.cascade.natural_inflow_m3s[stop:end],
                available_mw=[{u.id: row.get(u.id, u.capacity_mw) for u in hydro}
                              for row in current.available[stop:end]],
                holds=[{u.id: row.get(u.id, (0., 0.)) for u in hydro} for row in holds[stop:end]])
    return dict(quarter=t, stop=stop, forecast_sha256=_hash(window),
                hydraulic_tail=dict(start=stop, stop=end, quarters=end-stop,
                                    source=("日前已发布区间来水和能力、既定辅助服务占位，仅供日末水量可达性校核。"
                                            if end > stop else "电能量窗口已覆盖水力时域。"),
                                    sha256=_hash(tail) if end > stop else None))


def _market_offers(case, policy):
    reservoirs = {r.id:r for r in case.cascade.reservoirs}
    units, raw, effective, kinds = [], {}, {}, {}
    for unit in case.resources:
        if unit.kind == "hydro":
            kind = "run_of_river" if reservoirs[unit.id].regulation in ("ror", "run_of_river") else "reservoir"
        else:
            kind = {"thermal":"coal", "renewable":"pv", "fixed":"gas"}[unit.kind]
        kinds[unit.id] = kind
        if kind != "gas":
            start = 0.
            spans = []
            for width, price in unit.segments:
                end = round(start + width, 2)
                spans.append((start, end, price))
                start = end
            if not spans:
                spans = [(0., unit.capacity_mw, unit.offer_yuan_mwh)]
            # Coal's minimum-output base is a solver cost, outside its submitted curve.
            base_block = []
            if kind == "coal" and unit.minimum_mw > 0:
                if abs(spans[0][1]-unit.minimum_mw) > 1e-6:
                    raise ClearingError(f"{unit.id} 最小出力费用未与报价段分开")
                base_block = [spans.pop(0)]
            raw[unit.id] = [(round(b-a,2),v) for a,b,v in spans]
            new = effective_offer(spans, kind, unit.capacity_mw, policy)
            blocks = [(round(b-a, 2), float(v)) for a,b,v in new]
            effective[unit.id] = blocks
            # The native core represents minimum output by an always-consumed
            # first block. If mitigation cuts it below its original cost,
            # retain the cost difference in the online hourly cost.
            if base_block:
                a,b,base_price = base_block[0]
                installed_base = min(float(base_price), blocks[0][1])
                unit = replace(unit, online_cost_yuan_hour=unit.online_cost_yuan_hour
                               + (float(base_price)-installed_base)*unit.minimum_mw)
                blocks = [(round(b-a,2),installed_base),*blocks]
            unit = replace(unit, segments=tuple(blocks), offer_yuan_mwh=blocks[0][1])
        units.append(unit)
    return replace(case, resources=tuple(units)), raw, effective, kinds


def _slack(row):
    return max(_system_shortfalls(row).values())


def _system_shortfalls(row):
    """Sum bus shortfalls within each constraint family before reporting MW."""
    return {"unserved_load_mw": sum(max(0., v) for v in row["shed"].values()),
            "excess_injection_mw": sum(max(0., v) for v in row["spill"].values()),
            "reserve_short_mw": max(0., row["reliability_reserve_short_mw"])}


def _output_folder(root):
    return Path(root).resolve() / "dadu19"


def list_dadu(root):
    folder = _output_folder(root)
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if RUN_ID.fullmatch(p.name)
                   and (p/"receipt.json").is_file()), reverse=True)


def read_dadu(root, name):
    if not isinstance(name, str) or not RUN_ID.fullmatch(name):
        raise ValueError("大渡河运行编号无效")
    folder = _output_folder(root)/name
    receipt = json.loads((folder/"receipt.json").read_bytes())
    data = (folder/"result.json.gz").read_bytes()
    if (receipt.get("schema") != SCHEMA or receipt.get("run") != name or
            hashlib.sha256(data).hexdigest() != receipt.get("sha256")):
        raise ValueError("大渡河运行记录校验失败")
    result = json.loads(gzip.decompress(data))
    if (result.get("schema") != SCHEMA or result.get("run") != name
            or len(result["rt"]) != result["summary"]["n_executed"]
            or _hash(result["company"]["offers"]) != result["summary"]["offer_hash"]):
        raise ValueError("大渡河运行档案内容不完整")
    public = {k:result["company"][k] for k in ("schema","strategy","as_of","offers","submission_sha256")}
    if (json.loads((folder/"frozen_bids.json").read_bytes()) != public
            or public["submission_sha256"] != receipt.get("bid_hash")):
        raise ValueError("单独留存的公司冻结申报与执行档案不一致")
    for row in result["da"] + result["rt"]:
        for field in ("demand", "shed", "spill", "raw_nodal_prices"):
            row[field] = {int(b):v for b,v in row[field].items()}
    for row in result["money"]["quarters"]:
        for field in ("raw_price_by_bus", "effective_price_by_bus", "price_source_by_bus"):
            if field in row:
                row[field] = {int(b):v for b,v in row[field].items()}
    return result


def source_hashes():
    app = Path(__file__).resolve().parent
    files = [app/"sichuan_v5_dadu_run.py", app/"sichuan_v5_dadu_case.py",
             app/"sichuan_v5_dadu_policy.py", app/"sichuan_v5_energy.py",
             app/"sichuan_v5_hydro.py", app/"sichuan_v5_company.py", app/"sichuan_v5_rules.py",
             app/"sichuan_v5_segments.py", app/"sichuan_v5_settlement.py",
             app/"sichuan_v5_ancillary.py"]
    data = app.parent/"data"
    files.extend(f for directory in (data/"dadu19", data/"case39") for f in directory.glob("*") if f.is_file())
    return {f.relative_to(app.parent).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(set(files))}


def _stock_value_change(cascade, handoff, water_values):
    """Common ex-ante value of the change in stock and transit at execution end."""
    stations = sum(water_values[r.id]*(handoff["storage_m3"][r.id]-r.initial_storage_m3)
                   for r in cascade.reservoirs)
    initial = {(e.upstream,e.downstream):sum(e.initial_arrivals_m3) for e in cascade.reaches}
    transit = sum(water_values[e["downstream"]]*
                  (sum(e["next_arrivals_m3"])-initial[e["upstream"],e["downstream"]])
                  for e in handoff["in_transit"])
    return stations+transit


def _uncertainty_boundary(market, scenario):
    """D-1 synthetic design prior; no RT observations or seed enter this decision."""
    from statistics import NormalDist
    peak = max(sum(row.values()) for row in market.demand)
    sigma = .03 if scenario == "forecast_error" else 0.
    quantile = .95
    extra = 10 * math.ceil(peak * sigma * NormalDist().inv_cdf(quantile) / 10.)
    report = dict(issued_at="D-1 08:30 Asia/Shanghai", forecast_peak_mw=peak,
                  assumed_net_error_sigma_fraction=sigma, one_sided_quantile=quantile,
                  da_extra_up_mw=extra, da_requirement_mw=market.reserve_mw+extra,
                  rt_requirement_mw=market.reserve_mw,
                  basis="事前合成净负荷误差尺度占预测峰值3%，单侧正态95%设计分位并向上取整至10 MW。"
                        "仅预测偏差场景使用；未用当日误差校准，亦非四川备用规则或实测覆盖率。"
                        "在线煤机按15分钟爬坡和剩余容量留足日前裕度；按各节点日前负荷占比分摊的"
                        "同步激活潮流须满足线路限额，实时允许调用。")
    return replace(market, reserve_mw=market.reserve_mw+extra, deliverable_up_mw=extra), report


def run_dadu(root, *, scenario="wet_low", strategy="water_value", periods=96,
             seed=39, progress=None, water_value_scale=1., corridor_scale=1.,
             gas_plan_scale=1., awards=()):
    if type(periods) is not int or not 1 <= periods <= PERIODS:
        raise ValueError("实时执行时段范围须为1至96")
    for value,lo,hi,label in ((water_value_scale,.5,2.,"水价值倍数"),
                              (corridor_scale,.6,1.2,"通道限额倍数"),(gas_plan_scale,0.,1.,"气电计划倍数")):
        if type(value) not in (float,int) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{label}须在 {lo} 至 {hi} 范围内")
    tell = progress if progress is not None else lambda _:None
    scenario = {"wet":"wet_low","dry":"dry_peak","error":"forecast_error"}.get(scenario,scenario)
    tell("正在载入19站水情与独立的市场边界")
    base, meta = build_case(scenario,seed)
    public = company_snapshot(base,meta)
    water_values = {k:float(v)*water_value_scale for k,v in meta["water_value_yuan_m3"].items()}
    tell("公司正在跨站优化用水并冻结申报")
    company = prepare_company_submission(public, meta["expected_price_yuan_mwh"],water_values,
        strategy=strategy, as_of="D-1 09:45 Asia/Shanghai")
    operator = _assumptions(base,corridor_scale,gas_plan_scale)
    validate(operator)
    approved = apply_company_submission(operator,company)
    tell("市场正在核定水量可供电量、有效报价与风险缓解上限")
    policy = _policy(operator,meta["month"])
    market,raw,effective,kinds = _market_offers(approved,policy)
    da_market, forecast_margin = _uncertainty_boundary(market, scenario)
    known = {r.id:r for r in market.resources}
    records = tuple(a if isinstance(a,Award) else Award(**a) for a in awards)
    if any(a.unit not in known or known[a.unit].kind not in ("thermal","hydro") for a in records):
        raise ValueError("辅助服务占位只能指定已登记的合资格水电或煤机")
    holds,disabled = prepare_energy_boundary(unit_ids=tuple(known),storage_market={},awards=records)
    signature = source_hashes()
    boundary_hash = _hash(dict(operator=asdict(operator),scenario=scenario,seed=seed,periods=periods,
                                company_forecast=asdict(public),expected=meta["expected_price_yuan_mwh"],
                                water_values=water_values,forecast_source=signature["app/sichuan_v5_dadu_case.py"],
                                forecast_margin=forecast_margin, awards=[asdict(a) for a in records]))
    stations = meta["stations"]
    assets = meta["assets"]
    inputs = dict(scenario=scenario,seed=seed,month=meta["month"],strategy=strategy,
                  periods=periods,water_value_scale=water_value_scale,corridor_scale=corridor_scale,
                  gas_plan_scale=gas_plan_scale,stations=stations,assets=assets,available=market.available,
                  fixed_schedule=market.fixed_schedule,demand=market.demand,lines=[asdict(l) for l in market.lines],
                  market_case=asdict(operator),market_base_sha=BASE_COMMIT,hydro_source_sha=DATA_COMMIT,
                  source_hashes=signature,awards=[asdict(a) for a in records],boundary_hash=boundary_hash,
                  assumptions={k:meta[k] for k in ("pv_category","gas_plan_note","hydraulic_limits_note",
                               "water_value_basis","issued_at","network_assumption","hydrology_assumption",
                               "declared_participation","atlas_blobs","rules_pdf_sha256")},
                  study_type="合成 IEEE39，大渡河19站模拟组合；各站当日选择实时耦合并假设获批")
    inputs["assumptions"]["coal_commitment"] = {
        "basis": "开停机最短时间采用草案季节参数。各机组最小稳定出力、爬坡、初始状态与时长、启动成本和厂用电口径是本算例的合成边界。",
        "units": {u.id: dict(minimum_mw=u.minimum_mw, ramp_mw_15min=u.ramp_mw_15min,
                             initial_mw=u.initial_mw, initial_state_age_quarters=u.initial_state_age_quarters,
                             min_up_hours=DT*u.min_up_quarters, min_down_hours=DT*u.min_down_quarters,
                             startup_yuan=u.startup_yuan) for u in operator.resources if u.kind == "thermal"},
    }
    inputs["assumptions"]["forecast_margin"] = forecast_margin
    rootdir = _output_folder(root)
    rootdir.mkdir(parents=True,exist_ok=True)
    name = datetime.now(timezone.utc).strftime("dadu19_%Y%m%dT%H%M%SZ_")+uuid.uuid4().hex[:12]
    folder = rootdir/name
    folder.mkdir()
    frozen = {k:company[k] for k in ("schema","strategy","as_of","offers","submission_sha256")}
    (folder/"frozen_bids.json").write_bytes(_bytes(frozen))
    try:
        tell("正在进行日前96点机组组合与固定组合经济调度")
        da = day_ahead(da_market)
        if records:
            tell("正在按调频和备用占位重算日前参考")
            da = day_ahead(da_market,fixed_commitment=da,holds=holds,spot_disabled=disabled)
        if any(_slack(row)>1e-5 for row in da):
            first = next(r for r in da if _slack(r)>1e-5)
            raise ClearingError(f"日前第 {first['quarter']+1} 段有 {_slack(first):.2f} MW 可靠性松弛")
        base_forecast = forecast_callback(meta)
        hydraulic_static_sha = _hash(dict(
            reservoirs=[asdict(r) for r in market.cascade.reservoirs],
            reaches=[asdict(r) for r in market.cascade.reaches],
            unit_bounds=[(u.id, u.capacity_mw, u.minimum_mw, u.ramp_mw_15min)
                         for u in market.resources if u.kind == "hydro"]))
        inputs["hydraulic_static_sha256"] = hydraulic_static_sha
        issued = []
        def window_inputs(day,t,stop):
            if t % 8 == 0 or t+1 == periods:
                tell(f"实时执行第 {t+1}/{periods} 段")
            current = base_forecast(day,t,stop)
            issued.append(_issued_window(current,t,stop,holds,hydraulic_static_sha))
            return current

        rt = realtime(market, da, executed_periods=periods, holds=holds, spot_disabled=disabled,
                      window_inputs=window_inputs)
        for row in rt:
            row["system_shortfalls"] = _system_shortfalls(row)
        resources = [dict(id=u.id,bus=u.bus,kind=kinds[u.id]) for u in market.resources]
        quarters = [dict(quarter=r["quarter"],price_by_bus=r["raw_nodal_prices"],cleared_mw=r["power"],
                         metered_mw=r["power"],cap=policy["cap"],reliable=_slack(r)<=1e-5) for r in rt]
        money = settle_realtime(quarters,resources)
        spill_m3 = sum(900*h["water_spill_m3s"] for row in rt for h in row["hydro"].values())
        gas_mwh = DT*sum(row["power"][u.id] for row in rt for u in market.resources if u.kind=="fixed")
        solar_cut = DT*sum(sum(row["curtailed_mw"].values()) for row in rt)
        bid_hash = company["submission_sha256"]
        stock_change = _stock_value_change(market.cascade,rt[-1]["water_handoff"],water_values)
        cash = money["executed_portfolio_revenue"]
        summary = dict(boundary_hash=boundary_hash,input_hash=_hash([boundary_hash,bid_hash,signature]),
                       n_executed=len(rt),bid_hash=bid_hash,offer_hash=_hash(company["offers"]),
                       company_revenue=cash,water_spill_m3=spill_m3,water_stock_value_change=stock_change,
                       water_adjusted_value=None if cash is None else cash+stock_change,
                       gas_mwh=gas_mwh,solar_curtail_mwh=solar_cut,risk_active=policy["active"],
                       issued_boundary_hash=_hash(issued),max_slack_mw=max(map(_slack,rt)),
                       unserved_energy_mwh=DT*sum(r["system_shortfalls"]["unserved_load_mw"] for r in rt),
                       online_study="基态有功直流潮流",execution_basis="段初可观测工况、段内保持，执行量作合成计量")
        result = dict(schema=SCHEMA,run=name,inputs=inputs,company=company,policy=policy,
                      raw_offers=raw,effective_offers=effective,issued_forecasts=issued,
                      da=da,rt=rt,money=money,summary=summary)
        packed = gzip.compress(_bytes(result),compresslevel=4,mtime=0)
        (folder/"result.json.gz").write_bytes(packed)
        receipt = dict(schema=SCHEMA,run=name,sha256=hashlib.sha256(packed).hexdigest(),
                       scenario=scenario,strategy=strategy,periods=len(rt),bid_hash=bid_hash,
                       boundary_hash=boundary_hash,seed=seed,water_value_scale=water_value_scale,
                       corridor_scale=corridor_scale,gas_plan_scale=gas_plan_scale)
        temp = folder/"receipt.tmp"
        temp.write_bytes(_bytes(receipt))
        temp.replace(folder/"receipt.json")
        return name, result
    except Exception as exc:
        (folder/"failure.json").write_bytes(_bytes(dict(run=name,scenario=scenario,strategy=strategy,
                            submission_sha256=company["submission_sha256"],reason=str(exc))))
        raise
