"""负荷/风光 96 点曲线与“预测 vs 实际”数据生成（方案第 9.1–9.2 节）。

- 下列为 legacy_v1（缺省，保持历史复现）；multiscale_v2 须显式采用，见 forecast_multiscale.py。
- 旧版实际曲线 A：标幺日曲线（假设值形状）× 容量 / 峰值。
- 预测 F = A − e。误差 e 用 AR(1) 生成：e_t = φ e_{t-1} + σ sqrt(1-φ²) ξ_t，只作为数据工具，不进入出清模型。
- 越晚越准（假设做法）：日内/超短期误差 = 同一条噪声按 σ 比例缩小 + 少量独立噪声。
- σ 取方案 sigma 表的中值（风电日前 15%、光伏 12.5%（白天）、负荷 2.5%），均为假设值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

# 24 点标幺形状（假设值），再线性插值到 96 点
LOAD_SHAPE = [0.72, 0.68, 0.66, 0.65, 0.66, 0.70, 0.77, 0.85, 0.92, 0.96, 0.98, 0.97,
              0.93, 0.92, 0.94, 0.97, 0.99, 1.00, 0.99, 0.98, 0.95, 0.90, 0.83, 0.77]
WIND_A = [0.62, 0.66, 0.70, 0.72, 0.71, 0.68, 0.62, 0.55, 0.47, 0.40, 0.34, 0.30,
          0.28, 0.27, 0.29, 0.33, 0.38, 0.45, 0.52, 0.58, 0.62, 0.64, 0.64, 0.63]
WIND_B = [0.55, 0.58, 0.60, 0.61, 0.60, 0.57, 0.52, 0.46, 0.41, 0.37, 0.35, 0.34,
          0.35, 0.37, 0.40, 0.43, 0.47, 0.51, 0.55, 0.58, 0.60, 0.60, 0.58, 0.56]
TIE_SHAPE = [0.80, 0.78, 0.76, 0.75, 0.75, 0.78, 0.82, 0.86, 0.90, 0.92, 0.92, 0.90,
             0.88, 0.88, 0.90, 0.92, 0.94, 0.95, 0.95, 0.94, 0.92, 0.90, 0.86, 0.82]

SIGMA = {  # 标准差：风光为装机占比，负荷为当时负荷占比（假设值，方案 sigma 表中值）
    "wind": {"da": 0.15, "id": 0.065, "rt": 0.03},
    "pv": {"da": 0.125, "id": 0.05, "rt": 0.025},
    "load": {"da": 0.025, "id": 0.0075, "rt": 0.004},
}
PHI = {"wind": 0.96, "pv": 0.92, "load": 0.95}
RHO_W1W2 = 0.6


def interp96(shape24, T=96):
    x24 = np.arange(24) + 0.5
    x = (np.arange(T) + 0.5) * 24 / T
    return np.interp(x, x24, shape24, period=24)


def pv_shape(T=96, sunrise=6.5, sunset=19.5, peak=0.78):
    h = (np.arange(T) + 0.5) * 24 / T
    s = np.clip(np.sin(np.pi * (h - sunrise) / (sunset - sunrise)), 0, None)
    s[(h < sunrise) | (h > sunset)] = 0
    return peak * s ** 1.3


def ar1(T, phi, rng, xi=None):
    xi = rng.standard_normal(T) if xi is None else xi
    e = np.zeros(T)
    e[0] = xi[0]
    for t in range(1, T):
        e[t] = phi * e[t - 1] + np.sqrt(1 - phi ** 2) * xi[t]
    return e  # 单位方差



def validate_profiles(cfg, frame):
    if not frame.columns.is_unique:
        raise ValueError('profiles列名重复')
    if len(frame) != cfg.T:
        raise ValueError(f"profiles需{cfg.T}行")
    if 'period' in frame and not np.array_equal(frame['period'].to_numpy(), np.arange(1, cfg.T + 1)):
        raise ValueError('profiles period须按1..T严格顺序且无重复/缺失')
    from .input_validation import _series
    caps = {r['id']:float(r['cap']) for r in cfg.units['renewable']}
    for name in ['load']+list(caps):
        for stage in ('actual','da','id','rt'):
            col=f'{name}_{stage}'
            if col not in frame:raise ValueError(f'profiles缺少{col}')
            values = _series(frame[col].to_numpy(), cfg.T, f'profiles {col}')
            if name in caps and (values>caps[name]+1e-9).any():raise ValueError(f'profiles {col}超出装机容量')
    from .fixed_load import tie_mode, tie_profiles
    if tie_mode(cfg) == "fixed_load":
        for unit in cfg.units.get("tie", []):
            tie_profiles(cfg, frame, unit)
    else:
        for unit in cfg.units.get('tie', []):
            col = f"{unit['id']}_sched"
            if col not in frame:
                raise ValueError(f'profiles缺少区外来电曲线{col}')
            _series(frame[col].to_numpy(), cfg.T, f'profiles {col}', maximum=unit['pmax'])
    return frame


def generate_profiles(cfg: Config, seed: int | None = None) -> pd.DataFrame:
    """Versioned offline scenarios; omitted model retains the exact legacy path.

    ``multiscale_v2`` is explicitly selected for future runs, never inferred from
    the code version. Uploaded columns still take precedence in the separate
    ``apply_profile_overrides`` step. Neither model is an operational forecaster.
    """
    from .input_validation import validate_renewable_profiles
    validate_renewable_profiles(cfg)
    fo = cfg.base.get("forecast") or {}
    if not isinstance(fo, dict):
        raise ValueError("forecast须为配置对象")
    model = fo.get("model", "legacy_v1")
    if model not in ("legacy_v1", "multiscale_v2"):
        raise ValueError("forecast.model须为legacy_v1或multiscale_v2")
    frame = _generate_legacy_profiles(cfg, seed)
    if model == "multiscale_v2":
        from .forecast_multiscale import renewable_profiles
        frame = renewable_profiles(cfg, frame, seed)
    return validate_profiles(cfg, frame)


def _generate_legacy_profiles(cfg: Config, seed: int | None = None) -> pd.DataFrame:
    """返回 96 行：各对象的 actual / da / id / rt 四列（MW）及 tie 计划。"""
    T = cfg.T
    rng = np.random.default_rng(cfg.base["seed"] if seed is None else seed)
    # 可选覆盖（base.yaml 的 forecast 段，网页应用“风光预测误差”用）：sigma.{wind,pv,load}.{da,id,rt}、phi.{wind,pv,load}
    fo = cfg.base.get("forecast") or {}
    if not isinstance(fo, dict) or set(fo)-{'model','sigma','phi','error_scenarios'}:
        raise ValueError('forecast仅支持model/sigma/phi配置')
    for key in ('sigma','phi'):
        value=fo.get(key) or {}
        if not isinstance(value,dict) or set(value)-set(PHI):raise ValueError(f'forecast.{key}类型/资源键非法')
    for kind, value in (fo.get('sigma') or {}).items():
        if not isinstance(value,dict) or set(value)-{'da','id','rt'}:raise ValueError(f'forecast.sigma.{kind}阶段键非法')
    SIG = {k: {**v, **((fo.get("sigma") or {}).get(k) or {})} for k, v in SIGMA.items()}
    PH = {**PHI, **(fo.get("phi") or {})}
    from .input_validation import number
    for kind, phi in PH.items():
        if abs(number(phi, f'forecast.phi.{kind}')) >= 1:
            raise ValueError(f'forecast.phi.{kind}须满足abs(phi)<1')
    for kind, stages in SIG.items():
        for stage, sigma in stages.items():
            if number(sigma, f'forecast.sigma.{kind}.{stage}') < 0:
                raise ValueError(f'forecast.sigma.{kind}.{stage}须非负')
    peak = number(cfg.base["load_scale"], 'load_scale', positive=True) * 6254.23
    out = pd.DataFrame({"period": np.arange(1, T + 1)})
    from .time_grid import period_label
    out["time"] = [period_label(t, cfg.dt) for t in range(T)]

    def make(name, actual, cap_for_sigma, kind, xi=None, daylight=None, clip_cap=None):
        base = ar1(T, PH[kind], rng, xi)
        scale = cap_for_sigma if np.ndim(cap_for_sigma) else np.full(T, cap_for_sigma)
        if daylight is not None:
            scale = scale * daylight
        out[f"{name}_actual"] = actual
        for stage in ("da", "id", "rt"):
            indep = ar1(T, PH[kind], rng) * 0.3 if stage != "da" else 0.0
            e = SIG[kind][stage] * scale * (base + indep)   # = σ_da·scale·(ratio·base + ratio·indep)，ratio = σ_stage/σ_da
            f = actual - e
            if kind in ("wind", "pv"):
                f = np.clip(f, 0, clip_cap)  # 物理截断
                if daylight is not None:
                    f = np.where(daylight > 0, f, 0.0)
            out[f"{name}_{stage}"] = np.round(f, 3)

    load_a = peak * interp96(LOAD_SHAPE, T) * (1 + 0.01 * ar1(T, 0.9, rng))
    make("load", load_a, load_a, "load")
    # 风电：W1、W2 噪声相关（ρ=0.6，假设值）
    z1, z2 = rng.standard_normal(T), rng.standard_normal(T)
    xi1, xi2 = z1, RHO_W1W2 * z1 + np.sqrt(1 - RHO_W1W2 ** 2) * z2
    for spec in cfg.units["renewable"]:
        cap = spec["cap"]
        if spec["profile"] == "wind_a":
            make(spec["id"], cap * interp96(WIND_A, T), cap, "wind", xi=xi1, clip_cap=cap)
        elif spec["profile"] == "wind_b":
            make(spec["id"], cap * interp96(WIND_B, T), cap, "wind", xi=xi2, clip_cap=cap)
        else:
            shp = pv_shape(T)
            make(spec["id"], cap * shp, cap, "pv", daylight=(shp > 0).astype(float) * np.sqrt(shp / shp.max()), clip_cap=cap)
    for tie in cfg.units.get("tie", []):
        out[f"{tie['id']}_sched"] = np.round(tie["pmax"] * interp96(TIE_SHAPE, T), 3)
        from .fixed_load import tie_mode
        if tie_mode(cfg) == "fixed_load":
            for stage in ("actual", "da", "id", "rt"):
                out[f"{tie['id']}_{stage}"] = out[f"{tie['id']}_sched"].copy()
    return validate_profiles(cfg, out)


def apply_profile_overrides(cfg: Config, df: pd.DataFrame, overrides: pd.DataFrame | None = None) -> pd.DataFrame:
    """用 cfg.root/data/profile_overrides.csv（96 行，列为 {对象}_{actual,da,id,rt} 的子集）覆盖生成的曲线。

    覆盖实际值而未覆盖预测时，各阶段预测保持原误差（负荷按实际值比例缩放误差，风光保持绝对误差并截断到 [0, 装机]）；
    显式预测列（da/id/rt）优先于推导值，仅改变对应列。所有提供列须有限、非负，风光不超过装机。网页应用的“负荷 96 点编辑 / 上传 CSV”即写这个文件。
    """
    validate_profiles(cfg, df)
    from .resource_error_scenarios import apply_error_scenarios
    from .profile_sources import override_sources
    supplied = overrides is not None
    if overrides is None:
        p = cfg.root / "data" / "profile_overrides.csv"
        if not p.exists():
            return apply_error_scenarios(cfg, df)
        from .input_validation import read_input_csv
        overrides = read_input_csv(p)
    if len(overrides) != len(df):
        raise ValueError(f"profile_overrides 需 {len(df)} 行，实际 {len(overrides)} 行")
    sources = (dict(protected_columns=[c for c in overrides if c not in ('period', 'time')],
                    generated_columns=[], configured_forecasts={}) if supplied else override_sources(cfg, overrides.columns))
    out = df.copy()
    caps = {r["id"]: r["cap"] for r in cfg.units["renewable"]}
    from .fixed_load import tie_mode, tie_profiles
    fixed_names = []
    if tie_mode(cfg) == "fixed_load":
        for unit in cfg.units.get("tie", []):
            fixed_names.append(unit["id"])
            for col, values in tie_profiles(cfg, df, unit).items():
                out[col] = values
    names = ["load"] + list(caps) + fixed_names
    allowed={'period','time'}|{f'{n}_{stage}' for n in names for stage in ('actual','da','id','rt')}
    unknown=set(overrides.columns)-allowed
    if unknown:raise ValueError(f'profile_overrides未知列: {sorted(unknown)}')
    if not overrides.columns.is_unique:raise ValueError('profile_overrides列名重复')
    if 'period' in overrides:
        values=pd.to_numeric(overrides['period'],errors='coerce').to_numpy()
        if not np.array_equal(values,np.arange(1,len(df)+1)):
            raise ValueError('profile_overrides period须按1..T严格顺序且无重复/缺失')
    if 'time' in overrides and not np.array_equal(overrides['time'].astype(str).to_numpy(),df['time'].astype(str).to_numpy()):
        raise ValueError('profile_overrides time与时段顺序不一致')
    # Full-stage boundary scenarios must not silently ignore ID/RT input columns.
    # No override file retains the exact original generation path.
    for n in names:
        for stage in ("actual", "da", "id", "rt"):
            col = f"{n}_{stage}"
            if col not in overrides:
                continue
            if any(isinstance(value, (bool, np.bool_)) for value in overrides[col]):
                raise ValueError(f"profile_overrides {col} 不得包含布尔值")
            try:
                values = overrides[col].to_numpy(dtype=float)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"profile_overrides {col} 必须为有限数值") from exc
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError(f"profile_overrides {col} 必须为有限非负数值")
            if n in caps and (values > float(caps[n]) + 1e-9).any():
                raise ValueError(f"profile_overrides {col} 超出装机容量 {caps[n]} MW")
    for n in names:
        a_col = f"{n}_actual"
        if a_col in overrides and overrides[a_col].notna().all():
            a_old = out[a_col].to_numpy(float)
            a_new = overrides[a_col].to_numpy(float)
            for st in (() if n in fixed_names else ("da", "id", "rt")):
                e = a_old - out[f"{n}_{st}"].to_numpy(float)
                if n == "load":
                    e = e * np.divide(a_new, a_old, out=np.ones_like(a_new), where=a_old > 0)
                f = a_new - e
                if n in caps:
                    f = np.clip(f, 0, caps[n])
                out[f"{n}_{st}"] = np.round(f, 3)
            out[a_col] = a_new
        for stage in ("da", "id", "rt"):
            col = f"{n}_{stage}"
            if col in overrides:
                out[col] = overrides[col].to_numpy(float)
    # Stored typical profiles contain all stages. Only verified system columns
    # may return to their configured baseline before the selected error recipe.
    for col, values in sources['configured_forecasts'].items():
        if col in sources['generated_columns'] and col.endswith(('_da', '_id', '_rt')):
            out[col] = values
    out = apply_error_scenarios(cfg, out, protected_columns=sources['protected_columns'])
    return validate_profiles(cfg, out)


def save_profiles(cfg: Config, df: pd.DataFrame) -> str:
    p = cfg.root / "data" / "profiles" / f"profiles_{cfg.base['date']}_{cfg.base['season']}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)
    return str(p)


def accuracy_summary(df: pd.DataFrame, caps: dict) -> pd.DataFrame:
    """nRMSE 与 1 − nRMSE（近似“准确率”口径）。"""
    rows = []
    for name, cap in caps.items():
        for st in ("da", "id", "rt"):
            e = df[f"{name}_actual"] - df[f"{name}_{st}"]
            denom = cap if cap else df[f"{name}_actual"].mean()
            nrmse = float(np.sqrt((e ** 2).mean()) / denom)
            rows.append(dict(object=name, stage=st, nrmse=round(nrmse, 4), accuracy=round(1 - nrmse, 4)))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- 滚动阶段“当时可见”的预测
LEAD_ID = 16   # 提前 ≥ 4 h（16 个时段）用日内预测误差；更近按线性过渡到超短期误差（假设做法，见方案第 9 节）


def forecast_view(profiles: pd.DataFrame, name: str, issue_t: int, periods, first_actual: bool = False) -> np.ndarray:
    """在 issue_t 时刻发布、对 periods 各时段的预测（MW）。

    提前量 lead = t − issue_t：lead ≤ 0 且 first_actual → 实际值；1 ≤ lead ≤ 16 时误差由超短期(rt)线性过渡到日内(id)；
    lead > 16 用日内误差。越临近越准，同一时段的预测随发布时刻推进而收敛到实际值。
    """
    if profiles.attrs.get('forecast_model') == 'causal_physical_v3':
        raise ValueError('causal_physical_v3 must use its issued ForecastFrame, never legacy forecast_view')
    a = profiles[f"{name}_actual"].to_numpy()
    e_id = a - profiles[f"{name}_id"].to_numpy()
    e_rt = a - profiles[f"{name}_rt"].to_numpy()
    out = []
    for t in periods:
        lead = t - issue_t
        if lead <= 0:
            if first_actual:
                out.append(a[t])
                continue
            lead = 1
        w = min(1.0, (lead - 1) / (LEAD_ID - 1))
        out.append(a[t] - ((1 - w) * e_rt[t] + w * e_id[t]))
    return np.clip(np.array(out, float), 0, None)
