"""日内滚动 / 实时 SCED / 偏差结算：连续性、功率平衡、结算平衡。"""
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
    assert c["rt_vs_intraday_commitment_mismatch"] == 0  # 实时开机 = 最近一轮日内计划
    assert c["min_updown_violations"] == 0


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
