"""负荷/风光 96 点曲线与“预测 vs 实际”数据生成（方案第 9.1–9.2 节）。

- 实际曲线 A：标幺日曲线（假设值形状）× 容量 / 峰值。
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


def generate_profiles(cfg: Config, seed: int | None = None) -> pd.DataFrame:
    """返回 96 行：各对象的 actual / da / id / rt 四列（MW）及 tie 计划。"""
    T = cfg.T
    rng = np.random.default_rng(cfg.base["seed"] if seed is None else seed)
    peak = cfg.base["load_scale"] * 6254.23
    out = pd.DataFrame({"period": np.arange(1, T + 1)})
    out["time"] = [f"{(t*15)//60:02d}:{(t*15)%60:02d}" for t in range(T)]

    def make(name, actual, cap_for_sigma, kind, xi=None, daylight=None, clip_cap=None):
        base = ar1(T, PHI[kind], rng, xi)
        scale = cap_for_sigma if np.ndim(cap_for_sigma) else np.full(T, cap_for_sigma)
        if daylight is not None:
            scale = scale * daylight
        out[f"{name}_actual"] = actual
        for stage in ("da", "id", "rt"):
            ratio = SIGMA[kind][stage] / SIGMA[kind]["da"]
            indep = ar1(T, PHI[kind], rng) * 0.3 if stage != "da" else 0.0
            e = SIGMA[kind]["da"] * scale * (ratio * base + (indep * ratio))
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
    return out


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
