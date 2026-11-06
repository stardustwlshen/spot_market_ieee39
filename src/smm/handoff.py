"""Validated, per-resource day-ahead / intraday references.

Reference data never replaces the executed initial state. A new incomplete
intraday resource falls back to day-ahead, never silently to an older ID value.
"""
from __future__ import annotations

import copy
import numpy as np

STATE_KEYS = {"E": "E", "Vu": "Vu", "V": "Vh"}
FLAGS = {"E": "storage", "Vu": "pumped", "V": "hydro"}
THERMAL_TYPES = {"coal", "gas_ccgt", "gas_gt"}
OTHER_TYPES = {"hydro", "storage", "pumped", "wind", "pv"}


def configuration(cfg):
    rc, ic = cfg.mode.get("realtime", {}), cfg.mode.get("intraday", {})
    source = rc.get("handoff_source", "intraday")
    if source not in ("intraday", "day_ahead"):
        raise ValueError("realtime.handoff_source 须为 intraday|day_ahead")
    flags = rc.get("handoff", {})
    if not isinstance(flags, dict) or set(flags) - {"commitment", "storage", "pumped", "hydro"}:
        raise ValueError("realtime.handoff 只能包含 commitment/storage/pumped/hydro 布尔开关")
    if any(type(value) is not bool for value in flags.values()):
        raise ValueError("realtime.handoff 的开关必须为布尔值，不能用字符串或数字")
    commit = rc.get("intraday_commit_rt", "inherit")
    if commit not in ("inherit", "free"):
        raise ValueError("realtime.intraday_commit_rt 须为 inherit|free")
    actual = {u["type"] for u in cfg.units["thermal"]}
    known = THERMAL_TYPES | actual
    levels = {}
    for stage, spec in (("intraday", ic), ("realtime", rc)):
        groups = {}
        for key, default in (("lock_types", ["coal", "gas_ccgt"]), ("free_types", ["gas_gt"])):
            value = spec.get(key, default)
            if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) for x in value):
                raise ValueError(f"{stage}.{key} 必须为机组类型列表")
            allowed = known | (OTHER_TYPES if stage == "intraday" and key == "free_types" else set())
            if len(set(value)) != len(value) or set(value) - allowed:
                raise ValueError(f"{stage}.{key} 包含重复或未知类型")
            groups[key] = set(value)
        if groups["lock_types"] & groups["free_types"]:
            raise ValueError(f"{stage} 启停类型不能同时归入 lock_types 和 free_types")
        if actual - (groups["lock_types"] | groups["free_types"]):
            raise ValueError(f"{stage} 启停分类未覆盖所有启用火电类型")
        levels[stage] = groups
    if levels["intraday"]["lock_types"] & levels["realtime"]["free_types"]:
        raise ValueError("日前锁定类不能同时配置为实时自由启停；请统一两阶段分类")
    return dict(source=source, commit_rt=commit,
                **(dict(commitment=True, storage=True, pumped=True, hydro=True) | flags), levels=levels)


def _array(value, size, label, *, integer=False, binary=False, bounds=None):
    try:
        arr = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: non_numeric") from exc
    if arr.shape != (size,) or not np.isfinite(arr).all():
        raise ValueError(f"{label}: missing_or_invalid_length_or_nonfinite")
    if integer and (np.abs(arr - np.rint(arr)) > 1e-7).any():
        raise ValueError(f"{label}: non_integer")
    if binary and ((arr < 0) | (arr > 1)).any():
        raise ValueError(f"{label}: not_binary")
    if bounds and ((arr < bounds[0] - 1e-5) | (arr > bounds[1] + 1e-5)).any():
        raise ValueError(f"{label}: outside_physical_bounds")
    # Never validate near-integral input and then truncate it differently at use.
    return np.rint(arr) if integer else arr


def _bounds(cfg, kind, unit):
    if cfg is None:
        return None
    family = {"E": "storage", "Vu": "pumped", "V": "hydro"}[kind]
    spec = next(u for u in cfg.units[family] if u["id"] == unit)
    if kind == "E":
        return spec["soc_min"] * spec["ecap"], spec["soc_max"] * spec["ecap"]
    if kind == "Vu":
        return max(spec["vup_min"], spec["v_total"] - spec["vdn_max"]), min(spec["vup_max"], spec["v_total"] - spec["vdn_min"])
    season = spec["seasons"][cfg.base["season"]]
    if spec.get("regulation") == "run_of_river":
        return season["v0"] - float(spec.get("pondage", 0) or 0), season["v0"]
    return season["vmin"], season["vmax"]


def _require_da_subjects(cfg, plan):
    """A missing DA subject must not silently delete that resource's policy."""
    if cfg is None:
        return
    for family, key in (("thermal", "u"), ("storage", "E"), ("pumped", "Vu"), ("hydro", "Vh")):
        for spec in cfg.units[family]:
            if spec["id"] not in plan.get(key, {}):
                raise ValueError(f"day_ahead.{key}.{spec['id']}: missing_required_subject")
    for spec in cfg.units["hydro"]:
        unit = spec["id"]
        if unit not in plan.get("Qout", {}):
            raise ValueError(f"day_ahead.Qout.{unit}: missing_required_subject")
        for z in range(len(spec["zones"])):
            if (unit, z) not in plan.get("delta", {}):
                raise ValueError(f"day_ahead.delta.{unit}.{z}: missing_required_zone")


def _hydro_arrays(cfg, plan, unit, size, label):
    spec = next((u for u in cfg.units["hydro"] if u["id"] == unit), None) if cfg is not None else None
    keys = [(unit, z) for z in range(len(spec["zones"]))] if spec is not None else [jz for jz in plan.get("delta", {}) if jz[0] == unit]
    cap = spec["n_units"] if spec is not None else np.inf
    out = {jz: _array(plan.get("delta", {}).get(jz), size, f"{label}.delta.{jz}", integer=True, bounds=(0, cap)) for jz in keys}
    if out and (sum(out.values()) > cap + 1e-7).any():
        raise ValueError(f"{label}.delta.{unit}: total_units_exceeds_physical_count")
    return out


def merge_intraday(da_plan, previous, id_round, total, values, *, cfg=None, value_error=None, resource_errors=None):
    _require_da_subjects(cfg, da_plan)
    tau = id_round.get("tau")
    if isinstance(tau, bool) or not isinstance(tau, (int, np.integer)) or not 0 <= tau < total:
        raise ValueError("日内参考轮次 tau 必须位于当天时段内")
    tau, size = int(tau), total - int(tau)
    snap = id_round.get("snap") or {}
    out = copy.deepcopy(previous or da_plan)
    out["reference_metadata"] = {k: {} for k in (*STATE_KEYS, "u")}
    out.setdefault("state_values", {})
    source = f"intraday@{tau}"

    def set_tail(key, unit, candidate):
        base = _array(da_plan.get(key, {}).get(unit), total, f"day_ahead.{key}.{unit}")
        old = out.setdefault(key, {}).get(unit, base)
        arr = np.asarray(old, dtype=float).copy()
        arr[tau:] = candidate
        out[key][unit] = arr

    for unit in da_plan.get("u", {}):
        reason = None
        try:
            arr = _array(snap.get("u", {}).get(unit), size, f"u.{unit}", integer=True, binary=True)
        except ValueError as exc:
            reason = str(exc)
            arr = _array(da_plan["u"][unit], total, f"day_ahead.u.{unit}", integer=True, binary=True)[tau:]
        set_tail("u", unit, arr)
        out["reference_metadata"]["u"][unit] = dict(source="day_ahead" if reason else source,
             round=None if reason else tau, attempted_round=tau, fallback_reason=reason)

    for kind, key in STATE_KEYS.items():
        for unit in da_plan.get(key, {}):
            reason, updates, rec = None, {}, {}
            try:
                updates[(key, unit)] = _array(snap.get(key, {}).get(unit), size, f"{key}.{unit}", bounds=_bounds(cfg, kind, unit))
                error = value_error or (resource_errors or {}).get((kind, unit))
                if error:
                    raise ValueError(error)
                rec = values.get(kind, {}).get(unit, {})
                vals = {field: _array(rec.get(field), size, f"state_values.{kind}.{unit}.{field}")
                        for field in ("value_native", "value_yuan_per_mwh", "dual_raw")}
                if kind == "V":
                    for jz, arr in _hydro_arrays(cfg, snap, unit, size, "intraday").items():
                        updates[("delta", jz)] = arr
                    updates[("Qout", unit)] = _array(snap.get("Qout", {}).get(unit), size, f"Qout.{unit}", bounds=(0, np.inf))
            except ValueError as exc:
                reason = str(exc)
                fields = [(key, unit)]
                if kind == "V":
                    fields += [("delta", jz) for jz in da_plan.get("delta", {}) if jz[0] == unit]
                    fields += [("Qout", unit)]
                updates = {(field, uid): _array(da_plan.get(field, {}).get(uid), total, f"day_ahead.{field}.{uid}")[tau:]
                           for field, uid in fields}
                rec = da_plan.get("state_values", {}).get(kind, {}).get(unit, {})
                vals = {field: _array(rec.get(field), total, f"day_ahead.state_values.{kind}.{unit}.{field}")[tau:]
                        for field in ("value_native", "value_yuan_per_mwh", "dual_raw")}
            for (field, uid), arr in updates.items():
                set_tail(field, uid, arr)
            old = out["state_values"].setdefault(kind, {}).get(unit, da_plan.get("state_values", {}).get(kind, {}).get(unit, {}))
            merged = copy.deepcopy(rec)
            for field, tail in vals.items():
                arr = _array(old.get(field), total, f"previous.state_values.{kind}.{unit}.{field}").copy()
                arr[tau:] = tail
                merged[field] = arr
            merged["source"] = np.array(old.get("source", ["day_ahead"] * total), dtype=object)
            merged["source"][tau:] = "day_ahead" if reason else source
            out["state_values"][kind][unit] = merged
            out["reference_metadata"][kind][unit] = dict(trajectory_source="day_ahead" if reason else source,
                value_source="day_ahead" if reason else source, round=None if reason else tau,
                attempted_round=tau, fallback_reason=reason)
    sources = {v.get("trajectory_source", v.get("source")) for units in out["reference_metadata"].values() for v in units.values()}
    out["reference_source"] = next(iter(sources)) if len(sources) == 1 else "mixed"
    return out


def selected_reference(cfg, da_plan, ref, id_active):
    """Select switches without rewriting old source=day_ahead/free-GT semantics."""
    ho = configuration(cfg)
    _require_da_subjects(cfg, da_plan)
    use = bool(id_active and ho["source"] == "intraday" and ref is not None)
    selected = dict(da_plan)
    selected["state_values"] = dict(da_plan.get("state_values", {}))
    metadata = {k: {} for k in (*STATE_KEYS, "u")}
    for kind, key in STATE_KEYS.items():
        relevant = ((kind == "V" and cfg.mode["realtime"].get("hydro", "value") == "value")
                    or (kind != "V" and cfg.mode["realtime"]["storage"].get("mode", "value") == "value"))
        chosen = ref if use and ho[FLAGS[kind]] and relevant else da_plan
        selected[key] = chosen.get(key, {})
        selected["state_values"][kind] = chosen.get("state_values", {}).get(kind, {})
        if kind == "V":
            selected["delta"] = chosen.get("delta", {})
            selected["Qout"] = chosen.get("Qout", {})
        for unit in da_plan.get(key, {}):
            label = chosen.get("reference_source", "intraday" if chosen is ref else "day_ahead")
            metadata[kind][unit] = copy.deepcopy(chosen.get("reference_metadata", {}).get(kind, {}).get(unit,
                dict(trajectory_source=label, value_source=label, round=None, fallback_reason=None)))
            if not relevant:
                metadata[kind][unit]["value_source"] = None
            def validate(plan):
                _array(plan.get(key, {}).get(unit), cfg.T, f"selected.{key}.{unit}", bounds=_bounds(cfg, kind, unit))
                if relevant:
                    _array(plan.get("state_values", {}).get(kind, {}).get(unit, {}).get("value_native"), cfg.T,
                           f"selected.state_values.{kind}.{unit}.value_native")
                if kind == "V":
                    normalized = _hydro_arrays(cfg, plan, unit, cfg.T, "selected")
                    selected["delta"] = dict(selected["delta"])
                    selected["delta"].update(normalized)
                    _array(plan.get("Qout", {}).get(unit), cfg.T, f"selected.Qout.{unit}", bounds=(0, np.inf))
            try:
                validate(chosen)
            except ValueError as exc:
                if chosen is da_plan:
                    raise
                validate(da_plan)  # Required DA fields cannot silently become zero.
                selected[key] = dict(selected[key], **{unit: da_plan[key][unit]})
                selected["state_values"][kind] = dict(selected["state_values"][kind], **{unit: da_plan["state_values"][kind][unit]})
                if kind == "V":
                    selected["delta"] = dict(selected["delta"])
                    selected["delta"].update({jz: arr for jz, arr in da_plan["delta"].items() if jz[0] == unit})
                    selected["Qout"] = dict(selected["Qout"], **{unit: da_plan["Qout"][unit]})
                metadata[kind][unit] = dict(trajectory_source="day_ahead", value_source="day_ahead", round=None, fallback_reason=str(exc))
    return selected, metadata, ho, use
