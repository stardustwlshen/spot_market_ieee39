"""Same-run, explicitly sourced energy cash; never official settlement.

Read-only archive adapters plus the existing strict external-input/energy-cash
arithmetic. No solver, historical fallback, price choice, or booking is used.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
from math import fsum
import re
from zoneinfo import ZoneInfo

from .gd_energy_cash import EnergyPriceInput, SettlementKey
from .gd_external_inputs import (
    _decode, _list, _object, _text, _time, build_contract_energy_report,
    json_safe, load_contract_energy_inputs, read_json_document,
)
from .gd_price_research import EvidenceValue, SelectedCashPrice, _number
from .gd_run_archive import digest, read_run, strict_json, within
from .gd_spot_only_inputs import build_spot_only_cash, load_spot_only_inputs

SCHEMA = "gd.run_cash.v1"
PRICE_INTERVAL_POLICY = "explicit_prices_at_each_settlement_interval"
MODE = "participating_generator_storage_DA_RT_deviation"
_TOTALS = ("day_ahead_cash_yuan", "realtime_cash_yuan", "energy_deviation_cash_yuan")


class RunCashInputError(ValueError):
    """Contradictory identity, provenance, coverage, or source mapping."""


def _require(condition, message):
    if not condition:
        raise RunCashInputError(message)


class _Run:
    def __init__(self, run_dir, run_id, selected, asof):
        _text(run_id, "expected_run_id")
        _text(selected, "expected_selected_dispatch")
        self.asof = _time(asof, "asof")
        self.root = run_dir
        self.view = read_run(run_dir)
        _require(self.view["verified"], "run archive not verified: " + "; ".join(self.view["issues"]))
        _require(self.view["run_id"] == run_id, "run_id mismatch")
        self.manifest = self.view["manifest"]
        self.selected = self.view["selected_dispatch"]
        if self.selected is not None:
            _require(self.selected["dispatch_id"] == selected, "selected_dispatch mismatch")
            _require(self.view["stages"]["market_da"]["status"] == "completed",
                     "selected DA is not completed")
            _require(self.view["stages"]["inputs"]["status"] == "completed" and
                     self.manifest["input_sha256"] is not None and self.manifest["source_sha256"] is not None,
                     "selected DA requires a complete frozen input/source identity")
            selected_record = next(r for r in self.view["outputs"] if r["path"] == self.selected["path"])
            _require(selected_record["role"] == "selected_dispatch", "selected DA artifact role mismatch")
        self.expected_selected = selected
        self.outputs = {r["path"]: r for r in self.view["outputs"]}
        self.cache = {}
        self.parsed = {}

    def require_selected(self):
        _require(self.selected is not None, "selected DA unavailable in this run")

    def read(self, path, stage, *, completed=False):
        self.require_selected()
        _text(path, "artifact_path")
        r = self.outputs.get(path)
        _require(r is not None, "artifact not registered in this run: " + path)
        _require(r["stage"] == stage and r["run_id"] == self.view["run_id"] and
                 r["selected_dispatch_id"] == (None if stage == "inputs" else self.selected["dispatch_id"]),
                 "artifact run/stage/selected DA mismatch")
        if completed:
            _require(self.view["stages"][stage]["status"] == "completed", "artifact stage not completed")
        if path not in self.cache:
            data = within(self.root, path).read_bytes()
            _require(len(data) == r["size"] and sha256(data).hexdigest() == r["sha256"],
                     "artifact changed since archive verification")
            self.cache[path] = data
        return self.cache[path]

    def json(self, path, stage, *, completed=False):
        data = self.read(path, stage, completed=completed)
        try:
            if ("json", path) not in self.parsed:
                self.parsed["json", path] = strict_json(data)
            return self.parsed["json", path]
        except (RecursionError, UnicodeError) as exc:
            raise RunCashInputError("invalid registered JSON: " + path) from exc

    def csv(self, path, stage, required):
        try:
            reader = csv.DictReader(StringIO(self.read(path, stage).decode("utf-8")), strict=True)
            names = reader.fieldnames
            _require(names and len(names) == len(set(names)) and set(required) <= set(names),
                     "CSV missing/duplicate headers: " + path)
            rows = list(reader)
            _require(all(None not in r and all(v is not None for v in r.values()) for r in rows),
                     "CSV malformed row: " + path)
            return rows
        except (csv.Error, UnicodeError) as exc:
            raise RunCashInputError("invalid CSV: " + path) from exc

    def finish(self):
        latest = read_run(self.root)
        _require(latest["verified"] and digest(latest["manifest"]) == digest(self.manifest),
                 "run archive changed during cash mapping; take a stable snapshot")

    def binding(self):
        return dict(run_id=self.view["run_id"], selected_dispatch=self.expected_selected,
            selected_dispatch_sha256=None if self.selected is None else self.selected["sha256"],
            input_sha256=self.manifest["input_sha256"], source_sha256=self.manifest["source_sha256"])


def _csv_number(value, label):
    _require(isinstance(value, str) and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value),
             label + ": explicit decimal CSV number required")
    return _number(float(value), label)


def _period(value, label):
    _require(type(value) is int and value >= 0, label + ": nonnegative integer required")
    return value


def _csv_period(value):
    _require(isinstance(value, str) and re.fullmatch(r"0|[1-9]\d*", value), "CSV global_period must be integer")
    return int(value)


def _variables(run, path, stage):
    if ("variables", path) in run.parsed:
        return run.parsed["variables", path]
    result = {}
    for row in run.csv(path, stage, ("name", "value", "fixed")):
        _text(row["name"], "variable name")
        _require(row["name"] not in result, "duplicate variable name")
        result[row["name"]] = _csv_number(row["value"], "variable value")
    run.parsed["variables", path] = result
    return result


def _timeline(run, path, stage):
    if ("timeline", path) in run.parsed:
        return run.parsed["timeline", path]
    result = {}
    for row in run.csv(path, stage, ("global_period", "start", "end")):
        g = _csv_period(row["global_period"])
        _require(g not in result, "duplicate global_period in time evidence")
        start, end = _time(row["start"], "start"), _time(row["end"], "end")
        hours = (end - start).total_seconds() / 3600
        _require(hours == .25, "current model adapter requires explicit 15-minute intervals")
        if stage == "market_da":
            _require("duration_h" in row and _csv_number(row["duration_h"], "duration_h") == hours,
                     "DA duration_h does not match timestamps")
        result[g] = (start, end)
    ordered = sorted(result.values())
    _require(all(left[1] <= right[0] for left,right in zip(ordered,ordered[1:])),
             "duplicate or overlapping source time intervals")
    run.parsed["timeline", path] = result
    return result


def _component(key):
    _require(isinstance(key, SettlementKey), "typed SettlementKey required")
    _text(key.resource, "model resource")
    _text(key.resource_kind, "model resource_kind")
    _require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.resource) is not None,
             "model resource needs an explicit supported identifier")
    _require(key.direction in {"export", "import"}, "quantity direction required")
    if key.resource_kind == "thermal":
        _require(key.direction == "export", "thermal import is unsupported")
        return "power", "thermal_power_mw", 1
    if key.resource_kind == "battery":
        return (("gd_storage.discharge", "battery_discharge_mw", 1) if key.direction == "export"
                else ("gd_storage.charge", "battery_charge_mw", -1))
    if key.resource_kind == "pumped":
        return (("gd_storage.generate", "pumped_generation_mw", 1) if key.direction == "export"
                else ("gd_storage.pump", "pumped_pumping_mw", -1))
    raise RunCashInputError("model quantity mapping supports thermal/battery/pumped units only; no renewable node-to-resource guess")


def _unique_rows(rows, field, label):
    out = {}
    for row in _list(rows, label):
        _require(type(row) is dict and field in row, label + ": missing identity")
        identity = row[field]
        _text(identity, label + "." + field)
        _require(identity not in out, "duplicate " + label + " identity")
        out[identity] = row
    return out


def _declared_bool(row, field):
    value = row.get(field)
    _require(value is None or type(value) is bool, "source risk flag must be Boolean or unknown: " + field)
    return value


def _combined_flag(values, *, any_true):
    values = list(values)
    if any_true:
        return True if True in values else (False if values and all(v is False for v in values) else None)
    return False if False in values else (True if values and all(v is True for v in values) else None)


def _risk_summary(rows):
    return dict(derived_simulation=bool(rows),
        uses_future_actual=_combined_flag((r["uses_future_actual"] for r in rows), any_true=True),
        forecast_causal_online=_combined_flag((r["forecast_causal_online"] for r in rows), any_true=False),
        causal_eligible=False, causal_eligibility_status="not_certified",
        causal_online_execution_verified=False, model_source_authenticity_verified=False,
        source_evidence=[e for r in rows for e in r.get("source_evidence", [])])


def _forecast_semantics(kind, future, online):
    # These exact enums have defined semantics in gd_rt_inputs.map_forecast.
    # Missing declarations remain unknown; a supplied contradictory flag fails.
    _require(kind is None or type(kind) is str, "forecast kind must be text or unknown")
    if kind in {"offline_truth_conditioned_research", "offline_future_truth_conditioned"}:
        _require(future is not False and online is not True,
                 "offline truth-conditioned forecast kind contradicts risk flags")
    elif kind == "issued_forecast":
        _require(future is not True and online is not False,
                 "issued forecast kind contradicts risk flags")


def _rt_risk(row, index, report, summary, summary_path):
    forecast = row["audit"].get("forecast", {})
    _require(type(forecast) is dict, "RT forecast risk declaration must be an object")
    source = dict(artifact_path="stages/market_rt/roll_report.json",
        json_path=["completed_windows",index,"audit","forecast"],
        declarations={k: forecast.get(k) for k in ("kind","uses_future_actual","causal_online_forecast","authenticity_verified")})
    evidence = [source, dict(artifact_path="stages/market_rt/roll_report.json",json_path=[],
        declarations={k:report.get(k) for k in ("execution_mode","causal_state_information_gate","provider_authenticity_verified")})]
    if summary_path is not None:
        evidence.append(dict(artifact_path=summary_path,json_path=["summary"],
            declarations={k:summary.get(k) for k in ("causal_online_forecast","actual_replay")}))
    future = _declared_bool(forecast,"uses_future_actual")
    online, summary_online = _declared_bool(forecast,"causal_online_forecast"), _declared_bool(summary,"causal_online_forecast")
    _forecast_semantics(forecast.get("kind"),future,online)
    _require(not (future is True and (online is True or summary_online is True)),
             "future-conditioned RT source contradicts a causal-online declaration")
    _require(not (summary_online is True and online is False), "RT summary contradicts noncausal window forecast")
    return dict(derived_simulation=True,uses_future_actual=future,
        forecast_causal_online=_combined_flag((_declared_bool(forecast,"causal_online_forecast"),
            _declared_bool(summary,"causal_online_forecast")),any_true=False),
        causal_eligible=False,causal_eligibility_status="not_certified",source_evidence=evidence,
        model_source_authenticity_verified=False,receipt_kind="executed_simulation")


def _rt_samples(run, component, command_family, resource):
    cache_key = ("rt_samples", component, resource)
    if cache_key in run.parsed:
        return run.parsed[cache_key]
    path = "stages/market_rt/roll_report.json"
    report = run.json(path, "market_rt")
    _require(type(report) is dict and report.get("selected_da_source_sha256") == run.selected["sha256"],
             "RT report selected DA checksum mismatch")
    _require(report.get("execution_mode") == run.manifest["config"]["execution_mode"], "RT report execution mode mismatch")
    plans = _unique_rows(report.get("issued_execution_plans"), "plan_id", "issued plans")
    receipts = _unique_rows(report.get("execution_observations"), "observation_id", "execution observations")
    confirmed = _timeline(run, "stages/market_rt/executed_confirmed_intervals.csv", "market_rt")
    summary_records = [r for r in run.outputs.values() if r["stage"] == "market_rt" and r["role"] == "stage_summary"]
    _require(len(summary_records) <= 1, "duplicate RT stage summaries")
    summary_path = summary_records[0]["path"] if summary_records else None
    summary = {} if summary_path is None else run.json(summary_path,"market_rt")["summary"]
    samples, risks, window_numbers, receipt_ids = {}, {}, set(), set()
    for index, row in enumerate(_list(report.get("completed_windows"), "completed windows")):
        _require(type(row) is dict and type(row.get("window")) is dict, "malformed completed window")
        n = _period(row.get("window_number"), "window_number")
        periods = _list(row["window"].get("global_periods"), "window global_periods")
        _require(periods and all(type(p) is int and p >= 0 for p in periods) and len(set(periods)) == len(periods),
                 "invalid window global_periods")
        g = periods[0]
        _require(g not in samples and n not in window_numbers, "duplicate confirmed execution window")
        window_numbers.add(n)
        intervals = row["window"].get("intervals")
        _require(type(intervals) is dict and str(g) in intervals, "window first interval missing")
        times = intervals[str(g)]
        _require(type(times) is list and len(times) == 2, "window interval pair required")
        start, end = _time(times[0], "window start"), _time(times[1], "window end")
        _require(confirmed.get(g) == (start, end), "confirmed execution CSV does not match first window interval")
        audit = row.get("audit")
        _require(type(audit) is dict and type(audit.get("da")) is dict and
                 audit["da"].get("source_sha256") == run.selected["sha256"] and
                 audit["da"].get("references_are_selected_dispatch") is True,
                 "RT window selected DA evidence mismatch")
        wa = audit.get("window")
        _require(type(wa) is dict and type(wa.get("local_to_global")) is dict and
                 wa["local_to_global"].get("0") == g and type(wa["local_to_global"].get("0")) is int and
                 _time(wa.get("first_execution_start"), "audit execution start") == start and
                 _time(wa.get("first_execution_end"), "audit execution end") == end,
                 "RT local zero is not mapped to the confirmed global execution interval")
        plan = plans.get(f"rt_roll_window_{n}_global_{g}")
        _require(plan is not None and plan.get("kind") == "issued_research_execution", "matching issued research plan missing")
        _require((_time(plan.get("start"), "plan start"), _time(plan.get("end"), "plan end")) == (start, end),
                 "issued plan is not the confirmed first interval")
        _require(_time(plan.get("issued_at"), "plan issued_at") <= _time(plan.get("available_at"), "plan available_at") <= start,
                 "plan was unavailable at execution start")
        receipt = row.get("execution_observation")
        _require(type(receipt) is dict and receipt.get("kind") == "executed_simulation",
                 "model command is only a synthetic execution assumption, never external meter data")
        rid = receipt.get("observation_id")
        _text(rid, "execution observation_id")
        _require(rid not in receipt_ids and receipts.get(rid) == receipt, "missing/duplicate/mismatched execution receipt")
        receipt_ids.add(rid)
        _text(receipt.get("source"), "execution receipt source")
        observed, available = _time(receipt.get("observed_at"), "receipt observed_at"), _time(receipt.get("available_at"), "receipt available_at")
        _require(observed == end and observed <= available, "execution receipt is not available after interval end")
        payload = plan.get("payload")
        _require(type(payload) is dict and type(payload.get("command")) is dict, "issued command missing")
        command = payload["command"].get(command_family)
        _require(type(command) is dict and resource in command, "resource/direction missing from issued command")
        planned, observed_state = payload.get("planned_boundary"), receipt.get("state")
        _require(type(planned) is dict and type(observed_state) is dict and
                 planned == row.get("first_boundary") and
                 set(observed_state) == {"thermal", "storage"} and
                 observed_state == {k: planned.get(k) for k in ("thermal", "storage")},
                 "synthetic receipt does not confirm this plan boundary")
        metrics = row.get("stage_metrics")
        selected_stage = "RT_SCED_fixed_state_continuous_polish"
        _require(type(metrics) is list and metrics and all(type(m) is dict for m in metrics) and metrics[-1].get("stage") == selected_stage and
                 sum(m.get("stage") == selected_stage for m in metrics) == 1,
                 "RT selected continuous-polish stage not explicit; unsupported adapter format")
        variables = _variables(run, f"stages/market_rt/windows/{g:03d}/{selected_stage}/variables.csv", "market_rt")
        name = f"{component}[{resource},0]"
        _require(name in variables, "confirmed RT local-period-zero resource variable missing")
        power = _number(command[resource], "issued MW")
        _require(power >= 0 and variables[name] == power, "issued power differs from selected RT first interval variable")
        samples[g] = (start, end, power, available)
        risks[g] = _rt_risk(row,index,report,summary,summary_path)
    _require(set(samples) == set(confirmed), "completed window inventory differs from confirmed execution CSV")
    _require(set(receipts) == receipt_ids, "execution receipt inventory differs from completed windows")
    run.parsed[cache_key] = samples
    run.parsed["rt_risks", component, resource] = risks
    return samples


def _resource_node(run, key):
    frozen = run.json("stages/inputs/mapping/frozen_source_inputs.json", "inputs", completed=True)
    _require(type(frozen) is dict and type(frozen.get("config")) is dict and
             type(frozen["config"].get("units")) is dict, "frozen resource/node mapping missing")
    units, nodes = frozen["config"]["units"], {}
    for family, kind in (("thermal", "thermal"), ("storage", "battery"), ("pumped", "pumped")):
        for row in _list(units.get(family), "frozen " + family):
            _require(type(row) is dict, "frozen resource row must be an object")
            _text(row.get("id"), "frozen resource ID")
            bus = row.get("bus")
            _require(type(bus) in (int, str) and str(bus).strip(), "frozen resource node missing")
            ids = [row["id"]]
            if family == "pumped":
                n = row.get("n_units")
                _require(type(n) is int and n > 0, "explicit pumped unit inventory required")
                ids = [row["id"] + "_U" + str(j) for j in range(1, n+1)]
            for uid in ids:
                _require(uid not in nodes, "duplicate model resource identity")
                nodes[uid] = (kind, str(bus))
    _require(nodes.get(key.resource) == (key.resource_kind, key.node),
             "model resource kind/node differs from this run frozen inventory")


def _model_energy(run, key, stage, global_periods, mapping_source):
    run.require_selected()
    _text(mapping_source, "mapping_source")
    _require(stage in {"DA", "RT"}, "model stage must be DA or RT")
    _require(type(global_periods) in (list, tuple) and global_periods and
             all(type(g) is int and g >= 0 for g in global_periods) and
             len(set(global_periods)) == len(global_periods), "explicit unique global_periods required")
    component, family, sign = _component(key)
    _resource_node(run, key)
    if stage == "DA":
        variables = _variables(run, run.selected["path"], "market_da")
        timeline = _timeline(run, "stages/market_da/dispatch_by_period.csv", "market_da")
        samples = {}
        for g in global_periods:
            name = f"{component}[{key.resource},{g}]"
            _require(g in timeline and name in variables, "selected DA quantity/time sample missing")
            _require(variables[name] >= 0, "raw direction power must be nonnegative; no sign repair")
            samples[g] = (*timeline[g], variables[name], None)
    else:
        samples = _rt_samples(run, component, family, key.resource)
    selected = []
    for g in global_periods:
        _require(g in samples, "requested execution interval was not confirmed")
        selected.append((g, *samples[g]))
    _require(all(left[2] == right[1] for left, right in zip(selected, selected[1:])),
             "model quantity mapping must be ordered, contiguous, and nonoverlapping")
    start, end = _time(key.start, "settlement start"), _time(key.end, "settlement end")
    _require(selected[0][1] == start and selected[-1][2] == end, "model intervals do not exactly cover settlement interval")
    _require((end-start).total_seconds() in {900, 3600}, "model quantity target must be a quarter-hour or hour")
    if stage == "RT":
        _require(end <= run.asof and all(row[4] <= run.asof for row in selected), "model actual/receipt unavailable at asof")
    try:
        energy = _number(fsum(sign * row[3] * (row[2]-row[1]).total_seconds()/3600 for row in selected), "integrated MWh")
    except OverflowError as exc:
        raise RunCashInputError("integrated MWh arithmetic overflow") from exc
    if stage == "RT":
        risks = [run.parsed["rt_risks",component,key.resource][g] for g in global_periods]
    else:
        frozen_path = "stages/inputs/mapping/frozen_source_inputs.json"
        frozen = run.json(frozen_path,"inputs")
        provenance = frozen.get("provenance", {})
        _require(type(provenance) is dict, "DA provenance must be an object")
        profiles = provenance.get("profiles", {})
        _require(type(profiles) is dict, "DA profile provenance must be an object")
        _forecast_semantics(profiles.get("kind"), _declared_bool(profiles,"uses_future_actual"), _declared_bool(profiles,"causal_online_forecast"))
        risks = [dict(uses_future_actual=_declared_bool(profiles,"uses_future_actual"),
            forecast_causal_online=_declared_bool(profiles,"causal_online_forecast"),
            source_evidence=[dict(artifact_path=frozen_path,json_path=["provenance","profiles"],
                declarations={k:profiles.get(k) for k in ("kind","uses_future_actual","causal_online_forecast")})])]
    research_risks = _risk_summary(risks)
    source = mapping_source + f"; {run.view['run_id']} {stage} explicitly mapped model MW-hours; synthetic, not meter data"
    return dict(evidence=json_safe(EvidenceValue(energy, "synthetic", source)),
        provenance=dict(origin="selected_da_model" if stage == "DA" else "confirmed_rt_model_execution",
                        source=mapping_source, global_periods=list(global_periods)),
        samples=[dict(global_period=g, start=s.isoformat(), end=e.isoformat(), raw_mw=p,
                      direction_sign=sign, available_at=None if a is None else a.isoformat()) for g,s,e,p,a in selected],
        audit=dict(run.binding(), research_risks=research_risks, model_execution_is_meter=False, actual_meter_authenticity_verified=False,
                   quantity_unit="MWh", raw_power_unit="MW", price_aggregation_applied=False))


def model_energy_for_key(*, run_dir, expected_run_id, expected_selected_dispatch,
                         asof, key, stage, global_periods, mapping_source):
    """Read an explicit model interval mapping, returning only synthetic MWh.

    Storage eligibility and full settlement scope are validated by build_run_cash,
    not by this quantity-only helper. No target is auto-discovered or inferred.
    """
    run = _Run(run_dir, expected_run_id, expected_selected_dispatch, asof)
    result = _model_energy(run, key, stage, global_periods, mapping_source)
    run.finish()
    return result


def _provenance(document, run):
    p = read_json_document(document)
    _object(p, ("schema_version", "run_id", "selected_dispatch", "selected_dispatch_sha256", "input_sha256",
                "source_sha256", "source", "price_interval_policy", "quantities", "prices", "renewable_participation"), "cash provenance")
    _require(p["schema_version"] == SCHEMA, "unsupported cash provenance schema")
    for field, expected in run.binding().items():
        _require(p[field] == expected, "cash provenance " + field + " mismatch")
    _text(p["source"], "provenance source")
    _require(p["price_interval_policy"] == PRICE_INTERVAL_POLICY, "explicit settlement-interval prices required; no implicit averaging")
    return p


def _scope_extras(scope, p):
    durations = {}
    for key in scope.keys:
        durations.setdefault(key.resource, set()).add((key.end-key.start).total_seconds())
    _require(all(len(v) == 1 for v in durations.values()), "same resource cannot mix hourly and 15-minute settlement")
    renewable = {k.resource for k in scope.keys if k.resource_kind == "renewable"}
    provided = set()
    for row in _list(p["renewable_participation"], "renewable participation"):
        _object(row, ("resource", "mode", "source"), "renewable participation")
        _text(row["source"], "renewable participation source")
        _text(row["resource"], "renewable resource")
        _require(row["resource"] not in provided and row["resource"] in renewable and row["mode"] == MODE,
                 "duplicate/unsupported renewable participation; cannot assume ordinary generator settlement")
        provided.add(row["resource"])
    _require(provided == renewable, "explicit renewable participation mode is missing")


def _quantity_origin(run, key, evidence, descriptor, stage, record_available_at):
    _object(descriptor, ("origin", "source"), "quantity provenance", ("global_periods",))
    _text(descriptor["source"], "quantity source")
    origin = descriptor["origin"]
    if evidence.kind == "missing":
        _require(origin == "missing" and "global_periods" not in descriptor, "missing quantity needs missing provenance")
        return dict(origin=origin, source=descriptor["source"], meter_authenticity_verified=False)
    model_origin = "selected_da_model" if stage == "DA" else "confirmed_rt_model_execution"
    if origin == model_origin:
        _require("global_periods" in descriptor, "model quantity needs explicit period mapping")
        result = _model_energy(run, key, stage, descriptor["global_periods"], descriptor["source"])
        if stage == "RT":
            _require(all(_time(s["available_at"], "execution available_at") <= record_available_at
                         for s in result["samples"]), "model source receipt arrived after volume record availability")
        expected = result["evidence"]
        _require(evidence.kind == "synthetic" and evidence.value == expected["value"],
                 "model quantity must match this run exactly and remain synthetic")
        return dict(origin=origin, source=descriptor["source"], samples=result["samples"],
                    research_risks=result["audit"]["research_risks"], meter_authenticity_verified=False)
    external = "external_declared_plan" if stage == "DA" else "external_meter"
    _require(origin == external and "global_periods" not in descriptor, "unsupported quantity origin for stage")
    _require(evidence.kind == "external_verified", "external plan/meter requires explicit external declaration; not model execution")
    return dict(origin=origin, source=descriptor["source"], source_is_caller_declaration=True, meter_authenticity_verified=False)


def _price_origin(run, price, row, published_at):
    _object(row, ("key", "stage", "origin", "source"), "price provenance", ("artifact_path", "json_path"))
    _text(row["source"], "price provenance source")
    origin = row["origin"]
    formal_rt = price.selected_price.stage == "RT" and price.selected_price.price_basis == "external_settlement_price"
    if formal_rt:
        delivery_day = price.key.start.astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=0,minute=0,second=0,microsecond=0)
        _require(published_at >= delivery_day+timedelta(days=1),
                 "RT formal settlement publication must be D+1 or later; T-15 prices must remain research/synthetic")
    if origin == "external_price":
        _require("artifact_path" not in row and "json_path" not in row, "external price cannot claim a run artifact")
        return dict(origin=origin, source=row["source"], registered_in_this_run_verified=False,
                    computed_by_this_run_verified=False, price_producer_lineage_verified=False,
                    source_is_caller_declaration=True, formal_rt_publication_timing_checked=formal_rt)
    _require(origin == "verified_run_artifact" and "artifact_path" in row and "json_path" in row,
             "price needs explicit external or registered-artifact origin")
    _require(price.selected_price.value.kind in {"research", "synthetic"}, "model price cannot become official external price")
    stage = "da_research_price" if row["stage"] == "DA" else "rt_research_price"
    value = run.json(row["artifact_path"], stage, completed=True)
    for component in _list(row["json_path"], "price json_path"):
        if type(component) is str:
            _require(type(value) is dict and component in value, "price JSON object path missing")
        else:
            _require(type(component) is int and type(value) is list and 0 <= component < len(value), "price JSON array index invalid")
        value = value[component]
    registered = _decode(EnergyPriceInput, value, "registered cash price")
    registered.selected_price.validate()
    _require(registered == price, "registered price object differs from explicit selected cash input")
    return dict(origin=origin, source=row["source"], artifact_path=row["artifact_path"],
        artifact_sha256=run.outputs[row["artifact_path"]]["sha256"], json_path=row["json_path"],
        registered_in_this_run_verified=True, computed_by_this_run_verified=False,
        price_producer_lineage_verified=False, official_price_verified=False)


def build_run_cash(document, *, run_dir, expected_run_id, expected_selected_dispatch,
                   asof, provenance=None):
    """Join explicit cash inputs to one verified selected DA and source mapping.

    No document means unavailable, never a default no-contract scenario. Invalid
    identities/mappings raise; missing expected quantities/prices remain None in
    the existing ledger. Passing this API does not authenticate outside sources.
    """
    run = _Run(run_dir, expected_run_id, expected_selected_dispatch, asof)
    if document is None or run.selected is None:
        missing = []
        if document is None:
            missing.append("cash_input_document_and_explicit_scenario_not_supplied")
        if run.selected is None:
            missing.append("selected_DA_not_available_in_verified_run")
        run.finish()
        return dict(status="unavailable", rows=[], totals={f: None for f in _TOTALS}, missing=missing,
            audit=dict(run.binding(), archive_integrity_verified=True, full_official_settlement_verified=False,
                       missing_contract_assumed_zero=False, historical_fallback_used=False))
    p = _provenance(provenance, run)
    doc = read_json_document(document)
    kwargs = dict(expected_run_id=expected_run_id, expected_selected_dispatch=expected_selected_dispatch, asof=asof)
    if doc.get("input_type") == "spot_only":
        energy = load_spot_only_inputs(doc, **kwargs).energy
        builder = build_spot_only_cash
    elif doc.get("input_type") == "contract_energy":
        energy = load_contract_energy_inputs(doc, **kwargs)
        builder = build_contract_energy_report
    else:
        raise RunCashInputError("explicit spot_only or contract_energy input required; no default scenario")
    _scope_extras(energy.scope, p)
    quantities = {q.key: q for q in energy.volumes}
    quantity_times = {_decode(SettlementKey, w["data"]["key"], "volume key"):
                      _time(w["available_at"], "volume available_at") for w in doc["payload"]["volumes"]}
    prices = {(q.key, q.selected_price.stage): q for q in energy.prices}
    price_times = {(_decode(SettlementKey,w["data"]["key"],"price key"),w["data"]["selected_price"]["stage"]):
                   _time(w["published_at"],"price published_at") for w in doc["payload"]["prices"]}
    qseen, pseen, audit_quantities, audit_prices = set(), set(), [], []
    for row in _list(p["quantities"], "quantity provenance"):
        _object(row, ("key", "day_ahead", "actual"), "quantity provenance row")
        key = _decode(SettlementKey, row["key"], "quantity key")
        _require(key in quantities and key not in qseen, "unexpected/duplicate quantity provenance")
        qseen.add(key)
        q = quantities[key]
        audit_quantities.append(dict(key=json_safe(key),
            day_ahead=_quantity_origin(run, key, q.day_ahead_mwh, row["day_ahead"], "DA", quantity_times[key]),
            actual=_quantity_origin(run, key, q.actual_mwh, row["actual"], "RT", quantity_times[key])))
    _require(qseen == set(quantities), "quantity provenance missing")
    for row in _list(p["prices"], "price provenance"):
        _require(type(row) is dict and "key" in row and "stage" in row, "price provenance key/stage missing")
        key = _decode(SettlementKey, row["key"], "price key")
        _require(type(row["stage"]) is str, "price stage must be text")
        identity = (key, row["stage"])
        _require(identity in prices and identity not in pseen, "unexpected/duplicate price provenance")
        pseen.add(identity)
        audit_prices.append(dict(key=json_safe(key), stage=row["stage"], **_price_origin(run, prices[identity], row, price_times[identity])))
    _require(pseen == set(prices), "price provenance missing")
    report = builder(doc, **kwargs)
    model_risks = [q[stage]["research_risks"] for q in audit_quantities for stage in ("day_ahead","actual")
                   if "research_risks" in q[stage]]
    report["audit"].update(run.binding(), run_cash_schema=SCHEMA, archive_integrity_verified=True,
        archive_execution_mode=run.manifest["config"]["execution_mode"],
        full_day_run_verified=run.view["capabilities"]["market_rt"] and run.manifest["full_day_market_rt"],
        provenance_source=p["source"], price_interval_policy=PRICE_INTERVAL_POLICY,
        quantity_sources=audit_quantities, price_sources=audit_prices,
        renewable_participation=p["renewable_participation"], model_execution_is_meter=False,
        input_hash_is_authentication=False, actual_meter_authenticity_verified=False,
        full_official_settlement_verified=False, historical_fallback_used=False,
        automatic_price_selection=False, automatic_price_aggregation=False,
        run_manifest_or_ledger_modified=False, **_risk_summary(model_risks))
    run.finish()
    return report


# Deliberately separate from build_run_cash: no automatic price aggregation.
@dataclass(frozen=True)
class PublishedQuarterHourPrice:
    start: datetime
    end: datetime
    selected_price: SelectedCashPrice
    published_at: datetime
    available_at: datetime
    publication_version: str
    publication_status: str
    publication_source: str


def average_verified_quarter_hour_prices(*, key: SettlementKey,
        quarter_prices: tuple[PublishedQuarterHourPrice, ...], asof,
        hourly_settlement_source, participation_mode, participation_source,
        approval_source):
    """Explicit four-point arithmetic mean of declared formal settlement prices.

    External verification labels remain caller declarations. This helper rejects
    research candidates and does not select a price or certify a legal version.
    RT formal publication must be no earlier than the next Shanghai delivery day;
    DA prices may be known before delivery. No cash/run identity is inferred.
    """
    _require(isinstance(key, SettlementKey), "typed hourly SettlementKey required")
    for label in ("resource", "resource_kind", "settlement_unit", "node", "direction", "period"):
        _text(getattr(key, label), label)
    _require(key.resource_kind in {"thermal", "renewable", "battery", "pumped"}, "unsupported settlement resource")
    _require(key.direction in {"export", "import"} and
             (key.resource_kind in {"battery", "pumped"} or key.direction == "export"), "invalid settlement direction")
    for label, source in (("hourly_settlement_source",hourly_settlement_source),
                          ("participation_source",participation_source),("approval_source",approval_source)):
        _text(source, label)
    _require(participation_mode == MODE, "unsupported or unknown participation mode")
    start, end, cutoff = _time(key.start,"hourly start"), _time(key.end,"hourly end"), _time(asof,"asof")
    local = start.astimezone(ZoneInfo("Asia/Shanghai"))
    _require(end-start == timedelta(hours=1) and not (local.minute or local.second or local.microsecond),
             "target must be one Shanghai-aligned settlement hour")
    _require(isinstance(quarter_prices, tuple) and len(quarter_prices)==4 and
             all(isinstance(p,PublishedQuarterHourPrice) for p in quarter_prices), "exactly four typed quarter-hour prices required")
    stages, versions, sources, periods, values = set(), set(), set(), set(), []
    for i, slot in enumerate(quarter_prices):
        _require(_time(slot.start,"quarter start") == start+timedelta(minutes=15*i) and
                 _time(slot.end,"quarter end") == start+timedelta(minutes=15*(i+1)),
                 "four quarter-hour prices must be ordered, adjacent, and cover the exact hour")
        _require(isinstance(slot.selected_price,SelectedCashPrice), "typed selected publication price required")
        price = slot.selected_price.validate()
        _require(price.node == key.node and price.price_basis == "external_settlement_price" and
                 price.value.kind == "external_verified", "only explicit external settlement prices, never research candidates")
        _require(slot.publication_status == "declared_verified_settlement_publication", "unverified price publication cannot be averaged")
        _text(slot.publication_version,"publication_version"); _text(slot.publication_source,"publication_source")
        published, available = _time(slot.published_at,"price published_at"), _time(slot.available_at,"price available_at")
        _require(published <= available <= cutoff, "price publication <= availability <= asof required")
        if price.stage == "RT":
            next_day = local.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
            _require(published >= next_day, "RT formal settlement publication must be D+1 or later, not T-15 clearing candidate")
        _require(price.period not in periods, "duplicate quarter-hour price period")
        periods.add(price.period); stages.add(price.stage); versions.add(slot.publication_version); sources.add(slot.publication_source)
        values.append(_number(price.value.value,"quarter-hour price"))
    _require(len(stages)==len(versions)==len(sources)==1, "quarter-hour prices must share stage, publication version, and source")
    # Scale first: the average of finite values need not overflow when their sum would.
    average = _number(fsum(v/4 for v in values), "hourly arithmetic mean")
    stage = next(iter(stages)); version = next(iter(versions)); source = next(iter(sources))
    selected = SelectedCashPrice(stage,key.node,key.period,
        EvidenceValue(average,"external_verified",source+"; declared formal version "+version+"; explicit four-quarter arithmetic mean"),
        "external_settlement_price",approval_source)
    price = EnergyPriceInput(key,selected,hourly_settlement_source+"; explicit complete four-quarter arithmetic mean")
    return dict(status="external_price_mapping",price_input=json_safe(price),audit=dict(
        formula="(P_00 + P_15 + P_30 + P_45) / 4", energy_weighted=False,
        publication_version=version, publication_source=source, source_is_caller_declaration=True,
        publication_authenticity_verified=False, current_rule_applicability_certified=False,
        source_sections=["2026 Guangdong settlement 5.1/5.3.3", "2026-10 regional spot 3.4.4/3.6.1.2/3.6.2"],
        participation_mode=participation_mode,participation_source=participation_source,
        hourly_settlement_source=hourly_settlement_source, quarter_prices=json_safe(quarter_prices),
        automatic_price_selection=False, computed_by_this_run_verified=False,
        full_official_settlement_verified=False, price_unit="yuan/MWh"))
