"""配置读取。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
import math
from numbers import Real

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    base: dict
    units: dict
    mode: dict
    root: Path = field(default=ROOT)

    @property
    def rule_set(self) -> str:
        return self.base["rule_set"]

    @property
    def T(self) -> int:
        return int(self.base["periods"])

    @property
    def dt(self) -> float:
        return float(self.base["dt_h"])


def load_config(rule_set: str | None = None, season: str | None = None, overrides: dict | None = None,
                root: Path | str | None = None) -> Config:
    """root：项目根目录（默认本仓库）；场景运行时传 scenarios/<名称>，其下有 config/、data/、bids/，布局与仓库一致。"""
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(Path(root) if root else ROOT):
        return _load_config_unlocked(rule_set, season, overrides, root)


def _load_config_unlocked(rule_set=None, season=None, overrides=None, root=None):
    root = Path(root) if root else ROOT
    base = yaml.safe_load((root / "config" / "base.yaml").read_text(encoding="utf-8"))
    if rule_set:
        base["rule_set"] = rule_set
    if season:
        base["season"] = season
    if overrides:
        base.update(overrides)
    units = yaml.safe_load((root / "config" / "units.yaml").read_text(encoding="utf-8"))
    units = filter_enabled(units)
    modes = yaml.safe_load((root / "config" / "modes.yaml").read_text(encoding="utf-8"))
    mode = modes[base["rule_set"]]
    validate_auxiliary_pricing(mode)
    validate_tracking_policy(mode)
    rt_terminal_policy(mode)
    cfg = Config(base=base, units=units, mode=mode, root=root)
    from .fixed_load import tie_mode
    tie_mode(cfg)
    from .storage_anchors import validate_anchor_config
    validate_anchor_config(cfg)
    from .input_validation import validate_physical_inputs
    validate_physical_inputs(cfg)
    from .handoff import configuration
    configuration(cfg)
    return cfg


def filter_enabled(units: dict) -> dict:
    """去掉 enabled: false 的资源（水电/抽蓄的可选电站），其 id 记入 units["_disabled"]（报价校验时忽略）。"""
    units = dict(units)
    units.setdefault("pumped", [])
    units["pumped"] = units["pumped"] or []
    dis = []
    for g in ("thermal", "renewable", "hydro", "storage", "pumped", "tie"):
        lst = units.get(g) or []
        dis += [u["id"] for u in lst if not u.get("enabled", True)]
        units[g] = [u for u in lst if u.get("enabled", True)]
    units["_disabled"] = dis
    return units


def validate_auxiliary_pricing(mode: dict) -> None:
    """Validate explicitly configured future pricing inputs; never enable a solve.

    Old snapshots without this block remain readable. Historical
    ``pricing_penalties`` is not an alias and cannot activate this policy.
    """
    if "auxiliary_pricing" not in mode:
        return
    spec = mode["auxiliary_pricing"]
    if not isinstance(spec, dict):
        raise ValueError("auxiliary_pricing 必须为配置对象")
    if not isinstance(spec.get("enabled"), bool):
        raise ValueError("辅助算价enabled须为布尔值")
    penalties = spec.get("penalties_yuan_per_mwh")
    if not isinstance(penalties, dict) or set(penalties) != {"balance", "line", "reserve"}:
        raise ValueError("辅助算价须分别配置 balance/line/reserve，单位元/MWh")
    for key, value in penalties.items():
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
            raise ValueError(f"辅助算价 {key} 须为有限非负数，单位元/MWh")
    band = spec.get("dispatch_band")
    if not isinstance(band, dict) or set(band) != {"relative_to_dispatch", "minimum_mw"}:
        raise ValueError("辅助算价出力带须配置 relative_to_dispatch 与 minimum_mw")
    for key, value in band.items():
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
            raise ValueError(f"辅助算价出力带 {key} 须为有限非负数")

    if spec.get("rt_tracking_policy", "preserve") not in ("preserve", "remove"):
        raise ValueError("辅助RT跟踪策略须为preserve或remove")
    anchor = spec.get("anchor_penalty_yuan_per_discharge_mwh")
    if anchor is not None and (isinstance(anchor, bool) or not isinstance(anchor, Real) or not math.isfinite(anchor) or anchor < 0):
        raise ValueError("辅助锚点罚须为有限非负数，单位元/可放电等价MWh")


def validate_value_method(mode: dict) -> None:
    """第二轮C/D项：实时价值法参数（偏差带比例、回退罚因子，均为假设值）。"""
    rc = mode.get("realtime", {})
    if rc.get("storage", {}).get("mode", "value") not in ("value", "sced_anchor", "fixed_da"):
        raise ValueError("realtime.storage.mode须为value/sced_anchor/fixed_da")
    if rc.get("hydro", "value") not in ("value", "window_end_da", "fixed_da", "sced"):
        raise ValueError("realtime.hydro须为value/window_end_da/fixed_da/sced")
    spec = rc.get("value_method", {}) or {}
    for key in ("band_fraction", "hydro_band_fraction"):
        value = spec.get(key, 0.02)
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"value_method.{key}须为[0,1]内有限数")
    if spec.get("hydro_lookahead", "off") not in ("off", "reference_continuation"):
        raise ValueError("value_method.hydro_lookahead须为off/reference_continuation")
    value = spec.get("fallback_penalty", 1e5)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ValueError("value_method.fallback_penalty须为有限正数")


def validate_tracking_policy(mode: dict) -> None:
    """Explicit optional RT tracking migration; absent means historical policy."""
    validate_value_method(mode)
    spec = mode.get("realtime", {}).get("tracking_policy")
    if spec is None:
        return
    if not isinstance(spec, dict) or spec.get("mode") != "soft_deliverable_energy":
        raise ValueError("RT tracking_policy.mode须为soft_deliverable_energy")
    value = spec.get("penalty_yuan_per_discharge_mwh")
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ValueError("RT跟踪罚须为有限正数，单位元/可放电等价MWh")


def rt_terminal_policy(mode: dict) -> dict:
    """Explicit new RT policy; absent blocks preserve historical hard targets."""
    spec = mode.get("realtime", {}).get("value_method", {}).get("terminal_policy")
    if spec is None:
        return {"mode": "hard", "penalty_yuan_per_discharge_mwh": None}
    if not isinstance(spec, dict) or set(spec) - {"mode", "penalty_yuan_per_discharge_mwh"}:
        raise ValueError("realtime.value_method.terminal_policy 只能配置 mode 和 penalty_yuan_per_discharge_mwh")
    policy = spec.get("mode")
    if policy not in ("hard", "soft"):
        raise ValueError("实时终态 mode 须为 hard|soft")
    penalty = spec.get("penalty_yuan_per_discharge_mwh")
    if policy == "soft" or penalty is not None:
        if isinstance(penalty, bool) or not isinstance(penalty, Real) or not math.isfinite(penalty) or penalty <= 0:
            raise ValueError("实时软终态罚价必须为有限正数，单位元/可放电MWh")
        penalty = float(penalty)
    return {"mode": policy, "penalty_yuan_per_discharge_mwh": penalty}
