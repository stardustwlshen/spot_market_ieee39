"""Declared reservoir daily energy minima: execution accounting and asset witnesses.

The suffix is not future market clearing. It has no energy objective, never
executes or settles, and certifies hydro assets only, not network or reserve.
"""
from __future__ import annotations

import copy
import math
from numbers import Real

import numpy as np
import pyomo.environ as pyo

SCOPE = "hydro_assets_only;not_future_network_or_reserve;not_individual_turbine_commitment"


def limits(cfg):
    return {h["id"]: float(h["e_day_min"]) for h in cfg.units.get("hydro", [])
            if h.get("regulation", "reservoir") == "reservoir" and h.get("e_day_min") is not None}


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label}须为有限非负数")
    return float(value)


def reference_terms(cfg, da_plan, selected, hydro_mode, window_end, metadata):
    if not limits(cfg) or window_end >= cfg.T:
        return None
    from .handoff import _array
    plan = selected if hydro_mode == "value" else da_plan
    delta, power = {}, {}
    if hydro_mode != "sced":
        for h in cfg.units["hydro"]:
            for z in range(len(h["zones"])):
                key = h["id"], z
                delta[key] = _array(plan.get("delta", {}).get(key), cfg.T,
                    f"daily_energy.reference.delta.{key}", integer=True, bounds=(0, h["n_units"]))
            if hydro_mode == "fixed_da":
                power[h["id"]] = _array(da_plan.get("Ph", {}).get(h["id"]), cfg.T,
                    f"daily_energy.reference.Ph.{h['id']}", bounds=(0, h["n_units"] * h["unit_pmax"]))
    return dict(start_period=window_end, end_period_exclusive=cfg.T,
                delta=delta, power=power, hydro_mode=hydro_mode,
                reference_metadata=copy.deepcopy(metadata.get("V", {})))


def attach(m):
    """Add a hard remaining-day minimum, without any relaxation variable."""
    case, md = m._case, m._md
    required = limits(case.cfg)
    m._hydro_daily_energy = {}
    if not required:
        m.c_edaymin = pyo.Constraint([])
        return
    if case.stage not in ("DA", "ID", "RT"):
        raise ValueError("水电日电量下限仅支持DA/ID/RT阶段")
    actual = (case.init or {}).get("hydro_used", {})
    if case.t0 > 0 and (not isinstance(actual, dict) or not set(required) <= set(actual)):
        raise ValueError("水电日电量下限缺少实际已执行累计hydro_used，禁止重置为0")
    for h, minimum in required.items():
        used = _number(actual.get(h, 0.), f"{h}.hydro_used")
        if case.t0 == 0 and used > 1e-6:
            raise ValueError("日初hydro_used必须为0，禁止把计划量当已执行量")
        m._hydro_daily_energy[h] = dict(daily_minimum_mwh=minimum, executed_before_mwh=used,
                                        remaining_minimum_mwh=max(0., minimum-used))
    end = case.t0 + md.T
    if end > case.cfg.T:
        raise ValueError("水电日电量下限窗口不能超出运行日末")
    if case.stage in ("DA", "ID") and end != case.cfg.T:
        raise ValueError("日前/日内日电量下限须覆盖至运行日末")
    b = None
    if (case.stage == "RT" and end < case.cfg.T
            and any(r["remaining_minimum_mwh"] > 1e-6 for r in m._hydro_daily_energy.values())):
        b = _attach_suffix(m)
    m.c_edaymin = pyo.Constraint(list(required), rule=lambda m, h:
        (sum(m.Ph[j, t] for j in md.hydro[h]["units"] for t in m.T)
         + (sum(b.Ph[h, t] for t in b.F) if b is not None else 0.)) * md.dt
        >= m._hydro_daily_energy[h]["remaining_minimum_mwh"] - 1e-6)


def _attach_suffix(m):
    case, md = m._case, m._md
    T, end = md.T, case.cfg.T-case.t0
    spec = (case.terminal or {}).get("hydro_daily_energy_reference")
    if (not isinstance(spec, dict) or spec.get("start_period") != case.t0+T
            or spec.get("end_period_exclusive") != case.cfg.T):
        raise ValueError("实时日电量下限须提供完整剩余日水电资产续程参考")
    mode = spec.get("hydro_mode")
    if mode not in ("value", "fixed_da", "window_end_da", "sced"):
        raise ValueError("水电日电量续程模式非法")
    from .handoff import _array
    delta, power = {}, {}
    if mode != "sced":
        for h, d in md.hydro_units.items():
            for z in range(len(d["zones"])):
                delta[h, z] = _array(spec.get("delta", {}).get((h, z)), case.cfg.T,
                    f"daily_energy.delta.{h}.{z}", integer=True, bounds=(0, d["n_units"]))
            if mode == "fixed_da":
                power[h] = _array(spec.get("power", {}).get(h), case.cfg.T,
                    f"daily_energy.power.{h}", bounds=(0, d["pmax"]))
    b = m.hydro_daily_energy_suffix = pyo.Block(concrete=True)
    b._integer_names = ("delta",)
    b._reference_metadata = copy.deepcopy(spec.get("reference_metadata", {}))
    b._hydro_mode = mode
    b.F = pyo.Set(initialize=range(T, end), ordered=True)
    b.Ph = pyo.Var(m.HS, b.F, domain=pyo.NonNegativeReals)
    b.V = pyo.Var(m.HS, b.F, domain=pyo.NonNegativeReals)
    b.Qspill = pyo.Var(m.HS, b.F, domain=pyo.NonNegativeReals)
    b.delta = pyo.Var(m.JZ, b.F, domain=pyo.NonNegativeIntegers,
                     bounds=lambda b, h, z, t: (0, md.hydro_units[h]["n_units"]))
    b.pz = pyo.Var(m.JZ, b.F, domain=pyo.NonNegativeReals)
    b.constraints = pyo.ConstraintList()
    add = b.constraints.add
    def variable(name, h, t):
        return getattr(b if t >= T else m, name)[h, t]
    def online(h, t):
        owner = b if t >= T else m
        return sum(owner.delta[h, z, t] for z in range(len(md.hydro_units[h]["zones"])))
    def release(h, t):
        if t >= 0:
            return md.hydro[h]["water_rate"] * variable("Ph", h, t) + variable("Qspill", h, t)
        absolute = case.t0+t
        if absolute >= 0:
            history = (case.init or {}).get("hydro_hist", {}).get(h)
            if history is None or absolute >= len(history):
                raise ValueError(f"水电日电量续程缺少已执行出库历史{h}[{absolute}]")
            return _number(history[absolute], f"{h}.hydro_hist[{absolute}]")
        original = next(d for d in case.cfg.units["hydro"] if d["id"] == h)
        return original["water_rate"] * original["init_units_on"] * original["init_p_unit"]
    for h, d in md.hydro.items():
        unit = md.hydro_units[h]
        if d["units"] != [h]:
            raise ValueError("水电日电量续程仅支持现有一站一聚合主体映射")
        for t in b.F:
            for z, (lo, hi) in enumerate(unit["zones"]):
                add(b.pz[h, z, t] >= lo*b.delta[h, z, t])
                add(b.pz[h, z, t] <= hi*b.delta[h, z, t])
                if mode != "sced":
                    b.delta[h, z, t].fix(int(delta[h, z][case.t0+t]))
            add(b.Ph[h, t] == sum(b.pz[h, z, t] for z in range(len(unit["zones"]))))
            add(online(h, t) <= unit["n_units"])
            add(b.Ph[h, t] <= sum(unit["L"]))
            if mode == "fixed_da":
                b.Ph[h, t].fix(float(power[h][case.t0+t]))
            previous = variable("Ph", h, t-1)
            add(b.Ph[h, t]-previous <= unit["ramp"]*60*md.dt*online(h, t))
            add(previous-b.Ph[h, t] <= unit["ramp"]*60*md.dt*online(h, t-1))
            upstream = release(d["upstream"], t-int(d.get("lag_periods", 0))) if d.get("upstream") else 0.
            qout = release(h, t)
            add(b.V[h, t] == variable("V", h, t-1)+(d["qin"]+upstream-qout)*md.dt*.36)
            add(pyo.inequality(d["vmin"], b.V[h, t], d["vmax"]))
            add(qout >= d["qeco"])
            if d.get("qout_max") is not None:
                add(qout <= d["qout_max"])
        if d.get("e_day") is not None:
            add((sum(m.Ph[h, t] for t in m.T)+sum(b.Ph[h, t] for t in b.F))*md.dt <= d["e_day"]+1e-6)
    return b


def diagnostics(m):
    required = getattr(m, "_hydro_daily_energy", {})
    if not required:
        return dict(active=False, rows=[], suffix=None)
    b = getattr(m, "hydro_daily_energy_suffix", None)
    rows = []
    for h, inputs in required.items():
        window = sum(float(pyo.value(m.Ph[h, t])) for t in m.T)*m._md.dt
        future = sum(float(pyo.value(b.Ph[h, t])) for t in b.F)*m._md.dt if b is not None else 0.
        rows.append(dict(unit=h, **inputs, window_generation_mwh=window,
            witness_generation_mwh=future, remaining_upper_mwh=m._md.hydro[h].get("e_day"),
            minimum_margin_mwh=window+future-inputs["remaining_minimum_mwh"]))
    suffix = None
    if b is not None:
        suffix = dict(start_period=m._case.t0+m._md.T, end_period_exclusive=m._case.cfg.T,
            quantities_execute=False, quantities_settle=False, zero_objective_weight=True,
            scope=SCOPE, reference_metadata=copy.deepcopy(b._reference_metadata), hydro_mode=b._hydro_mode,
            integers_fixed=all(v.fixed for v in b.delta.values()),
            rows=[dict(unit=h, period=m._case.t0+t, power_mw=float(pyo.value(b.Ph[h,t])),
                volume_1e4m3=float(pyo.value(b.V[h,t])), spill_m3s=float(pyo.value(b.Qspill[h,t])),
                zone_counts=[float(pyo.value(b.delta[h,z,t])) for z in range(len(m._md.hydro_units[h]["zones"]))])
                for h in m.HS for t in b.F])
    return dict(active=True, stage=m._case.stage, start_period=m._case.t0,
        end_period_exclusive=m._case.t0+m._md.T, rows=rows, suffix=suffix,
        rule="hard_daily_minimum_no_shortage_slack", scope=SCOPE)


def audit(cfg, da_snapshot, rr, tolerance=1e-5, *, bids=None):
    """Recompute executed daily energy, not overlapping forecast-window totals."""
    minimum = limits(cfg)
    if not minimum:
        return dict(hydro_daily_minimum_active=False, hydro_daily_minimum_evidence_valid=True,
            hydro_daily_minimum_final_satisfied=None, hydro_daily_minimum_records=[],
            hydro_daily_minimum_errors=[], hydro_daily_minimum_scope="not_configured")
    errors = []
    def fail(message):
        if len(errors) < 40:
            errors.append(message)
    capacities = {}
    try:
        for spec in cfg.units["hydro"]:
            h = spec["id"]
            segments = bids["units"][h]["segments"]
            if not isinstance(segments, list) or not segments:
                raise ValueError("missing hydro bid segments")
            capacities[h] = sum(_number(s["p_end"]-s["p_start"], f"{h}.bid_width") for s in segments)
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"missing/invalid independently supplied hydro bids: {exc}")
    def array(sn, h, length, label):
        try:
            value = np.asarray(sn.get("Ph", {}).get(h), dtype=float)
            if value.shape != (length,) or not np.isfinite(value).all() or (value < -tolerance).any():
                raise ValueError()
            return value
        except (TypeError, ValueError):
            fail(f"{label}.{h}: missing/nonfinite/invalid power coverage")
            return None
    rounds = getattr(rr, "rt", [])
    periods = [r.get("t") for r in rounds]
    valid_prefix = (all(type(t) is int for t in periods) and periods == list(range(len(periods)))
                    and len(periods) <= cfg.T)
    if not valid_prefix:
        fail("RT periods are not a unique continuous prefix")
    complete = valid_prefix and len(rounds) == cfg.T
    executed = {h: 0. for h in minimum}
    def check_window(sn, start, stage, before, state=None, round_result=None):
        evidence = sn.get("hydro_daily_energy", {})
        if not isinstance(evidence, dict) or evidence.get("active") is not True:
            fail(f"{stage}@{start}: missing daily energy evidence")
            return
        end = evidence.get("end_period_exclusive")
        expected_end = (min(start+int(round(cfg.mode['realtime']['lookahead_h']/cfg.dt)), cfg.T)
                        if stage == "RT" else cfg.T)
        if (evidence.get("start_period") != start or not isinstance(end, int)
                or not start < end <= cfg.T or evidence.get("stage") != stage or end != expected_end
                or evidence.get("rule") != "hard_daily_minimum_no_shortage_slack"):
            fail(f"{stage}@{start}: invalid window scope")
            return
        rows = evidence.get("rows", [])
        if (not isinstance(rows, list) or len(rows) != len(minimum)
                or any(not isinstance(r, dict) for r in rows)
                or {r.get("unit") for r in rows} != set(minimum)):
            fail(f"{stage}@{start}: invalid daily minimum subject coverage")
            return
        suffix = evidence.get("suffix")
        future = {h: 0. for h in minimum}
        needs_suffix = (stage == "RT" and end < cfg.T
                        and any(max(0., minimum[h]-before[h]) > 1e-6 for h in minimum))
        if needs_suffix:
            if (not isinstance(suffix, dict) or suffix.get("start_period") != end
                    or suffix.get("end_period_exclusive") != cfg.T or suffix.get("integers_fixed") is not True
                    or suffix.get("quantities_execute") is not False or suffix.get("quantities_settle") is not False
                    or suffix.get("zero_objective_weight") is not True or suffix.get("scope") != SCOPE):
                fail(f"RT@{start}: missing/invalid hydro suffix")
                return
            frows = suffix.get("rows", [])
            expected = {(d["id"], t) for d in cfg.units["hydro"] for t in range(end, cfg.T)}
            if (not isinstance(frows, list) or len(frows) != len(expected)
                    or any(not isinstance(r, dict) for r in frows)
                    or {(r.get("unit"), r.get("period")) for r in frows} != expected):
                fail(f"RT@{start}: incomplete hydro suffix rows")
                return
            for r in frows:
                try:
                    power = _number(r.get("power_mw"), "suffix power")
                except ValueError:
                    fail(f"RT@{start}: invalid suffix power")
                    return
                if r["unit"] in future:
                    future[r["unit"]] += power*cfg.dt
        elif suffix is not None:
            fail(f"{stage}@{start}: unexpected suffix")
        for row in rows:
            h = row["unit"]
            power = array(sn, h, end-start, f"{stage}@{start}")
            if power is None:
                continue
            required = max(0., minimum[h]-before[h])
            window = float(power.sum()*cfg.dt)
            if h not in capacities or (power > capacities[h]+tolerance).any():
                fail(f"{stage}@{start}.{h}: offered power capacity exceeded/unavailable")
            expectations = dict(daily_minimum_mwh=minimum[h], executed_before_mwh=before[h],
                remaining_minimum_mwh=required, window_generation_mwh=window,
                witness_generation_mwh=future[h], minimum_margin_mwh=window+future[h]-required)
            for key, value in expectations.items():
                try:
                    actual = float(row[key])
                    if not math.isfinite(actual) or abs(actual-value) > tolerance:
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    fail(f"{stage}@{start}.{h}.{key}: inconsistent")
            if window+future[h] < required-tolerance:
                fail(f"{stage}@{start}.{h}: hard minimum shortfall")
        if needs_suffix and isinstance(suffix, dict):
            references = _audit_suffix_references(cfg, da_snapshot, rr, round_result, start, suffix, fail)
            _audit_suffix_physics(cfg, sn, start, end, suffix, state, fail, tolerance, capacities, references)
    check_window(da_snapshot, 0, "DA", {h: 0. for h in minimum})
    executed_before = []
    for i, r in enumerate(rounds):
        executed_before.append(dict(executed))
        state = getattr(rr, "states", [])[i] if i < len(getattr(rr, "states", [])) else None
        if getattr(state, "t", None) != i:
            fail(f"RT@{i}: actual-state timestamp inconsistent")
        for h in minimum:
            try:
                if abs(_number(state.hydro_used[h], "actual hydro_used")-executed[h]) > tolerance:
                    raise ValueError()
            except (AttributeError, KeyError, TypeError, ValueError):
                fail(f"RT@{i}.{h}: actual cumulative energy inconsistent")
        check_window(r.get("snap", {}), i, "RT", executed, state, r)
        sn = r.get("snap", {})
        for h in minimum:
            try:
                vals = np.asarray(sn.get("Ph", {}).get(h), dtype=float)
                if vals.ndim != 1 or not len(vals) or not np.isfinite(vals).all() or vals[0] < -tolerance:
                    raise ValueError()
                executed[h] += float(vals[0])*cfg.dt
            except (TypeError, ValueError):
                fail(f"RT@{i}.{h}: missing executed first-period energy")
    for r in getattr(rr, "id_rounds", []):
        tau = r.get("tau")
        if type(tau) is not int or not 0 <= tau < len(executed_before):
            fail("ID round cannot be matched to executed history")
            continue
        check_window(r.get("snap", {}), tau, "ID", executed_before[tau])
    final = getattr(rr, "states", [])[-1] if len(getattr(rr, "states", [])) == len(rounds)+1 else None
    if getattr(final, "t", None) != len(rounds):
        fail("final actual-state timestamp inconsistent")
    records = []
    for h, target in minimum.items():
        try:
            if abs(_number(final.hydro_used[h], "final hydro_used")-executed[h]) > tolerance:
                raise ValueError()
        except (AttributeError, KeyError, TypeError, ValueError):
            fail(f"{h}: final cumulative energy inconsistent")
        records.append(dict(unit=h, minimum_mwh=target, executed_mwh=executed[h],
            remaining_mwh=max(0., target-executed[h]), full_day=complete,
            final_satisfied=bool(complete and executed[h] >= target-tolerance) if complete else None))
        if complete and executed[h] < target-tolerance:
            fail(f"{h}: actual full-day minimum shortfall {target-executed[h]:.9g} MWh")
    return dict(hydro_daily_minimum_active=True, hydro_daily_minimum_evidence_valid=not errors,
        hydro_daily_minimum_full_day=complete,
        hydro_daily_minimum_final_satisfied=bool(not errors and all(r["final_satisfied"] for r in records)) if complete else None,
        hydro_daily_minimum_records=records, hydro_daily_minimum_errors=errors,
        hydro_daily_minimum_scope="DA_and_ID_remaining_day;RT_asset_witness;actual_executed_first_periods_only")


def _audit_suffix_physics(cfg, sn, start, end, suffix, state, fail, tol, capacities, references):
    """Independently reconstruct the asset witness from detached quantities."""
    specs = {h["id"]: h for h in cfg.units["hydro"]}
    records = {(r["unit"], r["period"]): r for r in suffix["rows"]}
    from .input_validation import hydro_volume_bounds
    def finite(value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("nonfinite witness")
        return value
    def power(h, t):
        return finite(records[h,t]["power_mw"] if t >= end else sn["Ph"][h][t-start])
    def counts(h, t):
        return (np.asarray(records[h,t]["zone_counts"], float) if t >= end else
                np.array([sn["delta"][h,z][t-start] for z in range(len(specs[h]["zones"]))], float))
    def release(h, t):
        if t >= end:
            return specs[h]["water_rate"]*power(h,t)+finite(records[h,t]["spill_m3s"])
        if t >= start:
            return finite(sn["Qout"][h][t-start])
        if t >= 0:
            return finite(state.hydro_hist[h][t])
        d = specs[h]
        return d["water_rate"]*d["init_units_on"]*d["init_p_unit"]
    try:
        for h,d in specs.items():
            season = d["seasons"][cfg.base["season"]]
            lo,hi = hydro_volume_bounds(d,cfg.base["season"])
            volume = finite(sn["Vh"][h][-1])
            window_generation = sum(finite(x) for x in sn["Ph"][h])*cfg.dt
            future_generation = 0.
            for t in range(end,cfg.T):
                r = records[h,t]
                p = power(h,t); n = counts(h,t); prevn = counts(h,t-1)
                if (n.shape != (len(d["zones"]),) or not np.isfinite(n).all()
                        or (n < -tol).any() or (abs(n-n.round()) > tol).any() or n.sum() > d["n_units"]+tol):
                    raise ValueError(f"{h}@{t}: integer zone counts")
                lower = sum(v*z[0] for v,z in zip(n,d["zones"]))
                upper = sum(v*z[1] for v,z in zip(n,d["zones"]))
                if p < lower-tol or p > upper+tol:
                    raise ValueError(f"{h}@{t}: stable-zone power")
                if h not in capacities or p > capacities[h]+tol:
                    raise ValueError(f"{h}@{t}: frozen offered power capacity")
                reference = references.get(h)
                if reference is not None:
                    plan, offset, fixed_power = reference
                    expected_counts = np.array([plan['delta'][h,z][t-offset] for z in range(len(d['zones']))], float)
                    if not np.allclose(n, expected_counts, atol=tol, rtol=0.):
                        raise ValueError(f"{h}@{t}: selected reference zone counts")
                    if fixed_power and abs(p-float(plan['Ph'][h][t-offset])) > tol:
                        raise ValueError(f"{h}@{t}: fixed DA power")
                previous = power(h,t-1)
                if p-previous > d["ramp"]*60*cfg.dt*n.sum()+tol or previous-p > d["ramp"]*60*cfg.dt*prevn.sum()+tol:
                    raise ValueError(f"{h}@{t}: cross-period ramp")
                q = release(h,t)
                if finite(r["spill_m3s"]) < -tol or q < season["qeco"]-tol or (d.get("qout_max") is not None and q > d["qout_max"]+tol):
                    raise ValueError(f"{h}@{t}: ecological/outflow limit")
                upstream = release(d["upstream"],t-int(d.get("lag_periods",0))) if d.get("upstream") else 0.
                expected = volume+(season["qin"]+upstream-q)*cfg.dt*.36
                volume = finite(r["volume_1e4m3"])
                if abs(volume-expected)>tol or volume<lo-tol or volume>hi+tol:
                    raise ValueError(f"{h}@{t}: water balance/volume bounds")
                future_generation += p*cfg.dt
            if d.get("regulation", "reservoir") == "reservoir" and season.get("e_day") is not None:
                used = finite(state.hydro_used[h])
                if used+window_generation+future_generation > season["e_day"]+tol:
                    raise ValueError(f"{h}: remaining daily upper limit")
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        fail(f"RT@{start}: invalid hydro physical witness ({exc})")


def audit_exported(cfg, hydro_evidence, actual_states, tolerance=1e-5, *, bids=None, id_failures=None):
    """Read existing full-precision JSON artifacts; never trust summary green lights.

    Inputs are parsed hydro_window_evidence.json and rolling_actual_states.json.
    cfg must be loaded from the run's frozen inputs, not today's repository.
    """
    from types import SimpleNamespace
    if not limits(cfg):
        return audit(cfg, {}, SimpleNamespace(rt=[], states=[]), tolerance, bids=bids)
    try:
        if (float(hydro_evidence["dt_h"]) != cfg.dt or hydro_evidence["season"] != cfg.base["season"]):
            raise ValueError("frozen time/season mismatch")
        def snapshot(raw):
            sn = dict(raw)
            records = raw["delta"]
            keys = [(r["station"], r["zone"]) for r in records]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate hydro zone evidence")
            sn["delta"] = {key: row["values"] for key,row in zip(keys, records)}
            return sn
        rr = SimpleNamespace(
            rt=[dict(t=r["t"], snap=snapshot(r["snapshot"]), reference_metadata=r.get('reference_metadata', {}),
                     handoff_config=r.get('handoff_config', {})) for r in hydro_evidence["rt_windows"]],
            id_rounds=[dict(tau=r["tau"], snap=snapshot(r["snapshot"]), state_values=r.get('state_values'))
                       for r in hydro_evidence["id_rounds"]],
            id_failures=hydro_evidence.get('id_failures', []) if id_failures is None else id_failures,
            states=[SimpleNamespace(**r) for r in actual_states["windows"]])
        rr.states.append(SimpleNamespace(**actual_states["final_executed_state"]))
        return audit(cfg, snapshot(hydro_evidence["da"]), rr, tolerance, bids=bids)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        return dict(hydro_daily_minimum_active=True, hydro_daily_minimum_evidence_valid=False,
            hydro_daily_minimum_full_day=False, hydro_daily_minimum_final_satisfied=False,
            hydro_daily_minimum_records=[], hydro_daily_minimum_errors=[f"missing/invalid full-precision daily energy evidence: {exc}"],
            hydro_daily_minimum_scope="DA_and_ID_remaining_day;RT_asset_witness;actual_executed_first_periods_only")


def id_state_values(round_result):
    """Archive the same formal SCED bundle used for handoff validity decisions."""
    if 'state_values' in round_result:
        return round_result['state_values']
    model = round_result.get('model')
    if model is None or not hasattr(model, 'dual'):
        return None
    from .scuc import state_values
    try:
        return state_values(model)
    except KeyError:
        return None  # Matches the handoff's explicitly missing-dual fallback.


def _audit_suffix_references(cfg, da, rr, round_result, start, suffix, fail):
    """Choose DA/ID from frozen switches and actual attempts, not witness claims."""
    from .handoff import _array, _bounds, _hydro_arrays
    rc = cfg.mode['realtime']
    mode = rc.get('hydro', 'value')
    if suffix.get('hydro_mode') != mode:
        fail(f"RT@{start}: suffix hydro mode differs from frozen configuration")
    if mode == 'sced':
        return {}
    metadata = (round_result or {}).get('reference_metadata', {}).get('V', {})
    suffix_meta = suffix.get('reference_metadata', {})
    use_id = (mode == 'value' and cfg.mode.get('intraday', {}).get('enabled', False)
              and rc.get('handoff_source', 'intraday') == 'intraday'
              and rc.get('handoff', {}).get('hydro', True))
    successes = [r for r in getattr(rr, 'id_rounds', []) if type(r.get('tau')) is int and r['tau'] <= start]
    failures = [r for r in getattr(rr, 'id_failures', []) if type(r.get('tau')) is int and r['tau'] <= start]
    latest = max(successes, key=lambda r:r['tau']) if successes else None
    last_failure = max((r['tau'] for r in failures), default=-1)
    if latest is not None and last_failure == latest['tau']:
        fail(f"RT@{start}: conflicting successful and failed ID attempt")
    source = latest if use_id and latest is not None and latest['tau'] > last_failure else None
    values = id_state_values(source) if source is not None else None
    references = {}
    for d in cfg.units['hydro']:
        h = d['id']; chosen, offset, label, round_number = da, 0, 'day_ahead', None
        invalid_id = False
        if source is not None:
            try:
                count = cfg.T-source['tau']; sn = source['snap']
                _array(sn.get('Vh', {}).get(h), count, f'ID.Vh.{h}', bounds=_bounds(cfg,'V',h))
                _array(sn.get('Qout', {}).get(h), count, f'ID.Qout.{h}', bounds=(0,np.inf))
                _hydro_arrays(cfg,sn,h,count,'ID')
                rec = (values or {}).get('V', {}).get(h, {})
                for key in ('value_native','value_yuan_per_mwh','dual_raw'):
                    _array(rec.get(key),count,f'ID.values.{h}.{key}')
                chosen, offset = sn, source['tau']
                label, round_number = f'intraday@{offset}', offset
            except (KeyError, TypeError, ValueError):
                invalid_id = True
        meta = metadata.get(h)
        if (not isinstance(meta,dict) or meta.get('trajectory_source') != label
                or meta.get('round') != round_number or suffix_meta.get(h) != meta
                or (invalid_id and not meta.get('fallback_reason'))):
            fail(f"RT@{start}.{h}: selected DA/ID reference provenance inconsistent")
        try:
            _hydro_arrays(cfg,chosen,h,cfg.T-offset,'selected_daily_energy_reference')
            if mode=='fixed_da':
                _array(chosen.get('Ph',{}).get(h),cfg.T-offset,f'fixed_DA.Ph.{h}',bounds=(0,d['n_units']*d['unit_pmax']))
            references[h] = chosen, offset, mode=='fixed_da'
        except (KeyError, TypeError, ValueError) as exc:
            fail(f"RT@{start}.{h}: missing independently selected reference ({exc})")
    return references
