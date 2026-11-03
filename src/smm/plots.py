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
TYPE_LABEL = {"tie": "区外来电", "coal": "燃煤", "hydro": "水电", "wind": "风电", "pv": "光伏", "gas_ccgt": "燃气联合循环",
              "gas_gt": "燃气轮机", "storage": "储能(放电)", "storage_ch": "储能(充电)"}
TYPE_COLOR = {"tie": "#7f7f7f", "coal": "#4d4d4d", "hydro": "#1f77b4", "wind": "#2ca02c", "pv": "#ffbf00",
              "gas_ccgt": "#d62728", "gas_gt": "#ff7f0e", "storage": "#9467bd"}


def _xt(ax, T=96):
    step = 8 if T > 24 else 2
    ticks = list(range(0, T + 1, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{(k*15)//60:02d}:{(k*15)%60:02d}" for k in ticks], rotation=0, fontsize=8)
    ax.set_xlim(0, max(1, T - 1))


def lmp_heatmap(prices: pd.DataFrame, path: Path, title="日前节点电价（元/MWh）"):
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


def storage_soc(storage: pd.DataFrame, path: Path, prices: pd.DataFrame | None = None, title="储能充放电与荷电状态"):
    units = storage.unit.unique()
    fig, axes = plt.subplots(len(units), 1, figsize=(12, 2.8 * len(units)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, s in zip(axes, units):
        d = storage[storage.unit == s]
        ax.bar(d.period, d.P_dis, color="#9467bd", label="放电 MW")
        ax.bar(d.period, -d.P_ch, color="#c5b0d5", label="充电 MW")
        ax.set_ylabel("MW")
        ax2 = ax.twinx()
        ax2.plot(d.period, d.soc * 100, "k-", lw=1.5, label="荷电状态 %")
        ax2.set_ylim(0, 100)
        ax2.set_ylabel("荷电状态 %")
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
    ax.set_ylabel("节点电价 元/MWh")
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


# ----------------------------------------------------------------------------- 全流程（日前→日内→实时→结算）
SETTLE_LABEL = {"coal": "燃煤", "gas_ccgt": "燃气联合循环", "gas_gt": "燃气轮机", "hydro": "水电", "wind": "风电", "pv": "光伏",
                "storage_dis": "储能放电", "storage_ch": "储能充电", "load": "负荷", "tie": "区外来电", "slack": "平衡松弛"}


def da_vs_rt_lmp(da_prices: np.ndarray, rt_prices: np.ndarray, load_da: np.ndarray, load_rt: np.ndarray, buses, path: Path):
    """上：负荷加权平均 LMP（日前与实时）；中：日前与实时价差最大的 4 个节点；下：RT−DA 价差热力图。"""
    T = rt_prices.shape[1]
    da = da_prices[:, :T]
    wda = (da * load_da[:, :T]).sum(0) / np.maximum(load_da[:, :T].sum(0), 1e-9)
    wrt = (rt_prices * load_rt).sum(0) / np.maximum(load_rt.sum(0), 1e-9)
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), gridspec_kw=dict(height_ratios=[1, 1, 1.3]))
    ax = axes[0]
    ax.plot(range(T), wda, "-", color="#1f77b4", lw=2, label="日前（负荷加权）")
    ax.plot(range(T), wrt, "-", color="#d62728", lw=1.5, label="实时（负荷加权）")
    ax.set_ylabel("元/MWh"); ax.legend(loc="upper left"); ax.grid(alpha=.3)
    ax.set_title("日前与实时节点电价")
    ax = axes[1]
    spread = np.abs(rt_prices - da).max(1)
    top = np.argsort(-spread)[:4]
    for k, i in enumerate(top):
        c = plt.cm.tab10(k)
        ax.plot(range(T), da[i], "--", color=c, lw=1.2, label=f"母线{buses[i]} 日前")
        ax.plot(range(T), rt_prices[i], "-", color=c, lw=1.2, label=f"母线{buses[i]} 实时")
    ax.set_ylabel("元/MWh"); ax.legend(ncol=4, fontsize=8, loc="upper left"); ax.grid(alpha=.3)
    ax.set_title("价差最大的 4 个节点")
    ax = axes[2]
    d = rt_prices - da
    vm = max(1.0, np.percentile(np.abs(d), 99))
    im = ax.imshow(d, aspect="auto", cmap="RdBu_r", vmin=-vm, vmax=vm, interpolation="nearest")
    ax.set_yticks(range(0, len(buses), 2)); ax.set_yticklabels([buses[i] for i in range(0, len(buses), 2)], fontsize=7)
    ax.set_ylabel("母线"); ax.set_title("实时减日前节点电价（元/MWh）")
    fig.colorbar(im, ax=ax, fraction=0.025)
    for a in axes:
        _xt(a, T)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def rolling_plans(id_plans: pd.DataFrame, id_sto: pd.DataFrame, rt_units: pd.DataFrame, rt_sto: pd.DataFrame,
                  da_units: pd.DataFrame, da_sto: pd.DataFrame, path: Path, units=("GT", "CCGT", "H1"), sto="ES1", x=None):
    """（开日内时）各轮日内计划（细线）vs 日前（虚线）vs 实时执行（黑线）；x 给出时画日前 SOC ±x 锚点带。"""
    T = int(rt_units.period.max()) + 1
    rounds = sorted(id_plans.tau.unique())
    cmap = plt.cm.viridis
    panels = list(units) + [f"{sto}:soc"]
    fig, axes = plt.subplots(len(panels), 1, figsize=(13, 3.0 * len(panels)), sharex=True)
    for ax, name in zip(axes, panels):
        if name.endswith(":soc"):
            s = name.split(":")[0]
            for k, tau in enumerate(rounds):
                d = id_sto[(id_sto.tau == tau) & (id_sto.unit == s)]
                ax.plot(d.period, d.E_mwh, color=cmap(k / max(1, len(rounds) - 1)), lw=0.8, alpha=0.8)
            d = da_sto[da_sto.unit == s]
            ax.plot(d.period, d.E_mwh, "--", color="#1f77b4", lw=1.6, label="日前")
            if x:
                ax.fill_between(d.period, (1 - x) * d.E_mwh, (1 + x) * d.E_mwh, color="#1f77b4", alpha=0.15,
                                label=f"锚点带 日前±{x:.0%}（窗口末时段约束）")
            d = rt_sto[rt_sto.unit == s]
            ax.plot(d.period, d.E_mwh, "-", color="k", lw=2, label="实时执行")
            ax.set_ylabel("MWh"); ax.set_title(f"{s} 电量：{'各轮日内计划 / ' if rounds else ''}日前 / 实时执行")
        else:
            for k, tau in enumerate(rounds):
                d = id_plans[(id_plans.tau == tau) & (id_plans.unit == name)]
                ax.plot(d.period, d.P_mw, color=cmap(k / max(1, len(rounds) - 1)), lw=0.8, alpha=0.8)
            d = da_units[da_units.unit == name]
            ax.plot(d.period, d.P_mw, "--", color="#1f77b4", lw=1.6, label="日前")
            d = rt_units[rt_units.unit == name]
            ax.plot(d.period, d.P_mw, "-", color="k", lw=2, label="实时执行")
            ax.set_ylabel("MW"); ax.set_title(f"{name} 出力：{'各轮日内计划（浅→深）/ ' if rounds else ''}日前 / 实时执行")
        ax.grid(alpha=.3); ax.legend(loc="upper left", fontsize=8)
        _xt(ax, 96)
        ax.axvspan(T - 0.5, 95, color="#eeeeee", zorder=0) if T < 96 else None
    if rounds:
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, max(rounds) / 4))
        fig.colorbar(sm, ax=axes, fraction=0.015, label="日内轮次触发时刻（小时）")
    fig.savefig(path, dpi=130, bbox_inches="tight"); plt.close(fig)


def settlement_bars(by_type: pd.DataFrame, by_entity: pd.DataFrame, path: Path):
    """上：按类型的日前结算、实时偏差结算与偏差损益（万元）；下：各主体总结算（万元）。"""
    bt = by_type[by_type.type != "slack"].copy()
    bt["label"] = bt.type.map(SETTLE_LABEL).fillna(bt.type)
    fig, axes = plt.subplots(2, 1, figsize=(13, 10))
    ax = axes[0]
    x = np.arange(len(bt)); w = 0.27
    ax.bar(x - w, bt.da_amount / 1e4, w, label="日前结算（日前电量×日前价格）", color="#1f77b4")
    ax.bar(x, bt.rt_amount / 1e4, w, label="实时偏差结算（偏差电量×实时价格）", color="#ff7f0e")
    ax.bar(x + w, bt.dev_pnl / 1e4, w, label="偏差损益（偏差电量×实时日前价差）", color="#2ca02c")
    ax.set_xticks(x); ax.set_xticklabels(bt.label)
    ax.axhline(0, color="k", lw=.8); ax.set_ylabel("万元（+收入 / −支出）"); ax.legend(); ax.grid(axis="y", alpha=.3)
    ax.set_title("按类型的两级结算")
    ax2 = ax.inset_axes([0.62, 0.08, 0.36, 0.38])
    ax2.bar(x, bt.rt_amount / 1e4, color="#ff7f0e"); ax2.bar(x, bt.dev_pnl / 1e4, 0.4, color="#2ca02c")
    ax2.set_xticks(x); ax2.set_xticklabels(bt.label, fontsize=6, rotation=45); ax2.tick_params(labelsize=6)
    ax2.axhline(0, color="k", lw=.5); ax2.set_title("放大：实时偏差/偏差损益", fontsize=8)
    ax = axes[1]
    be = by_entity[(by_entity.type != "slack") & (by_entity.total.abs() > 1)].sort_values("total")
    cols = [TYPE_COLOR.get(t.replace("storage_dis", "storage").replace("storage_ch", "storage"), "#8c564b") if t != "load" else "#17becf"
            for t in be.type]
    ax.barh(be.entity, be.total / 1e4, color=cols)
    ax.axvline(0, color="k", lw=.8); ax.set_xlabel("万元"); ax.set_title("各主体结算总额（日前+实时偏差）")
    ax.tick_params(axis="y", labelsize=7); ax.grid(axis="x", alpha=.3)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
