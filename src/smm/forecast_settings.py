"""Explicit preview/adoption of a scenario's future renewable-input generator."""
from __future__ import annotations

import copy
import hashlib
import io
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from .config import load_config
from .forecast import apply_profile_overrides, generate_profiles

MODEL_LABELS = {"legacy_v1": "旧版：固定实际曲线与单尺度误差",
                "multiscale_v2": "新版：实际波动与多尺度相关误差"}


def input_fingerprint(root):
    root = Path(root)
    files = ("config/base.yaml", "config/units.yaml", "config/modes.yaml", "data/profile_overrides.csv")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() if (root / name).exists() else None
            for name in files}


def model_name(cfg):
    forecast = cfg.base.get("forecast") or {}
    if not isinstance(forecast, dict):
        raise ValueError("forecast须为配置对象")
    return forecast.get("model", "legacy_v1")


def prepare_model_change(root, model):
    """Pure read: includes user overrides unchanged, never saves generated data."""
    if model not in MODEL_LABELS:
        raise ValueError("未知曲线模型")
    root = Path(root)
    before = input_fingerprint(root)
    cfg = load_config(root=root)
    candidate = copy.deepcopy(cfg)
    candidate.base.setdefault("forecast", {})
    candidate.base["forecast"] = {**(candidate.base["forecast"] or {}), "model": model}
    old = apply_profile_overrides(cfg, generate_profiles(cfg))
    new = apply_profile_overrides(candidate, generate_profiles(candidate))
    if input_fingerprint(root) != before:
        raise RuntimeError("输入已变化，请重新预览")
    rows = []
    for unit in cfg.units["renewable"]:
        uid, cap = unit["id"], float(unit["cap"])
        for version, frame in (("当前", old), ("预览", new)):
            actual, forecast = frame[f"{uid}_actual"], frame[f"{uid}_da"]
            rows.append({"机组": uid, "曲线": version,
                         "日前nRMSE（装机比例）": float(np.sqrt(np.mean((actual - forecast) ** 2)) / cap),
                         "日前平均15分钟变化（MW）": float(abs(np.diff(forecast)).mean()) if len(forecast) > 1 else 0.,
                         "实际平均15分钟变化（MW）": float(abs(np.diff(actual)).mean()) if len(actual) > 1 else 0.})
    override = root / "data/profile_overrides.csv"
    explicit = list(pd.read_csv(override, nrows=0).columns) if override.exists() else []
    return {"root": str(root.resolve()), "model": model, "previous_model": model_name(cfg),
            "fingerprint": before, "summary": rows, "profiles": new, "previous_profiles": old,
            "explicit_override_columns": [c for c in explicit if c not in ("period", "time")]}


def apply_model_change(root, prepared):
    """Adopt one explicitly previewed model; do not alter quotes/overrides/runs.

    A compare-and-set check and shared lock protect cooperating changes. Readers
    see either whole YAML through atomic replace. No historical result is read or
    rewritten; model omission remains legacy for all untouched scenarios.
    """
    from ruamel.yaml import YAML
    from .process_control import file_lock
    root = Path(root)
    if str(root.resolve()) != prepared.get("root"):
        raise ValueError("曲线预览属于另一场景")
    if prepared.get("model") not in MODEL_LABELS:
        raise ValueError("未知曲线模型")
    from .unit_edit_transaction import scene_input_lock
    with file_lock(root / "forecast_model.lock"), scene_input_lock(root):
        if input_fingerprint(root) != prepared.get("fingerprint"):
            raise RuntimeError("场景输入或上传曲线已变化，请重新预览后采用")
        # Revalidate against current files, rather than trusting serialized UI state.
        prepare_model_change(root, prepared["model"])
        path = root / "config/base.yaml"
        yaml = YAML()
        yaml.preserve_quotes = True
        base = yaml.load(path.read_text(encoding="utf-8"))
        if not base.get("forecast"):
            base["forecast"] = {}
        base["forecast"]["model"] = prepared["model"]
        output = io.StringIO()
        yaml.dump(base, output)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".forecast-model-", suffix=".tmp", delete=False) as f:
                name = f.name
                f.write(output.getvalue())
                f.flush()
                os.fsync(f.fileno())
            if input_fingerprint(root) != prepared["fingerprint"]:
                raise RuntimeError("场景输入已变化，未采用，请重新预览")
            os.replace(name, path)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()


def generation_metadata(cfg, *, override_columns=None):
    """Auditable assumptions for newly written results only."""
    from .forecast import PHI, SIGMA
    from .forecast_multiscale import ACTUAL, SLOW_VARIANCE_WEIGHT
    fo = cfg.base.get("forecast") or {}
    model = model_name(cfg)
    path = cfg.root / "data/profile_overrides.csv"
    columns = (list(pd.read_csv(path, nrows=0).columns) if path.exists() else []) if override_columns is None else list(override_columns)
    return {"model": model, "model_label": MODEL_LABELS[model],
            "method": "offline_synthetic_joint_scenario_not_operational_forecasting",
            "scope": "automatic generation only; explicit override columns retain externally supplied values",
            "seed": cfg.base["seed"], "dt_h": cfg.dt,
            "sigma_before_clipping": {k: {**v, **((fo.get("sigma") or {}).get(k) or {})} for k, v in SIGMA.items()},
            "phi": {**PHI, **(fo.get("phi") or {})},
            "actual_fluctuation_assumptions": ACTUAL if model == "multiscale_v2" else None,
            "slow_error_variance_weight": SLOW_VARIANCE_WEIGHT if model == "multiscale_v2" else None,
            "explicit_override_columns": [c for c in columns if c not in ("period", "time")],
            "rt_first_period_actual": bool(cfg.mode.get("realtime", {}).get("first_period_actual", True)),
            "limitations": ["合成数据不是实测风光，也不是可上线预测器。",
                            "标准差为截断前总体假设；不按单日结果反向缩放以凑准确率。",
                            "新版阶段误差共享路径并按幅度收敛，是理想化嵌套误差假设。" if model == "multiscale_v2" else "旧版实际风光为固定日形状。",
                            "上传的显式曲线列优先；报价avail_pmax仍可覆盖日前可用功率。"]}
