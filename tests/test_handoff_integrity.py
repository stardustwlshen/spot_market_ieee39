"""Small deterministic reference probes; no full-day optimization fixture."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from smm import handoff, rolling, scuc, value_outputs
from smm.config import load_config


@pytest.fixture
def context():
    cfg = load_config()
    for h in cfg.units['hydro']:
        h.pop('commitment', None)  # Synthetic aggregate plans have no identified execution history.
    T = cfg.T
    a = lambda x: np.full(T, float(x))
    plan = dict(u={u["id"]: a(1) for u in cfg.units["thermal"]}, E={}, Vu={}, Vh={}, delta={}, Qout={}, Ph={}, state_values={})
    for u in cfg.units["storage"]:
        plan["E"][u["id"]] = a(u["soc0"] * u["ecap"])
    for u in cfg.units["pumped"]:
        plan["Vu"][u["id"]] = a(u["vup0"])
    for u in cfg.units["hydro"]:
        s = u["seasons"][cfg.base["season"]]
        plan["Vh"][u["id"]] = a(s["v0"])
        plan["Qout"][u["id"]] = a(s["qeco"])
        plan["Ph"][u["id"]] = a(0)
        for z in range(len(u["zones"])):
            plan["delta"][(u["id"], z)] = a(0)
    for kind, key in handoff.STATE_KEYS.items():
        plan["state_values"][kind] = {u: dict(value_native=a(-10), value_yuan_per_mwh=a(-10), dual_raw=a(10), native_unit="native") for u in plan[key]}
    return cfg, plan


def id_round(plan, tau):
    snap = {k: {u: np.asarray(v)[tau:].copy() for u, v in values.items()} for k, values in plan.items() if k != "state_values"}
    vals = {kind: {u: {k: np.asarray(v)[tau:].copy() if isinstance(v, np.ndarray) else v for k, v in rec.items()}
                   for u, rec in units.items()} for kind, units in plan["state_values"].items()}
    return dict(tau=tau, snap=snap), vals


@pytest.mark.parametrize("flags", [{"storage": "false"}, {"pumped": 0}, {"storag": True}, None])
def test_bad_flags_rejected(context, flags):
    cfg, _ = context
    cfg.mode["realtime"]["handoff"] = flags
    with pytest.raises(ValueError, match="handoff"):
        handoff.configuration(cfg)


@pytest.mark.parametrize("stage,key,value", [
    ("intraday", "lock_types", ["coal", "gas_gt"]),
    ("realtime", "lock_types", ["unknown"]),
    ("intraday", "free_types", ["hydro"]),
    ("realtime", "free_types", ["gas_gt", "coal"]),
])
def test_invalid_classification_rejected(context, stage, key, value):
    cfg, _ = context
    cfg.mode[stage][key] = value
    with pytest.raises(ValueError):
        handoff.configuration(cfg)


def test_new_round_missing_dual_reverts_bundle_to_da_not_previous_id(context):
    cfg, da = context
    first, vals = id_round(da, 0)
    s = next(iter(da["E"]))
    first["snap"]["E"][s] += 1
    vals["E"][s]["value_native"][:] = -99
    ref = handoff.merge_intraday(da, da, first, cfg.T, vals, cfg=cfg)
    later, _ = id_round(da, 4)
    later["snap"]["E"][s] += 2
    out = handoff.merge_intraday(da, ref, later, cfg.T, {}, cfg=cfg, value_error="missing_dual")
    np.testing.assert_allclose(out["E"][s][:4], da["E"][s][:4] + 1)
    np.testing.assert_allclose(out["E"][s][4:], da["E"][s][4:])
    np.testing.assert_allclose(out["state_values"]["E"][s]["value_native"][4:], -10)
    assert out["reference_metadata"]["E"][s]["value_source"] == "day_ahead"
    assert out["reference_metadata"]["E"][s]["fallback_reason"] == "missing_dual"


@pytest.mark.parametrize("broken", ["missing", "short", "nan", "bounds"])
def test_invalid_id_resource_falls_back_individually(context, broken):
    cfg, da = context
    r, vals = id_round(da, 8)
    s, other = list(da["E"])[:2]
    r["snap"]["E"][other] += 1
    if broken == "missing":
        del vals["E"][s]
    elif broken == "short":
        r["snap"]["E"][s] = r["snap"]["E"][s][:-1]
    elif broken == "nan":
        vals["E"][s]["value_native"][1] = np.nan
    else:
        r["snap"]["E"][s][:] = 1e9
    out = handoff.merge_intraday(da, da, r, cfg.T, vals, cfg=cfg)
    np.testing.assert_allclose(out["E"][s], da["E"][s])
    np.testing.assert_allclose(out["E"][other][8:], da["E"][other][8:] + 1)
    assert out["reference_metadata"]["E"][s]["trajectory_source"] == "day_ahead"
    assert out["reference_metadata"]["E"][other]["trajectory_source"] == "intraday@8"
    np.testing.assert_allclose(da["E"][other], da["E"][other][0])


def test_invalid_da_never_becomes_zero_value(context):
    cfg, da = context
    s = next(iter(da["E"]))
    del da["state_values"]["E"][s]
    with pytest.raises(ValueError, match="state_values"):
        handoff.selected_reference(cfg, da, None, False)


def da_case_probe(cfg):
    # Mirrors DACase's optional source fields for this legacy synthetic-plan
    # probe. No future physical trajectory or real window builder is fabricated.
    from smm.natural_inflow import is_causal
    assert not is_causal(cfg)
    return SimpleNamespace(cfg=cfg, day_start_utc=None, input_provider=None,
                           selected_inputs=None, actual_state_required=False)


def install_window_probe(monkeypatch, clear=None):
    seen = {}
    def case(da_case, cfg, t, t1, issue, first, state, fixed, term, stage, *, selected_inputs=None):
        assert selected_inputs is None  # Explicitly legacy probe, not a causal mock.
        seen.update(fixed=fixed, terminal=term)
        return SimpleNamespace(cfg=cfg, terminal=term, stage=stage)
    def success(case, **kwargs):
        return dict(snap={"hydro_constraints": {}, "value_window": []}, slack={})
    monkeypatch.setattr(rolling, "make_window_case", case)
    monkeypatch.setattr(rolling, "clear_window", clear or success)
    return seen


@pytest.mark.parametrize("source,commit,inherit", [("intraday", "inherit", True), ("day_ahead", "inherit", False), ("intraday", "free", False)])
def test_da_lock_never_reads_latest_id_and_gt_semantics_retained(context, monkeypatch, source, commit, inherit):
    cfg, da = context
    cfg.mode["realtime"].update(handoff_source=source, intraday_commit_rt=commit)
    r, vals = id_round(da, 0)
    for a in r["snap"]["u"].values():
        a[:] = 0
    ref = handoff.merge_intraday(da, da, r, cfg.T, vals, cfg=cfg)
    seen = install_window_probe(monkeypatch)
    result = rolling.run_rt_period(da_case_probe(cfg), da, ref, cfg, rolling.initial_state(cfg), 0, ref=ref, id_active=True)
    for u in cfg.units["thermal"]:
        g = u["id"]
        if u["type"] in cfg.mode["realtime"]["lock_types"]:
            assert seen["fixed"]["u"][g, 0] == 1
            assert result["reference_metadata"]["u"][g]["source"] == "day_ahead"
        else:
            assert ((g, 0) in seen["fixed"]["u"]) == inherit
            if inherit:
                assert seen["fixed"]["u"][g, 0] == 0
    assert set(result["thermal_reference_u"]) == set(result["reference_metadata"]["u"])


def test_resource_switch_provenance_and_missing_gt_fallback(context, monkeypatch):
    cfg, da = context
    cfg.mode["realtime"]["handoff"]["storage"] = False
    r, vals = id_round(da, 0)
    gt = next(u["id"] for u in cfg.units["thermal"] if u["type"] == "gas_gt")
    del r["snap"]["u"][gt]
    ref = handoff.merge_intraday(da, da, r, cfg.T, vals, cfg=cfg)
    seen = install_window_probe(monkeypatch)
    result = rolling.run_rt_period(da_case_probe(cfg), da, ref, cfg, rolling.initial_state(cfg), 0, ref=ref, id_active=True)
    assert seen["fixed"]["u"][gt, 0] == 1
    assert result["reference_metadata"]["u"][gt]["fallback_reason"]
    assert all(m["trajectory_source"] == "day_ahead" for m in result["reference_metadata"]["E"].values())
    assert all(m["trajectory_source"] == "intraday@0" for m in result["reference_metadata"]["Vu"].values())


def test_day_end_uses_da_targets_without_continuation_reward(context):
    cfg, da = context
    ref = copy.deepcopy(da)
    for key in ("E", "Vu"):
        for a in ref[key].values():
            a[-1] += 10
    terms = rolling.value_method_terms(cfg, ref, cfg.T - 8, cfg.T, day_end_plan=da)
    assert all(x[3] == 0 for x in terms["state_value"])
    for kind, u, _, lo, hi, label in terms["state_band"]:
        if kind in ("E", "Vu"):
            assert lo == hi == da[kind][u][-1]


@pytest.mark.parametrize("error", [ValueError("data"), RuntimeError("pricing crashed")])
def test_non_infeasible_errors_never_soften(context, monkeypatch, error):
    cfg, da = context
    calls = []
    def fail(case, **kwargs):
        calls.append(case)
        raise error
    install_window_probe(monkeypatch, fail)
    with pytest.raises(RuntimeError, match=str(error)):
        rolling.run_rt_period(da_case_probe(cfg), da, da, cfg, rolling.initial_state(cfg), 0)
    assert len(calls) == 1


def test_export_uses_resource_source_not_global_label(context, monkeypatch):
    cfg, da = context
    unit = next(iter(da["E"]))
    monkeypatch.setattr(value_outputs, "snapshot", lambda model: da)
    w = dict(kind="E", unit=unit, period=7, value_native=-10., reference_source="day_ahead", value_source="day_ahead",
             reference_state=da["E"][unit][7], label="band", state=da["E"][unit][7], lower=0., upper=1000.,
             lower_binding=False, upper_binding=False, slack_up=0., slack_dn=0., soft=False)
    rr = SimpleNamespace(rt=[dict(t=0, snap={"value_window": [w]}, reference_source="intraday@0", plan_idx=0)], plans=[da])
    table = value_outputs.rt_value_windows(SimpleNamespace(case=da_case_probe(cfg), model=None), rr)
    assert table.iloc[0].reference_source == "day_ahead"
    assert table.iloc[0].state_id is None


def test_hard_infeasible_retry_shares_remaining_budget(context, monkeypatch):
    cfg, da = context
    budgets, softness = [], []
    def clear(case, *, solver_budget):
        budgets.append(solver_budget)
        softness.append(case.terminal.get("state_band_soft", False))
        if len(budgets) == 1:
            solver_budget.used_s += 12
            raise scuc.InfeasibleModelError("hard infeasible", phase="scuc", solver_status="infeasible")
        assert solver_budget.used_s == 12
        return dict(snap={"hydro_constraints": {}, "value_window": []}, slack={"state_band_slack_max": 2})
    install_window_probe(monkeypatch, clear)
    result = rolling.run_rt_period(da_case_probe(cfg), da, da, cfg, rolling.initial_state(cfg), 0)
    assert softness == [False, True] and budgets[0] is budgets[1]
    assert result["fallback"]["band_slack_max"] == 2


@pytest.mark.parametrize("phase", ["auxiliary_pricing", "agc_reservation", None])
def test_other_stage_infeasible_never_softens(context, monkeypatch, phase):
    cfg, da = context
    calls = []
    def clear(case, **kwargs):
        calls.append(case)
        raise scuc.InfeasibleModelError("other stage", phase=phase)
    install_window_probe(monkeypatch, clear)
    with pytest.raises(RuntimeError, match="other stage"):
        rolling.run_rt_period(da_case_probe(cfg), da, da, cfg, rolling.initial_state(cfg), 0)
    assert len(calls) == 1


def test_no_fresh_budget_after_infeasible_exhausts_first_attempt(context, monkeypatch):
    cfg, da = context
    calls = []
    def clear(case, *, solver_budget):
        calls.append(case)
        solver_budget.used_s = solver_budget.total_s
        raise scuc.InfeasibleModelError("hard infeasible", phase="scuc")
    install_window_probe(monkeypatch, clear)
    with pytest.raises(scuc.SolverBudgetExceededError):
        rolling.run_rt_period(da_case_probe(cfg), da, da, cfg, rolling.initial_state(cfg), 0)
    assert len(calls) == 1


def test_cancel_keeps_type_and_never_softens(context, monkeypatch):
    from smm.live_runner import RunCancelled
    cfg, da = context
    calls = []
    def clear(case, **kwargs):
        calls.append(case)
        raise RunCancelled("cancelled")
    install_window_probe(monkeypatch, clear)
    with pytest.raises(RunCancelled):
        rolling.run_rt_period(da_case_probe(cfg), da, da, cfg, rolling.initial_state(cfg), 0)
    assert len(calls) == 1


def test_near_integral_commitment_uses_the_validated_rounded_value(context, monkeypatch):
    cfg, da = context
    r, vals = id_round(da, 0)
    gt = next(u["id"] for u in cfg.units["thermal"] if u["type"] == "gas_gt")
    r["snap"]["u"][gt][:] = 1 - 5e-8
    ref = handoff.merge_intraday(da, da, r, cfg.T, vals, cfg=cfg)
    seen = install_window_probe(monkeypatch)
    result = rolling.run_rt_period(da_case_probe(cfg), da, ref, cfg, rolling.initial_state(cfg), 0, ref=ref, id_active=True)
    assert seen["fixed"]["u"][gt, 0] == 1
    assert result["thermal_reference_u"][gt][0] == 1
    np.testing.assert_array_equal(ref["u"][gt], np.ones(cfg.T))


@pytest.mark.parametrize("key", ["E", "Vu", "Vh", "Qout", "delta", "u"])
def test_missing_da_subject_or_zone_stops_before_solving(context, key):
    cfg, da = context
    del da[key][next(iter(da[key]))]
    with pytest.raises(ValueError, match="day_ahead.*missing_required"):
        handoff.selected_reference(cfg, da, None, False)


@pytest.mark.parametrize("counts", [(100, 0), (3, 3)])
def test_impossible_id_zone_counts_fall_back_as_hydro_bundle(context, counts):
    cfg, da = context
    r, vals = id_round(da, 0)
    unit = next(iter(da["Vh"]))
    for z, count in enumerate(counts):
        r["snap"]["delta"][unit, z][:] = count
    ref = handoff.merge_intraday(da, da, r, cfg.T, vals, cfg=cfg)
    assert ref["reference_metadata"]["V"][unit]["trajectory_source"] == "day_ahead"
    assert ref["reference_metadata"]["V"][unit]["fallback_reason"]
    for jz in da["delta"]:
        np.testing.assert_array_equal(ref["delta"][jz], da["delta"][jz])


@pytest.mark.parametrize("policy",["hard","soft"])
def test_value_soft_retry_keeps_future_day_end_asset_proof(context, monkeypatch, policy):
    cfg, da = context
    cfg.mode["realtime"]["value_method"]["terminal_policy"]={"mode":policy,"penalty_yuan_per_discharge_mwh":1000}
    terms=[]
    def clear(case, **kwargs):
        terms.append(case.terminal)
        if len(terms)==1:
            raise scuc.InfeasibleModelError('hard band',phase='scuc')
        return dict(snap={'hydro_constraints':{},'value_window':[]},slack={})
    install_window_probe(monkeypatch,clear)
    rolling.run_rt_period(da_case_probe(cfg),da,da,cfg,rolling.initial_state(cfg),0)
    assert len(terms)==2 and terms[1]['state_band_soft']
    assert terms[0]['asset_reachability']==terms[1]['asset_reachability']
    assert terms[0]['asset_reachability']['target_policy']==('day_end_soft' if policy=='soft' else 'day_end')
    for key in ('storage_targets','pumped_targets'):
        assert all(len(rows)==1 and rows[0][0]==cfg.T-9 for rows in terms[0]['asset_reachability'][key].values())
