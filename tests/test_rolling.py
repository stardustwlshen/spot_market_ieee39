"""实时 15 min 滚动 SCED（甘肃式）/ 偏差结算：连续性、锚点、执行日前曲线、功率平衡、结算平衡。"""
import numpy as np
import pytest

from smm.forecast import forecast_view


def test_forecast_view_converges(da_result):
    prof = da_result.case.profiles
    a = prof["load_actual"].to_numpy()
    first = forecast_view(prof, "load", 10, [10, 11], first_actual=True)
    assert first[0] == pytest.approx(a[10])
    # 同一时段：提前量越小越接近实际（日内误差 ≥ 超短期误差的绝对值按构造成立的平均意义）
    far = np.array([abs(forecast_view(prof, "load", t - 20, [t])[0] - a[t]) for t in range(20, 96)]).mean()
    near = np.array([abs(forecast_view(prof, "load", t - 1, [t])[0] - a[t]) for t in range(20, 96)]).mean()
    assert near < far


def test_soc_continuity(full_result):
    c = full_result["checks"]
    assert c["soc_continuity_err_mwh"] < 1e-6
    assert c["soc_equation_err_mwh"] < 1e-6
    assert c["storage_simultaneous"] == 0
    # 日内每轮的初始 SOC = 触发时刻的实际 SOC
    rr = full_result["rr"]
    cfg = full_result["da"].case.cfg
    for r in rr.id_rounds:
        st = rr.states[r["tau"]]
        for s in cfg.units["storage"]:
            sn = r["snap"]
            e1 = st.storage[s["id"]] + (s["eta_c"] * sn["Pc"][s["id"]][0] - sn["Pd"][s["id"]][0] / s["eta_d"]) * cfg.dt
            assert sn["E"][s["id"]][0] == pytest.approx(e1, abs=1e-6)


def test_commitment_continuity(full_result):
    c = full_result["checks"]
    assert c["locked_commitment_mismatch_id"] == 0       # 煤电/CCGT 日内锁定 = 日前
    assert c["locked_commitment_mismatch_rt"] == 0
    assert c["rt_vs_plan_commitment_mismatch"] == 0      # 无日内时参考计划 = 日前
    assert c["min_updown_violations"] == 0
    assert full_result["rr"].id_rounds == []              # 默认不设独立日内


def test_gt_free_and_hydro_follows_da(full_result):
    """GT 启停不锁定（变量在窗口 MIP 中自由）；水电执行日前曲线。"""
    from smm import rolling
    da = full_result["da"]
    cfg = da.case.cfg
    assert "gas_gt" not in cfg.mode["realtime"]["lock_types"]
    assert full_result["checks"]["hydro_vs_da_err_mw"] < 1e-6
    # 直接检查一个窗口：锁定集合只含煤电/CCGT，GT 不在其中
    plan = rolling._da_plan_arrays(da)
    st = rolling.state_from_plan(cfg, plan, 0)
    m = rolling.run_rt_period(da.case, plan, plan, cfg, st, 0)["model"]
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    locked = {g for (g, k) in m._case.fixed["u"]}
    assert locked == {g for g, ty in types.items() if ty in ("coal", "gas_ccgt")}


def _anchor_ok(res, x):
    """每个实时窗口末 E 满足 (1−x)E_DA ≤ E ≤ (1+x)E_DA（与 SOC 上下限求交）。"""
    da = res["da"]
    cfg = da.case.cfg
    E_da = da.tables["storage"].set_index(["unit", "period"]).E_mwh
    H = int(round(cfg.mode["realtime"]["lookahead_h"] * 60 / cfg.mode["realtime"]["interval_min"]))
    worst = 0.0
    for r in res["rr"].rt:
        t1 = min(cfg.T, r["t"] + H)
        for s in cfg.units["storage"]:
            e = float(r["snap"]["E"][s["id"]][t1 - 1 - r["t"]])
            ed = float(E_da[(s["id"], t1 - 1)])
            lo = max(s["soc_min"] * s["ecap"], (1 - x) * ed)
            hi = min(s["soc_max"] * s["ecap"], (1 + x) * ed)
            worst = max(worst, lo - e, e - hi)
    return worst


def test_storage_hourly_eq_default(full_result):
    """模式 B2（默认，广东式）：窗口内整点 E_RT = E_DA，防无解松弛未触发。"""
    da = full_result["da"]
    cfg = da.case.cfg
    sc = cfg.mode["realtime"]["storage"]
    assert sc["mode"] == "sced_anchor" and sc["anchor"] == "hourly_eq"
    E_da = da.tables["storage"].set_index(["unit", "period"]).E_mwh
    n = 0
    for r in full_result["rr"].rt:
        for k in range(len(r["snap"]["surplus"])):
            t = r["t"] + k
            if (t + 1) % 4 == 0:
                for s in cfg.units["storage"]:
                    assert r["snap"]["E"][s["id"]][k] == pytest.approx(float(E_da[(s["id"], t)]), abs=1e-5)
                    n += 1
    assert n > 0
    assert full_result["checks"]["soc_equation_err_mwh"] < 1e-6
    assert full_result["checks"]["anchor_slack_max_mwh"] < 1e-6
    assert full_result["checks"]["anchor_relaxed_periods"] == 0


def test_storage_anchor_window_end(window_end_result):
    """模式 B1（可选，甘V3.2 附录5 第9项）：窗口末 SOC 在日前 ±x 内。"""
    sc = window_end_result["da"].case.cfg.mode["realtime"]["storage"]
    assert sc["anchor"] == "window_end_pct"
    assert _anchor_ok(window_end_result, sc["x"]) < 1e-6
    assert window_end_result["checks"]["anchor_relaxed_periods"] == 0


def test_storage_fixed_da(fixed_da_result):
    """模式 A：储能执行日前充放电曲线。"""
    assert fixed_da_result["checks"]["storage_vs_da_err_mw"] < 1e-6
    assert fixed_da_result["checks"]["rt_balance_err_mw"] < 1e-5


def test_day_end_no_terminal_spike(da_result):
    """日末窗口截断：从日前轨迹状态出发的 23:00 实时窗口，锚点不需松弛，能量价不被期末 SOC 罚因子抬高。"""
    from smm import rolling
    cfg = da_result.case.cfg
    plan = rolling._da_plan_arrays(da_result)
    t = 92
    st = rolling.state_from_plan(cfg, plan, t)
    r = rolling.run_rt_period(da_result.case, plan, plan, cfg, st, t)
    assert r["anchor_relaxed"] == 0
    assert r["slack"]["anchor_slack_max_mwh"] < 1e-6
    # 罚因子 1e5 若进入对偶，λ 会远超出清上限；这里要求原始能量价不超过出清上限
    assert float(np.max(r["prices"]["energy"])) <= cfg.mode["price_limits"]["clear_max"] + 1e-6


def test_rt_power_balance(full_result):
    c = full_result["checks"]
    assert c["rt_balance_err_mw"] < 1e-5
    assert c["rt_injection_sum_err_mw"] < 1e-5
    # RT LMP 分解 π = λ − PTDFᵀ(μ⁺−μ⁻)
    for r in full_result["rr"].rt:
        p = r["prices"]
        assert np.abs(p["lmp"] - p["lmp_decomp"]).max() < 1e-4


def test_settlement_balance(full_result):
    st = full_result["settle"]
    rec = st["reconciliation"].set_index("stage")
    scale = max(1.0, abs(rec.loc["合计", "merchandising_surplus"]))
    assert abs(rec.loc["合计", "residual"]) < 1e-6 * scale
    assert rec.max_power_imbalance_mw.max() < 1e-6
    d = st["detail"]
    # 每个时段日前、实时电量均平衡；联络线无偏差
    assert np.abs(d.groupby("period").q_da.sum()).max() < 1e-6
    assert np.abs(d.groupby("period").q_rt.sum()).max() < 1e-6
    assert np.abs(d[d.type == "tie"].dq).max() < 1e-9
    # 储能充放电分列：放电 q ≥ 0、充电 q ≤ 0
    assert (d[d.type == "storage_dis"].q_rt >= -1e-9).all()
    assert (d[d.type == "storage_ch"].q_rt <= 1e-9).all()
    # 总额 = 日前 + 实时偏差
    assert np.allclose(d.total, d.da_amount + d.rt_amount)
