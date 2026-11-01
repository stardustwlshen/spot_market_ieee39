"""出清结果绘图：LMP 热力图/曲线、分类型出力堆叠图、储能 SOC、线路负载率。"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402


def _setup_font():
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "SimHei", "AR PL UMing CN"):
        if any(name in f.name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False


_setup_font()
TYPE_ORDER = ["tie", "coal", "hydro", "wind", "pv", "gas_ccgt", "gas_gt", "storage"]
TYPE_LABEL = {"tie": "区外来电", "coal": "燃煤", "hydro": "水电", "wind": "风电", "pv": "光伏", "gas_ccgt": "燃气CCGT",
              "gas_gt": "燃气GT", "storage": "储能(放电)", "storage_ch": "储能(充电)"}
TYPE_COLOR = {"tie": "#7f7f7f", "coal": "#4d4d4d", "hydro": "#1f77b4", "wind": "#2ca02c", "pv": "#ffbf00",
              "gas_ccgt": "#d62728", "gas_gt": "#ff7f0e", "storage": "#9467bd"}


def _xt(ax, T=96):
    ax.set_xticks(range(0, T + 1, 8))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 2)], rotation=0, fontsize=8)
    ax.set_xlim(0, T - 1)


def lmp_heatmap(prices: pd.DataFrame, path: Path, title="日前节点电价 LMP（元/MWh）"):
    piv = prices.pivot(index="bus", columns="period", values="lmp")
    fig, ax = plt.subplots(figsize=(13, 8))
    im = ax.imshow(piv.values, aspect="auto", cmap="RdYlBu_r", interpolation="nearest")
    ax.set_yticks(range(len(piv.index)))
    ax.set_yticklabels(piv.index, fontsize=7)
    ax.set_ylabel("母线")
    _xt(ax, piv.shape[1])
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="元/MWh")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def lmp_curves(prices: pd.DataFrame, system: pd.DataFrame, path: Path, buses=None, title="节点电价曲线"):
    piv = prices.pivot(index="period", columns="bus", values="lmp")
    if buses is None:  # 取最低、最高、和参考母线
        avg = piv.mean()
        buses = sorted(set([avg.idxmin(), avg.idxmax(), 31, 39, 30, 16]))
    fig, ax = plt.subplots(figsize=(12, 5))
    for b in buses:
        ax.plot(piv.index, piv[b], label=f"母线{b}", lw=1.3)
    ax.plot(system.period, system.energy_price, "k--", lw=1.5, label="系统能量价 λ")
    _xt(ax)
    ax.set_ylabel("元/MWh")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(ncol=4, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generation_stack(units: pd.DataFrame, system: pd.DataFrame, path: Path, title="日前出清分类型出力"):
    u = units.copy()
    u["P_pos"] = u.P_mw.clip(lower=0)
    u["P_neg"] = u.P_mw.clip(upper=0)
    pos = u.pivot_table(index="period", columns="type", values="P_pos", aggfunc="sum").fillna(0)
    neg = u[u.type == "storage"].groupby("period").P_neg.sum()
    types = [t for t in TYPE_ORDER if t in pos.columns]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.stackplot(pos.index, *[pos[t] for t in types], labels=[TYPE_LABEL[t] for t in types],
                 colors=[TYPE_COLOR[t] for t in types], alpha=0.9)
    ax.fill_between(neg.index, neg.values, 0, color="#c5b0d5", label=TYPE_LABEL["storage_ch"])
    ax.plot(system.period, system.load_mw, "k-", lw=1.8, label="负荷（日前预测）")
    re = units[units.type.isin(["wind", "pv"])]
    curt = re.groupby("period").curtail_mw.sum()
    if curt.max() > 1e-3:
        top = pos[types].sum(axis=1)
        ax.fill_between(curt.index, top, top + curt, facecolor="none", hatch="////", edgecolor="#2ca02c", lw=0.6, label="风光弃电")
    _xt(ax)
    ax.set_ylabel("MW")
    ax.set_title(title)
    ax.legend(loc="upper left", ncol=5, fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def storage_soc(storage: pd.DataFrame, path: Path, prices: pd.DataFrame | None = None, title="储能充放电与 SOC"):
    units = storage.unit.unique()
    fig, axes = plt.subplots(len(units), 1, figsize=(12, 2.8 * len(units)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, s in zip(axes, units):
        d = storage[storage.unit == s]
        ax.bar(d.period, d.P_dis, color="#9467bd", label="放电 MW")
        ax.bar(d.period, -d.P_ch, color="#c5b0d5", label="充电 MW")
        ax.set_ylabel("MW")
        ax2 = ax.twinx()
        ax2.plot(d.period, d.soc * 100, "k-", lw=1.5, label="SOC %")
        ax2.set_ylim(0, 100)
        ax2.set_ylabel("SOC %")
        if prices is not None:
            bus = d.bus.iloc[0]
            lp = prices[prices.bus == bus].sort_values("period")
            ax3 = ax.twinx()
            ax3.spines["right"].set_position(("outward", 45))
            ax3.plot(lp.period, lp.lmp, color="#d62728", lw=1, alpha=0.7, label="节点电价")
            ax3.set_ylabel("元/MWh", color="#d62728")
        ax.set_title(f"{s}（母线 {d.bus.iloc[0]}）", fontsize=10)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left", ncol=3)
        _xt(ax)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def line_loading(lines: pd.DataFrame, path: Path, top=6, title="重载/阻塞线路负载率"):
    mx = lines.groupby("line").loading.max().sort_values(ascending=False).head(top)
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for l in mx.index:
        d = lines[lines.line == l]
        ax.plot(d.period, d.loading * 100, label=l)
    ax.axhline(100, color="r", ls="--", lw=1)
    _xt(ax)
    ax.set_ylabel("负载率 %")
    ax.set_title(title)
    ax.legend(ncol=6, fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def rebid_compare(base_prices, new_prices, base_units, new_units, unit_ids, path: Path, buses, title="改报价前后对比"):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax = axes[0]
    for b in buses:
        a = base_prices[base_prices.bus == b].sort_values("period")
        c = new_prices[new_prices.bus == b].sort_values("period")
        l, = ax.plot(a.period, a.lmp, lw=1.2, label=f"母线{b} 改前")
        ax.plot(c.period, c.lmp, lw=1.2, ls="--", color=l.get_color(), label=f"母线{b} 改后")
    ax.set_ylabel("LMP 元/MWh")
    ax.legend(ncol=4, fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_title(title)
    ax = axes[1]
    for uid in unit_ids:
        a = base_units[base_units.unit == uid].sort_values("period")
        c = new_units[new_units.unit == uid].sort_values("period")
        l, = ax.plot(a.period, a.P_mw, lw=1.2, label=f"{uid} 改前")
        ax.plot(c.period, c.P_mw, lw=1.2, ls="--", color=l.get_color(), label=f"{uid} 改后")
    ax.set_ylabel("中标出力 MW")
    ax.legend(ncol=4, fontsize=8)
    ax.grid(alpha=0.3)
    _xt(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def forecast_plot(profiles: pd.DataFrame, path: Path, title="日前/日内/实时预测与实际"):
    fig, axes = plt.subplots(4, 1, figsize=(12, 11), sharex=True)
    for ax, key, name in zip(axes, ["load", "W1", "W2", "PV1"], ["系统负荷", "风电 W1", "风电 W2", "光伏 PV1"]):
        ax.plot(profiles.period, profiles[f"{key}_actual"], "k-", lw=1.6, label="实际")
        ax.plot(profiles.period, profiles[f"{key}_da"], "--", label="日前短期预测")
        ax.plot(profiles.period, profiles[f"{key}_id"], ":", label="日内超短期预测")
        ax.set_ylabel("MW")
        ax.set_title(name, fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, ncol=3)
        _xt(ax)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
