"""Discoverable, checksummed output inventory with explicit execution scopes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

DA_TABLE_FILES = {
    "units": "da_unit_awards.csv", "prices": "da_nodal_prices.csv",
    "lines": "da_line_flows.csv", "storage": "da_storage.csv",
    "hydro": "da_hydro.csv", "pumped": "da_pumped.csv", "system": "da_system.csv",
}


def write_da_tables(result, directory):
    """Identical complete DA tables for both CLI entrypoints and web runs."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for key, filename in DA_TABLE_FILES.items():
        result.tables[key].to_csv(directory / filename, index=False, float_format="%.4f")
    result.tables["prices"].pivot(index="bus", columns="time", values="lmp").to_csv(
        directory / "da_lmp_matrix.csv", float_format="%.2f")


def write_profile_evidence(case, directory):
    """Persist the exact in-memory profiles; never reload current input curves."""
    from copy import deepcopy
    from .forecast import accuracy_summary
    from .forecast_settings import generation_metadata
    directory = Path(directory)
    case.profiles.to_csv(directory / "profiles.csv", index=False)
    if getattr(case,'selected_inputs',None) is not None:
        from .natural_inflow import case_input_evidence
        metadata=dict(model='causal_physical_v3',calibrated=False,
            information='causal available observation prefix; explicit RT current-mean research exception',
            input_provenance=case_input_evidence(case),
            accuracy_scope='not_computed_from_future_actual;evaluate_issued_frames_against_executed_or_frozen_research_truth_separately')
        pd.DataFrame(columns=['object','stage','nrmse','accuracy']).to_csv(directory/'forecast_accuracy.csv',index=False)
        (directory/'profile_generation.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        if case.input_provider is not None:
            (directory/'causal_forecast_registry.json').write_text(json.dumps(case.input_provider.archive(case.day_start_utc),ensure_ascii=False,allow_nan=False),encoding='utf-8')
        return
    accuracy = accuracy_summary(case.profiles, {r["id"]: r["cap"] for r in case.cfg.units["renewable"]})
    accuracy.reindex(columns=["object", "stage", "nrmse", "accuracy"]).to_csv(directory / "forecast_accuracy.csv", index=False)
    frozen = case.profiles.attrs.get("generation_metadata")
    if frozen is not None:
        metadata = deepcopy(frozen)
        metadata["metadata_capture"] = "before_solve_with_prepared_profiles"
    else:
        # Direct Python case callers may not capture generator metadata. Their
        # actual profile values are known, but current files cannot prove origin.
        metadata = generation_metadata(case.cfg, override_columns=[])
        metadata["explicit_override_columns"] = None
        metadata["metadata_capture"] = "case_configuration_only;generator_and_override_provenance_not_captured"
    (directory / "profile_generation.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def write_catalog(directory, scope, *, full=False, plot_files=()):
    """Only enumerate files this writer promises to produce, not stale directory extras.

    Hashes describe the archived bytes. They are not proof of solve feasibility or
    a replacement for run-start provenance and frozen inputs.
    """
    directory = Path(directory)
    da_files = [*DA_TABLE_FILES.values(), "da_lmp_matrix.csv", "da_reserve.csv", "da_reserve_system.csv"]
    records = [(name, "日前完整计划") for name in da_files]
    records += [(name, "诊断，数量不执行") for name in (
        "auxiliary_pricing_evidence.json", "stage_diagnostics.json", "reserve_window_evidence.json")]
    records += [(f"auxiliary_pricing_{key}.csv", "辅助定价诊断，数量不执行") for key in (
        "bands", "slack_differences", "reserve_differences", "state_differences", "removed_rt_tracking", "anchor_costs")]
    if full:
        records += [(name, "独立日内计划，未启用时仅空表") for name in (
            "id_rounds_summary.csv", "id_plans.csv", "id_prices_indicative.csv", "id_storage.csv")]
        records += [(f"rt_{key}.csv", "实时已执行首段，每时段只计一次") for key in (
            "units", "prices", "lines", "storage", "hydro", "pumped", "system", "reserve", "reserve_system", "lmp_matrix")]
        records += [(f"settlement_{key}.csv", scope["settlement_scope"]) for key in (
            "detail", "by_entity", "by_type", "reconciliation", "line_rent")]
        records += [(f"operating_profit_{key}.csv", "已执行范围，独立成本核算") for key in ("by_period", "by_unit", "by_type")]
        records += [(name, "前瞻窗口诊断，禁止累计为实际执行") for name in (
            "rt_tracking_windows.csv", "rt_tracking_window_evidence.json", "hydro_window_targets.csv", "hydro_window_evidence.json")]
        records += [(name, "执行范围与冻结成本") for name in ("rolling_actual_states.json", "fixed_cost_profile.json", "output_scope.json", "solver_windows.csv")]
    records += [(name, "保存的实际输入曲线与预测诊断") for name in ("profiles.csv", "forecast_accuracy.csv", "profile_generation.json")]
    if (directory/'causal_forecast_registry.json').is_file():
        records.append(('causal_forecast_registry.json','按发布时间冻结的因果预测与正式选中输入来源；非实际执行总量'))
    if full and (directory/'forecast_error_status.json').is_file():
        records += [(name,'事后预测误差，仅已执行输入范围；重叠预测比较不是额外执行量') for name in
            ('executed_input_actual.csv','forecast_error_detail.csv','forecast_error_summary.csv','forecast_error_status.json')]
    records += [(name, "实时已执行" if name.startswith("rt_") else "日前或阶段对比，按图标题") for name in plot_files]
    if full and (directory/'id_state_value_evidence.json').is_file():
        records.append(('id_state_value_evidence.json','正式SCED原对偶数值缓存及原回退错误；非新求解或辅助价'))
    records.append(("summary.json", "综合摘要，不等于安全认证"))
    artifacts = []
    for filename, meaning in records:
        path = directory / filename
        item = dict(file=filename, scope=meaning, present=path.is_file())
        if path.is_file():
            data = path.read_bytes()
            item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            if path.suffix == ".csv":
                try:
                    frame = pd.read_csv(path)
                    item.update(rows=len(frame), columns=list(frame.columns))
                except pd.errors.EmptyDataError:
                    item.update(rows=0, columns=[], empty_without_schema=True)
        artifacts.append(item)
    manifest = dict(schema_version=1, **scope, artifacts=artifacts,
                    note="Only current writer outputs are listed; unlisted legacy files are not current-run evidence.",
                    quantity_source="formal fixed-integer SCED; RT first period only",
                    information_assumption="RT first period uses offline synthetic actual; future periods use forecast views; not an online data-availability guarantee; boundary_lead_min is not an enforced T-minus-30 forecast publication rule",
                    money_note="DA revenue is planned energy revenue; RT price valuation is not two-settlement cash; profit uses independent frozen costs. Missing costs remain unknown.")
    if (directory/'causal_forecast_registry.json').is_file():
        manifest['information_assumption']='causal_physical_v3: each issued frame uses available observations and released public inputs; first RT interval mean is an explicit perfect-measurement research exception; daily DA is issued at day start, not D-1'
    (directory / "output_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 结果文件索引", "", f"范围：{scope['completed_scope']}；实际执行 {scope['executed_periods']} / {scope['expected_periods']} 段。", "",
             "日前为完整计划，实时仅统计每轮实际执行首段。前瞻窗口可能重叠，不得合计成全天出力、备用或利润。",
             "实时首段使用离线合成实际值，是已知当段实际值的理想化研究假设，不是线上可获得性或预测准确性的证明。",
             "价格表区分正式原始价、选用原始价及限价后结算价；线路正式潮流与辅助定价对偶是不同来源。",
             "日前收入是计划中标收入，不是运营利润。实时按RT价估值不是两级结算；完整现金结算见 settlement_*。",
             "利润仅覆盖实际执行范围；缺失独立成本时为未知，不能用报价或零成本补齐。",
             "本索引不证明求解可行或所有物理约束通过，请同时查看 summary.json 警示与冻结来源。", "",
             "| 文件 | 范围 | 数据行 |", "|---|---|---|"]
    for item in artifacts:
        lines.append(f"| [{item['file']}]({item['file']}) | {item['scope']} | {item.get('rows', '—') if item['present'] else '未生成'} |")
    (directory / "结果文件索引.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return manifest
