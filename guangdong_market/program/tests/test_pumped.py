"""抽水蓄能与水电（库容型/径流式）测试。"""
import copy

import numpy as np
import pytest

from conftest import _short_run


def _ps_cfg(cfg):
    return cfg.units["pumped"][0]


def test_pumped_da_constraints(da_result):
    """日前：不同时抽发、台数限制、库容平衡、库容上下限、申报的日末库容（硬约束）。"""
    cfg = da_result.case.cfg
    p = _ps_cfg(cfg)
    ps = da_result.tables["pumped"]
    ps = ps[ps.unit == p["id"]].sort_values("period")
    assert len(ps) == cfg.T
    assert ((ps.P_gen > 1e-3) & (ps.P_pump > 1e-3)).sum() == 0
    assert (ps.n_gen <= p["n_units"]).all() and (ps.n_pump <= p["n_units"]).all()
    assert (ps.P_gen <= ps.n_gen * p["unit_gen_max"] + 1e-6).all()
    assert (ps.P_gen >= ps.n_gen * p["unit_gen_min"] - 1e-6).all()
    if not p.get("variable_speed"):
        assert np.allclose(ps.P_pump, ps.n_pump * p["unit_pump"], atol=1e-4)   # 定速泵：抽水功率 = 台数 × 额定
    V = np.concatenate([[p["vup0"]], ps.V_up.to_numpy()])
    dV = (ps.P_pump / p["e_pump"] - ps.P_gen / p["e_gen"]).to_numpy() * cfg.dt
    assert np.allclose(np.diff(V), dV, atol=1e-5)
    assert ps.V_up.min() >= p["vup_min"] - 1e-6 and ps.V_up.max() <= p["vup_max"] + 1e-6
    ve = da_result.case.bids["units"][p["id"]].get("vup_end")
    if ve is not None:
        assert abs(ps.V_up.iloc[-1] - ve) < 1e-5
    # 发电量 = 抽水量 × 综合效率（日末回到初值时）
    eg, ep = ps.P_gen.sum() * cfg.dt, ps.P_pump.sum() * cfg.dt
    if ep > 1 and ve is not None and abs(ve - p["vup0"]) < 1e-6:
        assert abs(eg / ep - p["e_gen"] / p["e_pump"]) < 1e-4


def test_pumped_hourly_mode_step(da_result):
    """mode_step_periods=4：每小时内抽/发台数不变（工程简化）。"""
    cfg = da_result.case.cfg
    step = int(cfg.mode["pumped"].get("mode_step_periods", 1))
    ps = da_result.tables["pumped"].sort_values("period")
    if step > 1:
        for col in ("n_gen", "n_pump"):
            a = ps[col].to_numpy().reshape(-1, step)
            assert (a == a[:, :1]).all()


def test_pumped_rules_same_as_storage(da_result):
    """抽蓄市场规则同储能：配置 rules=storage，旧的 pricing/band 键已删除；定价 LP 中抽蓄出力不被固定（正常参与定价）。"""
    from smm import scuc
    cfg = da_result.case.cfg
    pc = cfg.mode["pumped"]
    assert pc.get("rules") == "storage"
    for k in ("pricing", "pricing_eps", "rt_mode", "rt_band_1e4m3", "da_mode", "reserve"):
        assert k not in pc
    assert not hasattr(scuc, "fix_pumped_for_pricing")
    m = da_result.model
    assert not any(m.Pg[i].fixed or m.Pp[i].fixed for i in m.Pg)


def test_pumped_reserve_follows_storage(da_result):
    """备用参与同储能：storage_reserve=false 时抽蓄备用变量为 0。"""
    import pyomo.environ as pyo
    cfg = da_result.case.cfg
    m = da_result.model
    if not cfg.mode["reserve"].get("storage_reserve"):
        assert max(abs(pyo.value(m.rp_up[i])) for i in m.rp_up) < 1e-6


def test_pumped_vup_end_declared_vs_free(da_result):
    """日末上库库容：申报 → 日前硬约束（同储能申报期末 SOC）；不申报（None）→ 不加约束，由出清决定。"""
    from dataclasses import replace
    from smm import scuc
    pid = _ps_cfg(da_result.case.cfg)["id"]
    md = scuc.prepare(da_result.case)
    assert md.pumped[pid]["v_end"] is not None
    bids = copy.deepcopy(da_result.case.bids)
    bids["units"][pid]["vup_end"] = None
    md2 = scuc.prepare(replace(da_result.case, bids=bids))
    assert md2.pumped[pid]["v_end"] is None


def test_pumped_quantity_only_model(da_result):
    """报价 mode=quantity_only（同储能报量不报价；旧名 dispatch 视为同义）：报价不进入目标。"""
    from dataclasses import replace
    from smm import scuc
    cfg = da_result.case.cfg
    pid = _ps_cfg(cfg)["id"]
    for mname in ("quantity_only", "dispatch"):
        bids = copy.deepcopy(da_result.case.bids)
        bids["units"][pid]["mode"] = mname
        md = scuc.prepare(replace(da_result.case, bids=bids))
        d = md.pumped[pid]
        assert d["mode"] == "quantity_only"
        assert max(d["cg"]) <= 0.1 + 1e-9 and max(d["cp"]) == 0.0
        assert len(d["Lg"]) == 1 and d["Lg"][0] == pytest.approx(d["N"] * d["unit_gen_max"])


def test_pumped_rt_default_hourly_eq(full_result):
    """实时默认同储能 B2 hourly_eq：整点上库库容 = 日前；抽/发 0-1 互斥。"""
    da, rt = full_result["da"], full_result["rt"]
    sc = da.case.cfg.mode["realtime"]["storage"]
    assert sc["mode"] == "sced_anchor" and sc["anchor"] == "hourly_eq"
    d = da.tables["pumped"].set_index("period")
    r = rt["pumped"].set_index("period")
    assert ((r.P_gen > 1e-3) & (r.P_pump > 1e-3)).sum() == 0
    for t in r.index:
        if (t + 1) % 4 == 0:
            assert abs(r.loc[t, "V_up"] - d.loc[t, "V_up"]) < 1e-3
    v = r.V_up.to_numpy()
    assert np.allclose(r.V_start.to_numpy()[1:], v[:-1], atol=1e-6)


@pytest.mark.parametrize("sm", ["fixed_da", "window_end_pct"])
def test_pumped_rt_alternatives(da_result, sm):
    """可选实时方式与储能共用开关：fixed_da（A，执行日前曲线）/ window_end_pct（B1，窗口末 ±x）。"""
    from dataclasses import replace
    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    if sm == "fixed_da":
        mode["realtime"]["storage"]["mode"] = "fixed_da"
    else:
        mode["realtime"]["storage"]["anchor"] = "window_end_pct"
    da2 = replace(da_result, case=replace(da_result.case, cfg=replace(cfg, mode=mode)))
    res = _short_run(da2, periods=8)
    d = da_result.tables["pumped"].set_index("period")
    r = res["rt"]["pumped"].set_index("period")
    if sm == "fixed_da":
        assert np.allclose(r.P_gen, d.loc[r.index, "P_gen"], atol=1e-5)
        assert np.allclose(r.P_pump, d.loc[r.index, "P_pump"], atol=1e-5)
    else:
        assert ((r.P_gen > 1e-3) & (r.P_pump > 1e-3)).sum() == 0


def test_pumped_anchors_function():
    """抽蓄锚点与储能锚点同一规则：hourly_eq 取窗口内整点、window_end_pct 取窗口末 ±x（并截到库容上下限）。"""
    from smm.config import load_config
    from smm.rolling import pumped_anchors
    cfg = load_config()
    pid = cfg.units["pumped"][0]["id"]
    Vu = {pid: np.full(cfg.T, 300.0)}
    a = pumped_anchors(cfg, Vu, 0, 8)[pid]
    assert [k for k, _, _ in a] == [3, 7] and all(lo == hi == 300.0 for _, lo, hi in a)
    cfg.mode["realtime"]["storage"]["anchor"] = "window_end_pct"
    a = pumped_anchors(cfg, Vu, 0, 8)[pid]
    x = float(cfg.mode["realtime"]["storage"]["x"])
    assert len(a) == 1 and a[0][0] == 7 and a[0][1] == pytest.approx(300 * (1 - x)) and a[0][2] == pytest.approx(300 * (1 + x))


def test_pumped_settlement_entities(full_result):
    """结算：抽蓄发电、抽水分列两个结算主体（633 号文：抽水电量按现货价格结算）。"""
    be = full_result["settle"]["by_entity"]
    ents = set(be["entity"]) if "entity" in be else set(be.index)
    pid = _ps_cfg(full_result["da"].case.cfg)["id"]
    assert {f"{pid}_发电", f"{pid}_抽水"} <= ents


def test_pumped_bids_rules():
    from smm.bids import default_bids, fill_missing_bids, validate_bids
    from smm.config import load_config
    cfg = load_config()
    b = default_bids(cfg)
    pid = cfg.units["pumped"][0]["id"]
    ub = b["units"][pid]
    assert len(ub["pump_segments"]) == 1                     # 抽水状态单一报价（征求意见稿 6.1）
    assert min(s["price"] for s in ub["gen_segments"]) > ub["pump_segments"][0]["price"]
    assert not [i for i in validate_bids(cfg, b) if i.level == "error"]
    bad = copy.deepcopy(b)
    bad["units"][pid]["pump_segments"][0]["price"] = 500.0   # 抽水价 ≥ 发电首段价 → 违规
    assert [i for i in validate_bids(cfg, bad) if i.level == "error" and i.unit_id == pid]
    miss = copy.deepcopy(b)
    del miss["units"][pid]
    assert pid in fill_missing_bids(cfg, miss)["units"]
    assert ub["vup_end"] == cfg.units["pumped"][0].get("vup_end_bid", cfg.units["pumped"][0]["vup0"])
    bad2 = copy.deepcopy(b)
    bad2["units"][pid]["vup_end"] = 1.0e4                   # 申报日末库容越限 → 违规
    assert [i for i in validate_bids(cfg, bad2) if i.level == "error" and i.rule == "日末库容"]


def test_hydro_reservoir_vs_run_of_river():
    """库容型/径流式开关：径流式不受日电量约束，库容仅日调节池且日末回初值；H2 梯级上游滞时入流。"""
    from smm.bids import default_bids
    from smm.case import build_da_case
    from smm.config import load_config
    from smm.forecast import generate_profiles
    from smm.network import build_network
    from smm import scuc
    cfg = load_config()
    assert cfg.units["hydro"][0]["regulation"] == "reservoir"
    assert "H2" in cfg.units["_disabled"]                    # 可选径流站默认关闭
    import yaml
    from smm.config import filter_enabled
    raw = yaml.safe_load(open(cfg.root / "config" / "units.yaml", encoding="utf-8"))
    for h in raw["hydro"]:
        h["enabled"] = True
    cfg.units["hydro"] = filter_enabled(raw)["hydro"]
    net = build_network(cfg)
    prof = generate_profiles(cfg, cfg.base["seed"])
    case = build_da_case(cfg, net, prof, default_bids(cfg))
    md = scuc.prepare(case)
    h2 = md.hydro["H2"]
    assert h2["regulation"] == "run_of_river" and h2["e_day"] is None
    assert h2["vmin"] == h2["vmax"] == h2["v_end"]           # pondage=0：纯径流
    m = scuc.build_model(case)
    assert "H1" in m.c_eday and "H2" not in m.c_eday        # 日电量约束仅库容型（川V4.0 附件8 约束(16)）
    # 梯级：H2 第 t 时段入流含 H1 在 t−lag 的出库
    lag = int(h2["lag_periods"])
    expr = str(m.c_water["H2", lag + 2].body)
    assert "Ph[H1" in expr or "Ph['H1'" in expr or "Qspill[H1" in expr


def test_run_of_river_default_bid_planned():
    import yaml
    from smm.bids import default_bids
    from smm.config import filter_enabled, load_config
    cfg = load_config()
    raw = yaml.safe_load(open(cfg.root / "config" / "units.yaml", encoding="utf-8"))
    for h in raw["hydro"]:
        h["enabled"] = True
    cfg.units["hydro"] = filter_enabled(raw)["hydro"]
    b = default_bids(cfg)
    assert b["units"]["H2"]["mode"] == "planned" and b["units"]["H1"]["mode"] == "price_quantity"
