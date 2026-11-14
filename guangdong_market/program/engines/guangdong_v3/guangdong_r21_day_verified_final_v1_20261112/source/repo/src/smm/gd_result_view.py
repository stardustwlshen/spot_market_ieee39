"""Read-only, solver-free views of the frozen provincial research evidence.

This is deliberately not a market engine or a generic result-directory browser.
Only the named complete RT v2 and its exact selected DA are accepted. Values are
never padded with zero; incompatible or incomplete evidence fails closed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DA_RUN = "ieee39_da96_run_v1"
RT_RUN = "ieee39_rt96_reference_v2"
DA_STAGE = "DA_SCED_fixed_state_continuous_polish"
RT_STAGE = "RT_SCED_fixed_state_continuous_polish"
COST_KEYS = (
    "thermal_bid_cost", "startup_cost", "shutdown_cost",
    "renewable_curtailment_cost", "renewable_bid_cost", "battery_bid_cost",
    "pumped_bid_cost", "network_penalty_cost", "balance_penalty_cost",
    "reserve_penalty_cost", "anchor_penalty_cost", "reservoir_band_cost",
)
ENERGY_KEYS = (
    "load_mw", "renewable_mw", "curtailment_mw", "tie_mw", "thermal_mw",
    "storage_net_mw", "balance_shortfall_mw", "balance_surplus_mw",
)
SYSTEM_KEYS = ENERGY_KEYS + (
    "balance_residual_mw", "max_line_slack_mw", "max_nodal_balance_residual_mw",
)
RESERVE_KEYS = (
    "primary_requirement_mw", "primary_provided_mw", "primary_shortfall_mw",
    "emergency_requirement_mw", "emergency_provided_mw", "emergency_shortfall_mw",
    "thermal_emergency_available_mw", "pumped_emergency_available_mw",
)
READINESS = (
    {"feature": "日前所选解", "status": "研究证据可用", "detail": "固定整数连续精修；精确SHA256匹配"},
    {"feature": "实时96首段", "status": "研究证据可用", "detail": "v2从头连续执行；未拼接部分v1"},
    {"feature": "正式节点电价（LMP）", "status": "不可用", "detail": "完整官方整数定价与乘子映射尚未验收"},
    {"feature": "结算现金与利润", "status": "不可用", "detail": "报价与优化罚不是经营成本或结算现金"},
    {"feature": "真实AGC里程、性能与价格", "status": "不可用", "detail": "只有合成容量边界；没有实测履约/结算证据"},
    {"feature": "理想调度对比", "status": "不可用", "detail": "待独立基准结果及可比性检查，不以0或旧应用结果替代"},
)


class EvidenceError(ValueError):
    """A safe, relative-path error suitable for presentation to the user."""


@dataclass(frozen=True)
class ResearchResult:
    da_rows: tuple[dict[str, Any], ...]
    rt_rows: tuple[dict[str, Any], ...]
    resource_rows: tuple[dict[str, Any], ...]
    solver_rows: tuple[dict[str, Any], ...]
    anchors: tuple[dict[str, Any], ...]
    provenance: tuple[dict[str, str], ...]
    summary: dict[str, Any]
    da_costs: dict[str, float]
    rt_costs: dict[str, float]
    terminal_thermal: dict[str, Any]
    readiness: tuple[dict[str, str], ...]


def default_evidence_root() -> Path:
    return Path(__file__).resolve().parents[3] / "evidence"


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def _number(value: Any, label: str) -> float:
    _need(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label}: 需要数值")
    _need(math.isfinite(value), f"{label}: 非有限数值")
    return float(value)


def _close(a: Any, b: Any, label: str, atol: float = 1e-6) -> None:
    _need(abs(_number(a, label) - _number(b, label)) <= atol, f"{label}: 数据不一致")


def _finite_tree(value: Any, label: str) -> None:
    if isinstance(value, float):
        _need(math.isfinite(value), f"{label}: 非有限数值")
    elif isinstance(value, dict):
        for item in value.values():
            _finite_tree(item, label)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item, label)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, "JSON含重复字段")
        result[key] = value
    return result


class _Reader:
    def __init__(self, root: Path):
        self.root = root
        self.provenance: list[dict[str, str]] = []
        self._cache: dict[str, bytes] = {}

    def read(self, relative: str) -> bytes:
        if relative in self._cache:
            return self._cache[relative]
        try:
            data = (self.root / relative).read_bytes()
        except OSError:
            raise EvidenceError(f"缺失或不能读取证据：{relative}") from None
        self.provenance.append({"file": relative, "sha256": hashlib.sha256(data).hexdigest()})
        self._cache[relative] = data
        return data

    def json(self, relative: str) -> Any:
        data = self.read(relative)
        try:
            result = json.loads(data, object_pairs_hook=_unique_pairs)
            _finite_tree(result, relative)
            return result
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise EvidenceError(f"JSON证据损坏：{relative}") from None

    def csv(self, relative: str) -> list[dict[str, str]]:
        try:
            reader = csv.DictReader(io.StringIO(self.read(relative).decode("utf-8-sig")))
            fields = reader.fieldnames
            _need(bool(fields) and len(fields) == len(set(fields)), f"CSV字段缺失或重复：{relative}")
            rows = list(reader)
            _need(bool(rows), f"CSV没有数据：{relative}")
            _need(all(None not in row and None not in row.values() for row in rows), f"CSV行损坏：{relative}")
            return rows
        except (UnicodeError, csv.Error):
            raise EvidenceError(f"CSV证据损坏：{relative}") from None


def read_variables(reader: _Reader, relative: str, expected_sha256: str | None) -> dict[str, float]:
    rows = reader.csv(relative)
    if expected_sha256 is not None:
        _need(reader.provenance[-1]["sha256"] == expected_sha256, "所选DA变量SHA256不匹配，拒绝替代解")
    result: dict[str, float] = {}
    for row in rows:
        _need(set(row) == {"name", "value", "fixed"}, "DA变量CSV字段不完整")
        name = row["name"]
        _need(name and name not in result, "DA变量名缺失或重复")
        _need(row["fixed"] in {"True", "False"}, "DA变量fixed标志无效")
        try:
            value = float(row["value"])
        except (ValueError, TypeError):
            raise EvidenceError(f"DA变量数值无效：{name}") from None
        result[name] = _number(value, "DA变量")
    return result


def _variable(values: dict[str, float], family: str, resource: Any, period: int) -> float:
    # Pyomo quotes numeric-looking string IDs (bus '1'), but not C1 / ES1.
    key = f"{family}[{resource!r},{period}]" if str(resource).isdigit() else f"{family}[{resource},{period}]"
    _need(key in values, f"所选DA缺少变量：{key}")
    return values[key]


def _stage(metric: dict[str, Any], expected: str, period: int | None) -> dict[str, Any]:
    _need(metric["stage"] == expected, "求解阶段身份不匹配")
    _need(metric["backend"] in {"copt", "highs"} and metric["termination"] == "optimal", "求解阶段证据未验收")
    _need(metric["requested_gap_met"] is True, "原求解gap未达标")
    request = _number(metric["requested_mip_rel_gap"], "请求gap")
    achieved = _number(metric["canonical_relative_gap"], "实际gap")
    _need(request == .001 and 0 <= achieved <= request, "请求或实得gap不符合0.1%边界")
    objective = _number(metric["objective_yuan"], "求解报价目标")
    bound = _number(metric["best_bound_yuan"], "求解界")
    absolute_gap = abs(objective - bound)
    _close(absolute_gap, metric["absolute_gap_yuan"], "原始求解绝对gap", 1e-6)
    _close(absolute_gap / max(1., abs(objective)), achieved, "原始目标/界与canonical gap", 1e-12)
    return {"global_period": period, "stage": expected, "backend": metric["backend"], "requested_gap": request,
            "achieved_gap": achieved, "time_s": _number(metric["time_s"], "求解秒数"),
            "objective_yuan": _number(metric["objective_yuan"], "窗口报价目标"),
            "exact_global_optimality_proved": metric["exact_global_optimality_proved"]}


def _timestamp(value: Any) -> datetime:
    try:
        time = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise EvidenceError("执行时间格式无效") from None
    _need(time.utcoffset() == timedelta(hours=8), "执行时间必须带+08:00时区")
    return time


def read_execution_rows(reader: _Reader, relative: str, intervals: dict[str, Any]) -> list[dict[str, Any]]:
    raw = reader.csv(relative)
    _need(len(raw) == 96, "完整RT96需要96行，不接受部分v1或补齐数据")
    fields = {"global_period", "start", "end", *SYSTEM_KEYS, *COST_KEYS,
              "quoted_objective_cost_yuan", *RESERVE_KEYS}
    rows = []
    for period, source in enumerate(raw):
        _need(set(source) == fields, "RT执行CSV字段缺失或不受支持")
        _need(source["global_period"] == str(period), "RT期号重复、缺失或非连续")
        row: dict[str, Any] = {"global_period": period, "start": source["start"], "end": source["end"]}
        _need([row["start"], row["end"]] == intervals[str(period)], "RT时间轴与所选DA不一致")
        start, end = _timestamp(row["start"]), _timestamp(row["end"])
        _need(end - start == timedelta(minutes=15), "执行段必须是15分钟")
        if period:
            _need(row["start"] == rows[-1]["end"], "RT执行区间有间断或重叠")
        for field in fields - {"global_period", "start", "end"}:
            try:
                row[field] = _number(float(source[field]), f"RT {field}")
            except (TypeError, ValueError) as exc:
                if isinstance(exc, EvidenceError):
                    raise
                raise EvidenceError(f"RT {field}: 数值无效") from None
        row["duration_h"] = (end - start).total_seconds() / 3600
        residual = (row["thermal_mw"] + row["renewable_mw"] + row["tie_mw"] + row["storage_net_mw"]
                    + row["balance_shortfall_mw"] - row["balance_surplus_mw"] - row["load_mw"])
        _close(residual, row["balance_residual_mw"], "RT系统功率守恒")
        _close(residual, 0.0, "RT系统平衡")
        _close(sum(row[key] for key in COST_KEYS), row["quoted_objective_cost_yuan"], "RT首段费用分项")
        for prefix in ("primary", "emergency"):
            _need(row[f"{prefix}_shortfall_mw"] >= -1e-7, "备用缺额不能为负")
            _need(row[f"{prefix}_provided_mw"] + row[f"{prefix}_shortfall_mw"] + 1e-6 >= row[f"{prefix}_requirement_mw"], "备用提供与缺额不满足需求")
        rows.append(row)
    _need(_timestamp(rows[0]["start"]).hour == 0 and _timestamp(rows[0]["start"]).minute == 0, "RT96必须从午夜开始")
    return rows


def _da_rows(values: dict[str, float], frozen: dict[str, Any], reference: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    buses = [str(bus) for bus in frozen["network"]["buses"]]
    thermal, batteries, pumped = (reference[key] for key in ("thermal_ids", "battery_ids", "pumped_unit_ids"))
    rows, resources = [], []
    profiles = frozen["profiles"]
    _need(len(profiles["load_da"]) == len(profiles["TIE_sched"]) == 96, "DA输入曲线必须完整96段")
    for period in range(96):
        row = {"global_period": period, "start": reference["intervals"][str(period)][0],
               "end": reference["intervals"][str(period)][1], "duration_h": .25,
               "load_mw": _number(profiles["load_da"][period], "DA负荷"),
               "tie_mw": _number(profiles["TIE_sched"][period], "DA外生受电")}
        for column, family in (("renewable_mw", "renewable_power"), ("curtailment_mw", "renewable_curtailment"),
                               ("balance_shortfall_mw", "balance_shortfall"), ("balance_surplus_mw", "balance_surplus")):
            row[column] = sum(_variable(values, family, bus, period) for bus in buses)
        row["thermal_mw"] = sum(_variable(values, "power", unit, period) for unit in thermal)
        row["storage_net_mw"] = sum(_variable(values, "gd_storage.discharge", unit, period) - _variable(values, "gd_storage.charge", unit, period) for unit in batteries)
        row["storage_net_mw"] += sum(_variable(values, "gd_storage.generate", unit, period) - _variable(values, "gd_storage.pump", unit, period) for unit in pumped)
        for prefix in ("primary", "emergency"):
            name = f"gd_reserve.{prefix}_short[{period}]"
            _need(name in values, f"DA缺少变量：{name}")
            row[f"{prefix}_shortfall_mw"] = values[name]
        residual = row["thermal_mw"] + row["renewable_mw"] + row["tie_mw"] + row["storage_net_mw"] + row["balance_shortfall_mw"] - row["balance_surplus_mw"] - row["load_mw"]
        _close(residual, 0.0, "所选DA系统功率守恒")
        rows.append(row)
        for unit in thermal:
            resources.append({"stage": "DA", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "thermal", "power_mw": _variable(values, "power", unit, period), "online": _variable(values, "online", unit, period)})
        for unit in batteries:
            resources.append({"stage": "DA", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "battery", "power_mw": _variable(values, "gd_storage.discharge", unit, period) - _variable(values, "gd_storage.charge", unit, period), "energy_mwh": _variable(values, "gd_storage.energy", unit, period)})
        for unit in pumped:
            resources.append({"stage": "DA", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "pumped", "power_mw": _variable(values, "gd_storage.generate", unit, period) - _variable(values, "gd_storage.pump", unit, period)})
    return rows, resources


def _load(root: Path) -> ResearchResult:
    reader = _Reader(root)
    completion = reader.json(f"{RT_RUN}/completion.json")
    summary = reader.json(f"{RT_RUN}/validation_summary.json")
    _need(completion["completed"] is True and completion["full_RT96_completed"] is True and completion["completed_execution_intervals"] == 96, "RT v2未完整完成，不回退或拼接部分v1")
    _need(summary["full_RT96_completed"] is True and summary["completed_execution_intervals"] == 96, "RT汇总未确认完整96段")
    for key in ("source_unchanged", "input_unchanged"):
        _need(completion[key] is True and summary[key] is True, "RT源或输入冻结校验未通过")
    _need(summary["constraint_violations_gt_1e_6"] == 0, "RT存在尚未验收的活动约束违例")
    _need(completion["offline_future_truth_conditioned"] is True and completion["causal_online_forecast"] is False, "RT研究信息边界不匹配")
    source = reader.json(f"{RT_RUN}/source_inputs.json")
    reference = source["da_reference"]
    _need(reference["stage"] == DA_STAGE, "RT没有引用固定整数连续精修DA")
    _need(source["offline_forecast"]["uses_future_actual"] is True, "RT未来条件信息披露缺失")
    _need(source["assumptions"]["no_actual_AGC_delivery_evidence"] is True, "AGC证据范围改变，需专用适配")
    _need(source["assumptions"]["window_periods"] == 16 and source["assumptions"]["tail_policy"] == "truncate_research", "滚动窗口政策不受支持")
    for key in ("formal_lmp", "settlement_cash", "AGC_actual_mileage", "AGC_settlement_performance", "AGC_uniform_price"):
        _need(key in summary and summary[key] is None, "官方市场结果需独立验收适配，不从当前研究文件启用")
    da_completion = reader.json(f"{DA_RUN}/completion.json")
    _need(da_completion["completed"] is True, "DA证据未完成")
    da_source = reader.json(f"{DA_RUN}/source_inputs.json")
    frozen = reader.json(f"{DA_RUN}/frozen_source_inputs.json")
    values = read_variables(reader, f"{DA_RUN}/{DA_STAGE}/variables.csv", reference["source_sha256"])
    da, resources = _da_rows(values, frozen, reference)
    rt = read_execution_rows(reader, f"{RT_RUN}/executed_first_intervals.csv", reference["intervals"])
    solver_rows = [_stage(reader.json(f"{DA_RUN}/{stage}/metrics.json"), stage, None) for stage in ("DA_SCUC", "DA_SCED", DA_STAGE)]
    _need(len({row["backend"] for row in solver_rows}) == 1, "DA求解阶段backend不一致")
    market_backend = solver_rows[0]["backend"]
    da_physical = reader.json(f"{DA_RUN}/{DA_STAGE}/physical_summary.json")
    _need(da_physical["not_settlement_cash"] is True, "DA成本口径缺少非结算标识")
    da_costs = {key: _number(da_physical[key], key) for key in COST_KEYS}
    _close(sum(da_costs.values()), da_physical["objective_yuan"], "DA费用分项")
    rt_costs = {key: sum(row[key] for row in rt) for key in COST_KEYS}
    anchors: list[dict[str, Any]] = []
    terminal = {}
    previous_throughput: dict[str, float] = {}
    for period, row in enumerate(rt):
        folder = f"{RT_RUN}/windows/{period:03d}"
        selected = reader.json(f"{folder}/selected_first_interval.json")
        outcome = reader.json(f"{folder}/outcome.json")
        _need(selected["overlapping_window_objective_not_daily_cost"] is True, "首段费用缺少重叠窗口标识")
        _need(selected["state"] == outcome["first_boundary"], "所选首段与outcome边界不一致")
        for key in SYSTEM_KEYS:
            _close(row[key], selected["physical"]["system"][key], f"逐窗首段 {period} {key}")
        for key in (*COST_KEYS, "quoted_objective_cost_yuan"):
            _close(row[key], selected["costs"][key], f"逐窗首段费用 {period} {key}")
        for key in RESERVE_KEYS:
            _close(row[key], outcome["reserve_diagnostics"][key], f"逐窗备用 {period} {key}")
        _need(outcome["backend"] == market_backend, "RT与所选DA的backend不一致")
        metrics = outcome["stage_metrics"]
        _need(all(metric.get("backend") == market_backend for metric in metrics), "RT求解阶段backend不一致")
        _need(len(metrics) == 2, "每窗需要原MIP及所选连续精修两阶段证据")
        solver_rows.extend(_stage(metric, stage, period) for metric, stage in zip(metrics, ("RT_SCED", RT_STAGE)))
        payload, state = outcome["first_interval_payload"], selected["state"]
        _close(sum(payload["thermal_power_mw"].values()), row["thermal_mw"], "逐机热机与系统汇总")
        net = sum(payload["battery_discharge_mw"].values()) - sum(payload["battery_charge_mw"].values()) + sum(payload["pumped_generation_mw"].values()) - sum(payload["pumped_pumping_mw"].values())
        _close(net, row["storage_net_mw"], "逐机储能与系统汇总")
        for unit in reference["thermal_ids"]:
            _close(payload["thermal_online"][unit], _variable(values, "online", unit, period), "RT热机组合与所选DA")
            _close(payload["thermal_power_mw"][unit], state["thermal"][unit]["power_mw"], "热机首段状态")
            resources.append({"stage": "RT", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "thermal", "power_mw": payload["thermal_power_mw"][unit], "online": payload["thermal_online"][unit]})
        for unit in reference["battery_ids"]:
            battery = state["storage"]["batteries"][unit]
            energy = _number(battery["initial_energy_mwh"], "RT电池库存")
            throughput = _number(battery["cycle_throughput_used_mwh"], "RT累计吞吐")
            if unit in previous_throughput:
                _need(throughput + 1e-7 >= previous_throughput[unit], "电池跨窗累计吞吐下降")
            previous_throughput[unit] = throughput
            resources.append({"stage": "RT", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "battery", "power_mw": payload["battery_discharge_mw"][unit] - payload["battery_charge_mw"][unit], "energy_mwh": energy})
            if period % 4 == 3:
                target = _variable(values, "gd_storage.energy", unit, period)
                _close(energy, target, "RT整点电池DA硬锚点")
                anchors.append({"global_period": period, "unit": unit, "target_mwh": target, "actual_mwh": energy, "error_mwh": energy - target})
        for unit in reference["pumped_unit_ids"]:
            resources.append({"stage": "RT", "global_period": period, "start": row["start"], "end": row["end"], "resource": unit, "kind": "pumped", "power_mw": payload["pumped_generation_mw"][unit] - payload["pumped_pumping_mw"][unit]})
        terminal = state["thermal"]
    shortage_ranges = []
    for row in rt:
        if row["emergency_shortfall_mw"] > 1e-6:
            if shortage_ranges and shortage_ranges[-1][1] == row["start"]:
                shortage_ranges[-1][1] = row["end"]
            else:
                shortage_ranges.append([row["start"], row["end"]])
    energy = {key: sum(row[key] * row["duration_h"] for row in rt) for key in ENERGY_KEYS}
    for key, value in energy.items():
        _close(value, summary["executed_energy_mwh"][key], f"RT全日能量 {key}")
    for key, value in rt_costs.items():
        _close(value, summary["executed_cost_components"][key], f"RT全日费用 {key}")
    quoted_cost = sum(row["quoted_objective_cost_yuan"] for row in rt)
    _close(quoted_cost, sum(rt_costs.values()), "RT全日费用加总")
    _close(quoted_cost, completion["executed_quoted_cost_yuan"], "RT全日completion费用")
    _close(quoted_cost, summary["executed_quoted_cost_yuan"], "RT全日summary费用")
    deficit = sum(row["emergency_shortfall_mw"] * row["duration_h"] for row in rt)
    periods = [row["global_period"] for row in rt if row["emergency_shortfall_mw"] > 1e-6]
    # The historical field is misnamed mwh: this is capacity × time, not energy.
    _close(deficit, summary["emergency_shortfall_mwh"], "备用缺额容量时间积")
    _need(periods == summary["emergency_shortfall_global_periods"] and len(periods) == summary["emergency_shortfall_periods"], "备用短缺时段汇总不一致")
    max_gap = max(row["achieved_gap"] for row in solver_rows if row["stage"] == "RT_SCED")
    _close(max_gap, summary["max_original_gap"], "RT原MIP实际最大gap", 1e-12)
    _close(max(row["max_line_slack_mw"] for row in rt), summary["max_line_slack_mw"], "线路松弛汇总")
    _need(len(anchors) == summary["battery_hour_anchor_observations"], "电池锚点数量不一致")
    _need(rt[0]["start"] == summary["first_start"] and rt[-1]["end"] == summary["last_end"], "全日边界不一致")
    return ResearchResult(tuple(da), tuple(rt), tuple(resources), tuple(solver_rows), tuple(anchors), tuple(reader.provenance),
                          {"rt_energy_mwh": energy, "da_energy_mwh": {key: sum(row[key] * .25 for row in da) for key in ENERGY_KEYS},
                           "reserve_deficit_mw_h": deficit, "reserve_shortfall_periods": len(periods), "reserve_shortage_ranges": shortage_ranges,
                           "max_reserve_shortfall_mw": max(row["emergency_shortfall_mw"] for row in rt),
                           "rt_quoted_cost_yuan": quoted_cost, "da_quoted_cost_yuan": sum(da_costs.values()),
                           "max_original_gap": max_gap, "max_polish_gap": max(row["achieved_gap"] for row in solver_rows if row["stage"] == RT_STAGE),
                           "max_line_slack_mw": summary["max_line_slack_mw"], "anchor_count": len(anchors),
                           "max_anchor_error_mwh": max(abs(row["error_mwh"]) for row in anchors),
                           "selected_da_sha256": reference["source_sha256"], "snapshot_sha256_recorded": da_source["snapshot_sha256"],
                           "synthetic_c1_agc_mw": source["assumptions"]["c1_agc_mw"], "truncated_tail_windows": len(summary["truncated_tail_windows"]),
                           "full_official_market_completed": False, "settlement_cash": None, "formal_lmp": None},
                          da_costs, rt_costs, terminal, tuple(dict(row) for row in READINESS))


def load_research_results(evidence_root: str | Path | None = None) -> ResearchResult:
    """Load exact frozen evidence or raise EvidenceError, without solver imports.

    Paths inside historical source metadata are never followed. This permits a
    relocated delivery bundle and avoids silently reading stale originals.
    """
    try:
        return _load(Path(evidence_root) if evidence_root is not None else default_evidence_root())
    except EvidenceError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        raise EvidenceError(f"证据字段缺失或结构不兼容（{type(exc).__name__}）；未填零或回退旧结果") from None


HINDSIGHT_RUN = "ieee39_hindsight96_reference_v1"


@dataclass(frozen=True)
class HindsightView:
    available: bool
    reason: str
    rows: tuple[dict[str, Any], ...] = ()
    report: dict[str, Any] | None = None
    provenance: tuple[dict[str, str], ...] = ()


HINDSIGHT_SEAL_FILES = tuple(sorted([
    *(f"{stage}/{name}" for stage in ("DA_SCUC", "DA_SCED", DA_STAGE) for name in ("metrics.json", "variables.csv", "physical_summary.json")),
    "completion.json", "comparison.json", "source_inputs.json", "dispatch_by_period.csv",
    "resource_dispatch.csv", "terminal_state.json", "hindsight_audit.json", "workflow_audit.json",
    "source_hash_before.json", "source_hash_after.json", "comparison_source_validation.json", "stage_events.json",
    "unchanged_da_template_without_solve/source_inputs.json",
    "unchanged_da_template_without_solve/frozen_source_inputs.json",
]))


def _verify_hindsight_seal(reader):
    seal = reader.json(f"{HINDSIGHT_RUN}/result_integrity_manifest.json")
    _need(type(seal["schema_version"]) is int and seal["schema_version"] == 1 and seal["kind"] == "hindsight_result_integrity_seal", "理想结果完整性封存版本不支持")
    _need(seal["seal_context"] in {"post_export", "independent_readback_after_original_run"}, "理想结果封存时点类型无效")
    _need(isinstance(seal["verification_reference"], str) and bool(seal["verification_reference"].strip()), "理想结果封存缺少核验来源")
    try:
        stamped = datetime.fromisoformat(seal["created_at_utc"])
    except (TypeError, ValueError):
        raise EvidenceError("理想结果封存时间无效") from None
    _need(stamped.utcoffset() == timedelta(0), "理想结果封存时间必须为UTC")
    _need(seal["signature_verified"] is False and seal["model_constraints_revalidated_by_seal"] is False, "完整性封存不能冒充签名或重新验算模型")
    _need(set(seal["files"]) == set(HINDSIGHT_SEAL_FILES), "理想结果封存必需文件清单缺失或变化")
    for name in HINDSIGHT_SEAL_FILES:
        digest = hashlib.sha256(reader.read(f"{HINDSIGHT_RUN}/{name}")).hexdigest()
        _need(digest == seal["files"][name], f"理想结果文件与封存hash不一致：{name}")
    return {"created_at_utc": seal["created_at_utc"], "seal_context": seal["seal_context"],
            "required_file_count": len(HINDSIGHT_SEAL_FILES), "signature_verified": False}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _hindsight_evidence_chain(reader, completion, source, report, market):
    """Cross-check immutable inputs, raw own-stage solutions, costs and boundaries.

    This is independent readback arithmetic, not a new solve or a full model
    reconstruction. It must not certify arbitrary summary-only result folders.
    """
    base = HINDSIGHT_RUN
    stages = ("DA_SCUC", "DA_SCED", DA_STAGE)
    for flag in ("source_unchanged", "input_unchanged", "native_solver_requested"):
        _need(completion[flag] is True, "理想调度源、输入或原生运行状态未通过")
    _need(completion["error"] is None, "理想调度运行记录有错误")
    before = reader.json(f"{base}/source_hash_before.json")
    after = reader.json(f"{base}/source_hash_after.json")
    audit = reader.json(f"{base}/hindsight_audit.json")
    workflow = reader.json(f"{base}/workflow_audit.json")
    backend = audit.get("solver")
    _need(backend in {"copt", "highs"} and workflow.get("solver") == backend, "理想调度求解器身份不一致")
    required_sources = {"gd_hindsight.py", "gd_reference_workflow.py", "gd_energy_core.py", "gd_storage_core.py", "gd_dispatch_reserves.py", "gd_piecewise_bids.py", "gd_ieee39_inputs.py", backend + "_solver.py", "run_ieee39_hindsight_reference.py"}
    _need(before == after and required_sources <= {Path(name).name for name in before}, "理想调度源文件前后hash链缺失或变化")
    _need(all(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value) for value in before.values()), "理想调度源hash格式错误")
    template = reader.json(f"{base}/unchanged_da_template_without_solve/source_inputs.json")
    frozen = reader.json(f"{base}/unchanged_da_template_without_solve/frozen_source_inputs.json")
    _need(_canonical_hash(frozen) == template["snapshot_sha256"] == source["source_snapshot_sha256"] == market.summary["snapshot_sha256_recorded"], "理想调度冻结快照hash不匹配")
    _need(source["source_files"] == template["source_files"], "理想调度原始输入hash清单与冻结模板不一致")
    audit = reader.json(f"{base}/hindsight_audit.json")
    workflow = reader.json(f"{base}/workflow_audit.json")
    _need(audit["solver"] == workflow["solver"] == backend and audit["threads"] == workflow["threads"] == 1, "理想调度求解器审计不一致")
    _need(audit["requested_mip_rel_gap"] == workflow["requested_mip_rel_gap"] == .001, "理想调度审计gap边界不一致")
    for flag in ("full_day_actual_used_in_SCUC_and_SCED", "own_storage_trajectories", "original_network_resource_quote_initial_and_terminal_inputs_preserved", "synthetic_actual"):
        _need(audit[flag] is True, "理想调度实际信息/自身路径审计未通过")
    for flag in ("forecast_fallback_used", "market_DA_storage_anchors_used", "cross_regional_market", "conventional_hydro"):
        _need(audit[flag] is False, "理想调度审计范围不一致")
    _need(workflow["state_source"]["thermal_commitment"] == "this_workflow_DA_SCUC", "理想调度组合并非自身SCUC")
    _need(workflow["horizon"]["periods"] == 96 and workflow["horizon"]["settlement_ids"] == list(range(96)), "理想调度审计时轴不完整")
    for stage in stages:
        _need(workflow["storage_by_stage"][stage]["anchor_periods"] == [], "理想调度含外部储能锚点")
    units, net, profiles = frozen["config"]["units"], frozen["network"], frozen["profiles"]
    buses = [str(b) for b in net["buses"]]
    actual_keys = ["load_actual", *(u["id"] + "_actual" for u in units["renewable"])]
    _need(all(isinstance(profiles[key], list) and len(profiles[key]) == 96 for key in actual_keys), "理想调度缺少完整冻结actual曲线")
    tie = {str((b, t)): 0.0 for b in buses for t in range(96)}
    for unit in units["tie"]:
        for t in range(96):
            tie[str((str(unit["bus"]), t))] += profiles[unit["id"] + "_sched"][t]
    actual = {"load_mw": {str((b, t)): net["load_share"][b] * profiles["load_actual"][t] for b in buses for t in range(96)},
              "renewable_available_mw": {u["id"]: {str(t): profiles[u["id"] + "_actual"][t] for t in range(96)} for u in units["renewable"]},
              "physical_tie_mw": tie, "provenance": source["actual_provenance"], "fixed_boundaries": source["fixed_boundary_provenance"]}
    _need(audit["actual_provenance"] == source["actual_provenance"] and audit["fixed_boundary_provenance"] == source["fixed_boundary_provenance"], "理想调度实际输入来源不一致")
    _need(_canonical_hash(actual) == audit["actual_input_sha256"], "理想调度actual输入hash不匹配")
    h = report["hindsight"]
    _need(type(h["constraint_violations_gt_1e_6"]) is int and h["constraint_violations_gt_1e_6"] == 0, "理想调度存在活动约束违例")
    _need(0 <= _number(h["max_active_constraint_residual"], "理想调度残差") <= 1e-6, "理想调度活动约束残差超界")
    solutions = {}
    for stage in stages:
        metric = reader.json(f"{base}/{stage}/metrics.json")
        _stage(metric, stage, None)
        _need(metric["backend"] == backend, "理想调度阶段backend与审计不一致")
        _need(metric == h["solver_stages"][stage] == completion["stages"][stage], "理想调度阶段指标副本不一致")
        values = read_variables(reader, f"{base}/{stage}/variables.csv", None)
        physical = reader.json(f"{base}/{stage}/physical_summary.json")
        _need(physical["active_constraint_violations_gt_1e_6"] == [] and physical["not_settlement_cash"] is True, "理想调度阶段物理验收未通过")
        _close(sum(_number(physical[key], key) for key in COST_KEYS), physical["objective_yuan"], "理想调度阶段费用分项")
        _close(metric["objective_yuan"], physical["objective_yuan"], "理想调度阶段费用与求解目标")
        for unit in units["thermal"]:
            for t in range(96):
                _variable(values, "power", unit["id"], t)
                _variable(values, "online", unit["id"], t)
        if stage == DA_STAGE:
            _close(physical["objective_yuan"], h["quoted_objective_cost_yuan"], "理想比较费用与所选解目标")
            _need(set(h["cost_components_yuan"]) == set(COST_KEYS), "理想调度费用分项不完整")
            for key in COST_KEYS:
                _close(physical[key], h["cost_components_yuan"][key], "理想比较费用与所选解分项")
        solutions[stage] = values
    selected = solutions[DA_STAGE]
    _need(set(solutions["DA_SCED"]) == set(selected), "理想SCED/精修变量覆盖不一致")
    scuc_expected = {name for name in selected if not name.startswith("gd_reserve.primary_pumped[")}
    scuc_expected.update(f'gd_reserve.primary_pumped[{u["id"]},{t}]' for u in units["pumped"] for t in range(96))
    _need(set(solutions["DA_SCUC"]) == scuc_expected, "理想SCUC/SCED变量覆盖不一致")
    for stage, values in solutions.items():
        for t in range(96):
            for bus in buses:
                for family in ("renewable_power", "renewable_curtailment", "angle", "balance_shortfall", "balance_surplus"):
                    _variable(values, family, bus, t)
            for line in net["branches"]:
                for family in ("flow", "line_over", "line_under"):
                    _variable(values, family, line["branch_id"], t)
    original_sced_metric = h["solver_stages"]["DA_SCED"]
    for name in original_sced_metric["integer_refs"]:
        _need(name in selected and name in solutions["DA_SCED"], "精修整数引用缺少变量")
        _close(selected[name], solutions["DA_SCED"][name], "理想精修整数状态未保持")
    # All SCED thermal commitments come from this run's SCUC, not market DA.
    for unit in units["thermal"]:
        for t in range(96):
            for stage in ("DA_SCED", DA_STAGE):
                _close(_variable(solutions[stage], "online", unit["id"], t), _variable(solutions["DA_SCUC"], "online", unit["id"], t), "理想调度自身SCUC组合继承")
    terminal = reader.json(f"{base}/terminal_state.json")
    _need(terminal == h["terminal_state"] and terminal["through_period"] == 95, "理想调度终态与比较报告不一致")
    raw_resources = reader.csv(f"{base}/resource_dispatch.csv")
    resources = {}
    for row in raw_resources:
        key = (row["kind"], row["resource"], row["global_period"])
        _need(key not in resources, "理想调度逐机记录重复")
        resources[key] = row
    expected = {(kind, uid, str(t)) for kind, ids in (
        ("thermal", [u["id"] for u in units["thermal"]]), ("battery", [u["id"] for u in units["storage"]]),
        ("pumped_unit", [f'{u["id"]}_U{i}' for u in units["pumped"] for i in range(1, u["n_units"] + 1)]),
        ("pumped_plant", [u["id"] for u in units["pumped"]])) for uid in ids for t in range(96)}
    _need(set(resources) == expected, "理想调度逐机记录未完整覆盖96段")
    def check_resource(kind, uid, t, field, value):
        try:
            parsed = float(resources[kind, uid, str(t)][field])
        except (ValueError, TypeError):
            raise EvidenceError("理想调度逐机数值缺失") from None
        _close(parsed, value, "理想调度逐机结果与所选变量")
    for unit in units["thermal"]:
        uid = unit["id"]
        init = frozen["initial_state"]["thermal"].get(uid, unit["init"])
        online, age = int(init["online"]), float(init["hours"])
        for t in range(96):
            current = _variable(selected, "online", uid, t)
            _need(abs(current - round(current)) < 1e-6 and round(current) in (0, 1), "理想热机组合非二进制")
            current = round(current)
            age = age + .25 if current == online else .25
            online = current
            for field, family in (("power_mw", "power"), ("online", "online"), ("start", "start"), ("stop", "stop")):
                check_resource("thermal", uid, t, field, _variable(selected, family, uid, t))
        for field, value in (("online", online), ("power_mw", _variable(selected, "power", uid, 95)), ("state_age_h", age), ("remaining_min_state_h", max(0., unit["ut" if online else "dt"] - age))):
            _close(terminal["thermal"][uid][field], value, "理想热机终态义务")
    for unit in units["storage"]:
        uid = unit["id"]
        energy = unit["ecap"] * unit["soc0"]
        throughput = template["options"]["battery_policy"][uid]["cycle_throughput_used_mwh"]
        for t in range(96):
            charge, discharge = (_variable(selected, "gd_storage." + family, uid, t) for family in ("charge", "discharge"))
            energy += .25 * (charge * unit["eta_c"] - discharge / unit["eta_d"])
            throughput += .25 * (charge * unit["eta_c"] + discharge / unit["eta_d"])
            actual_energy = _variable(selected, "gd_storage.energy", uid, t)
            _close(energy, actual_energy, "理想电池库存递推")
            for field, value in (("power_mw", discharge - charge), ("charge_mw", charge), ("discharge_mw", discharge), ("energy_mwh", actual_energy)):
                check_resource("battery", uid, t, field, value)
        _close(energy, terminal["storage"]["batteries"][uid]["initial_energy_mwh"], "理想电池终态")
        _close(throughput, terminal["storage"]["batteries"][uid]["cycle_throughput_used_mwh"], "理想电池累计吞吐终态")
        _close(terminal["storage"]["batteries"][uid]["initial_hour_direction"], 0, "理想午夜电池方向边界")
        _close(energy, frozen["bids"]["units"][uid]["soc_end"] * unit["ecap"], "理想电池终态目标")
    for plant in units["pumped"]:
        pid = plant["id"]
        upper, lower = plant["vup0"] * 10000, (plant["v_total"] - plant["vup0"]) * 10000
        for t in range(96):
            total_generation = total_pump = 0.
            for i in range(1, plant["n_units"] + 1):
                uid = f"{pid}_U{i}"
                generation, pump = (_variable(selected, "gd_storage." + family, uid, t) for family in ("generate", "pump"))
                total_generation += generation
                total_pump += pump
                for field, value in (("power_mw", generation - pump), ("generation_mw", generation), ("pump_mw", pump)):
                    check_resource("pumped_unit", uid, t, field, value)
                mode = resources["pumped_unit", uid, str(t)]["mode"]
                _need(mode in ("off", "pump", "generate") and selected[f"gd_storage.mode[{uid},{t},{mode}]"] > .5, "理想抽蓄模式与所选变量不一致")
            water = .25 * (total_pump / plant["e_pump"] - total_generation / plant["e_gen"]) * 10000
            upper += water
            lower -= water
            for field, value in (("power_mw", total_generation - total_pump), ("upper_volume_m3", upper), ("lower_volume_m3", lower)):
                check_resource("pumped_plant", pid, t, field, value)
            _close(upper, _variable(selected, "gd_storage.upper_volume", pid, t), "理想抽蓄上库递推")
            _close(lower, _variable(selected, "gd_storage.lower_volume", pid, t), "理想抽蓄下库递推")
        for side, value in (("upper", upper), ("lower", lower)):
            _close(value, terminal["storage"]["pumped_plants"][pid][f"{side}_initial_m3"], "理想双库终态")
            bounds = template["options"]["pumped_plant_policy"][pid][f"{side}_final_bounds_m3"]
            _need(bounds[0] - 1e-6 <= value <= bounds[1] + 1e-6, "理想双库终态目标不满足")
    for plant in units["pumped"]:
        for i in range(1, plant["n_units"] + 1):
            uid = f'{plant["id"]}_U{i}'
            policy = template["options"]["pumped_unit_policy"][uid]
            initial = plant["init_mode"]
            mode = "off" if initial == "idle" else initial
            age, events = policy["initial_mode_elapsed_h"], 0
            for t in range(96):
                current = resources["pumped_unit", uid, str(t)]["mode"]
                if current == mode:
                    age += .25
                else:
                    _need({current, mode} != {"pump", "generate"}, "理想抽蓄不允许直接抽发切换")
                    events += int(current != "off") + int(mode != "off")
                    age = .25
                mode = current
            end = terminal["storage"]["pumped_units"][uid]
            _need(end["initial_mode"] == mode, "理想抽蓄终态模式")
            for field, value in (("initial_mode_elapsed_h", age), ("initial_generation_mw", _variable(selected, "gd_storage.generate", uid, 95)), ("executed_start_stop_events", events), ("remaining_start_stop_events", policy["remaining_start_stop_events"] - events), ("minimum_state_remaining_h", max(0., policy[f"min_{mode}_h"] - age))):
                _close(end[field], value, "理想抽蓄终态年龄/预算")
    # Independently evaluate quote curves from frozen inputs, not summary totals.
    def quote(power, segments):
        return sum(max(0., min(power, s["p_end"]) - s["p_start"]) * s["price"] for s in segments)
    bids = frozen["bids"]["units"]
    costs = {key: 0. for key in ("thermal_bid_cost", "startup_cost", "shutdown_cost", "renewable_bid_cost", "battery_bid_cost", "pumped_bid_cost")}
    for t in range(96):
        for unit in units["thermal"]:
            uid = unit["id"]
            costs["thermal_bid_cost"] += .25 * (quote(_variable(selected, "power", uid, t), bids[uid]["segments"]) + bids[uid]["noload_cost"] * _variable(selected, "online", uid, t))
            costs["startup_cost"] += bids[uid]["startup_cost_yuan"] * _variable(selected, "start", uid, t)
            costs["shutdown_cost"] += template["options"]["thermal_policy"][uid]["shutdown_yuan"][str(t)] * _variable(selected, "stop", uid, t)
        for unit in units["renewable"]:
            costs["renewable_bid_cost"] += .25 * quote(_variable(selected, "renewable_power", str(unit["bus"]), t), bids[unit["id"]]["segments"])
        for unit in units["storage"]:
            uid = unit["id"]
            costs["battery_bid_cost"] += .25 * (quote(_variable(selected, "gd_storage.discharge", uid, t), bids[uid]["dis_segments"]) - quote(_variable(selected, "gd_storage.charge", uid, t), bids[uid]["ch_segments"]))
        for plant in units["pumped"]:
            pid = plant["id"]
            totals = {family: sum(_variable(selected, "gd_storage." + family, f"{pid}_U{i}", t) for i in range(1, plant["n_units"] + 1)) for family in ("generate", "pump")}
            costs["pumped_bid_cost"] += .25 * (quote(totals["generate"], bids[pid]["gen_segments"]) - quote(totals["pump"], bids[pid]["pump_segments"]))
    policy = template["options"]["energy_policy"]
    penalties = policy["penalties_yuan_per_mwh"]
    costs["network_penalty_cost"] = sum(.25 * penalties["line"] * (_variable(selected, "line_over", line["branch_id"], t) + _variable(selected, "line_under", line["branch_id"], t)) for line in net["branches"] for t in range(96))
    # This frozen IEEE39 has no modeled sections. Unknown sections need a new adapter.
    _need(not any(name.startswith("section_over[") for name in selected), "理想调度新增断面需专用费用适配")
    costs["balance_penalty_cost"] = sum(.25 * (penalties["balance_shortfall"] * _variable(selected, "balance_shortfall", b, t) + penalties["balance_surplus"] * _variable(selected, "balance_surplus", b, t)) for b in buses for t in range(96))
    costs["renewable_curtailment_cost"] = sum(.25 * policy["renewable_curtailment_penalty_yuan_per_mwh"][str((b, t))] * _variable(selected, "renewable_curtailment", b, t) for b in buses for t in range(96))
    # Current DA reserve penalties are zero only because all exported shortage
    # variables are zero; missing variables or nonzero shortage fail closed.
    for prefix in ("primary", "emergency"):
        for t in range(96):
            _close(selected[f"gd_reserve.{prefix}_short[{t}]"], 0, "理想DA备用缺额需专用罚项重算")
    costs["reserve_penalty_cost"] = 0.
    _need(workflow["storage_by_stage"][DA_STAGE]["anchor_periods"] == [], "理想调度锚点罚项不可推断")
    for name, value in selected.items():
        if name.startswith("gd_storage.band_deviation["):
            _close(value, 0, "理想调度无市场DA库容软带")
    costs["anchor_penalty_cost"] = costs["reservoir_band_cost"] = 0.
    for key, value in costs.items():
        _close(value, h["cost_components_yuan"][key], "理想调度冻结报价独立重算", 1e-5)
    return frozen, selected


def _load_hindsight(root: Path, market: ResearchResult) -> HindsightView:
    reader = _Reader(root)
    seal_info = _verify_hindsight_seal(reader)
    completion = reader.json(f"{HINDSIGHT_RUN}/completion.json")
    _need(completion["completed"] is True, "理想调度实验未完成")
    for key in ("synthetic_actual", "full_day_actual_information", "own_storage_trajectories"):
        _need(completion[key] is True, "理想调度信息边界不匹配")
    _need(completion["market_DA_anchors_used"] is False, "理想调度不能借用市场DA储能锚点")
    source = reader.json(f"{HINDSIGHT_RUN}/source_inputs.json")
    _need(source["source_snapshot_sha256"] == market.summary["snapshot_sha256_recorded"], "理想调度源快照与市场基线不一致")
    report = reader.json(f"{HINDSIGHT_RUN}/comparison.json")
    _need(type(report["schema_version"]) is int and report["schema_version"] == 1, "理想调度比较版本不支持")
    comparison, hindsight, market_report = (report[key] for key in ("comparison", "hindsight", "market_rt"))
    _need(hindsight["completed"] is True and market_report["completed"] is True, "比较需要双方完整结果")
    _need(comparison["full_day_interval_alignment_verified"] is True, "比较时轴未对齐")
    for key in ("same_feasible_set", "quoted_objective_difference_is_savings", "global_lower_bound_claim", "pure_forecast_value_claim", "physical_cost_comparable", "settlement_cash_comparable"):
        _need(comparison[key] is False, "当前比较不允许节省、预测价值、下界或现金可比声明")
    for item in (hindsight, market_report):
        for key in ("physical_cost", "settlement"):
            _need(item[key]["available"] is False and item[key]["value_yuan"] is None, "实际成本/结算需要新的独立数据接口")
        costs = [_number(value, "理想比较费用") for value in item["cost_components_yuan"].values()]
        _close(sum(costs), item["quoted_objective_cost_yuan"], "理想比较费用分项")
    _close(market_report["quoted_objective_cost_yuan"], market.summary["rt_quoted_cost_yuan"], "理想比较市场费用身份")
    _need(set(market_report["cost_components_yuan"]) == set(market.rt_costs), "比较市场费用分项不完整")
    for key, value in market.rt_costs.items():
        _close(market_report["cost_components_yuan"][key], value, "比较市场费用分项身份")
    actual_hashes = {Path(item["file"]).name: item["sha256"] for item in market.provenance if item["file"].startswith(f"{RT_RUN}/") and len(Path(item["file"]).parts) == 2}
    for name in ("completion.json", "executed_first_intervals.csv", "validation_summary.json"):
        _need(market_report["source_files"][name] == actual_hashes[name], "理想比较未引用当前RT96证据")
    _close(comparison["quoted_objective_difference_market_minus_hindsight_yuan"], market.summary["rt_quoted_cost_yuan"] - hindsight["quoted_objective_cost_yuan"], "理想比较费用差额")
    _need(hindsight["selected_stage"] == DA_STAGE, "理想调度没有选择其自身连续精修")
    for name in ("DA_SCUC", "DA_SCED", DA_STAGE):
        _stage(hindsight["solver_stages"][name], name, None)
    frozen, selected_values = _hindsight_evidence_chain(reader, completion, source, report, market)
    raw = reader.csv(f"{HINDSIGHT_RUN}/dispatch_by_period.csv")
    _need(len(raw) == 96, "理想调度必须有96个完整区间")
    required = {"global_period", "start", "end", "duration_h", "renewable_available_mw", *ENERGY_KEYS,
                "balance_residual_mw", "max_line_slack_mw"}
    rows = []
    for period, item in enumerate(raw):
        _need(required <= set(item), "理想调度CSV缺少关键字段")
        _need(item["global_period"] == str(period), "理想调度期号不连续")
        _need(item["start"] == market.rt_rows[period]["start"] and item["end"] == market.rt_rows[period]["end"], "理想调度时轴与RT不同")
        row: dict[str, Any] = {"global_period": period, "start": item["start"], "end": item["end"]}
        for key in set(item) - {"global_period", "start", "end"}:
            if item[key] == "" and key not in required:
                row[key] = None
            else:
                try:
                    row[key] = _number(float(item[key]), f"理想调度 {key}")
                except (ValueError, TypeError):
                    raise EvidenceError(f"理想调度 {key}: 缺失或非有限数值") from None
        _need(row["duration_h"] == .25, "理想调度区间长度错误")
        residual = row["thermal_mw"] + row["renewable_mw"] + row["tie_mw"] + row["storage_net_mw"] + row["balance_shortfall_mw"] - row["balance_surplus_mw"] - row["load_mw"]
        _close(residual, row["balance_residual_mw"], "理想调度功率守恒")
        _close(residual, 0, "理想调度功率平衡")
        _close(row["renewable_available_mw"], row["renewable_mw"] + row["curtailment_mw"], "理想新能源可用功率")
        _close(row["load_mw"], frozen["profiles"]["load_actual"][period], "理想调度负荷actual来源")
        _close(row["tie_mw"], frozen["profiles"]["TIE_sched"][period], "理想调度外生受电来源")
        units = frozen["config"]["units"]
        _close(row["renewable_available_mw"], sum(frozen["profiles"][u["id"] + "_actual"][period] for u in units["renewable"]), "理想新能源actual来源")
        for column, family in (("renewable_mw", "renewable_power"), ("curtailment_mw", "renewable_curtailment"), ("balance_shortfall_mw", "balance_shortfall"), ("balance_surplus_mw", "balance_surplus")):
            _close(row[column], sum(_variable(selected_values, family, str(bus), period) for bus in frozen["network"]["buses"]), "理想系统曲线与所选变量")
        _close(row["thermal_mw"], sum(_variable(selected_values, "power", u["id"], period) for u in units["thermal"]), "理想系统热机与所选变量")
        storage_net = sum(_variable(selected_values, "gd_storage.discharge", u["id"], period) - _variable(selected_values, "gd_storage.charge", u["id"], period) for u in units["storage"])
        storage_net += sum(_variable(selected_values, "gd_storage.generate", f'{u["id"]}_U{i}', period) - _variable(selected_values, "gd_storage.pump", f'{u["id"]}_U{i}', period) for u in units["pumped"] for i in range(1, u["n_units"] + 1))
        _close(row["storage_net_mw"], storage_net, "理想系统储能与所选变量")
        rows.append(row)
    for key in ENERGY_KEYS:
        total = sum(row[key] * row["duration_h"] for row in rows)
        _close(total, hindsight["physical"][key.removesuffix("_mw") + "_mwh"], "理想调度能量积分")
        _close(market.summary["rt_energy_mwh"][key], market_report["physical"][key.removesuffix("_mw") + "_mwh"], "比较市场能量身份")
    exogenous_same = True
    for key in ("load_mw", "renewable_available_mw", "tie_mw"):
        differences = []
        for ideal, rt in zip(rows, market.rt_rows):
            market_value = rt["renewable_mw"] + rt["curtailment_mw"] if key == "renewable_available_mw" else rt[key]
            differences.append(ideal[key] - market_value)
        expected = {"max_absolute_difference_mw": max(map(abs, differences)), "signed_energy_difference_mwh": sum(differences) * .25, "absolute_energy_difference_mwh": sum(map(abs, differences)) * .25}
        for field, value in expected.items():
            _close(value, comparison["exogenous_differences"][key][field], "比较外生曲线差异")
        exogenous_same = exogenous_same and expected["max_absolute_difference_mw"] <= 1e-8
    _need(comparison["same_exogenous_trajectory"] is exogenous_same, "比较外生曲线一致性声明错误")
    # Do not return arbitrary absolute source paths to presentation consumers.
    safe_report = {"integrity_seal": seal_info, "comparison": {key: comparison[key] for key in ("same_exogenous_trajectory", "same_feasible_set", "exogenous_differences", "quoted_objective_difference_market_minus_hindsight_yuan")},
                   "hindsight": {key: hindsight[key] for key in ("quoted_objective_cost_yuan", "cost_components_yuan", "physical")},
                   "market_rt": {key: market_report[key] for key in ("quoted_objective_cost_yuan", "cost_components_yuan", "physical")}}
    return HindsightView(True, "已核对冻结实际输入、自身三阶段解、逐机/系统曲线、终态与报价；不是完整模型或官方市场认证", tuple(rows), safe_report, tuple(reader.provenance))


def load_hindsight_comparison(market: ResearchResult, evidence_root: str | Path | None = None) -> HindsightView:
    """Optional, independent benchmark. Missing data remains unavailable, not 0."""
    root = Path(evidence_root) if evidence_root is not None else default_evidence_root()
    if not (root / HINDSIGHT_RUN / "completion.json").exists():
        return HindsightView(False, "尚未提供完整理想调度研究结果")
    try:
        return _load_hindsight(root, market)
    except EvidenceError as exc:
        return HindsightView(False, str(exc))
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        return HindsightView(False, f"理想调度证据字段缺失或结构不兼容（{type(exc).__name__}）")
