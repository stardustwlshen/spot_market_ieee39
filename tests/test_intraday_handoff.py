"""第三轮：日内→实时交接（realtime.handoff_source / handoff / intraday_commit_rt）。"""
import copy
from dataclasses import replace

import numpy as np
import pytest

from smm import rolling, scuc


def _cfg_with(da_result, **rt):
    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    mode["realtime"]["storage"]["mode"] = "value"
    mode["realtime"]["hydro"] = "value"
    mode["auxiliary_pricing"]["enabled"] = False
    mode["realtime"].update(rt)
    return replace(da_result, case=replace(da_result.case, cfg=replace(cfg, mode=mode)))


def test_handoff_config_defaults_and_validation(da_result):
    ho = rolling.handoff_config(_cfg_with(da_result).case.cfg)
    assert ho["source"] == "intraday" and ho["commit_rt"] == "inherit"
    assert all(ho[k] for k in ("commitment", "storage", "pumped", "hydro"))
    with pytest.raises(ValueError):
        rolling.handoff_config(_cfg_with(da_result, handoff_source="foo").case.cfg)
    with pytest.raises(ValueError):
        rolling.handoff_config(_cfg_with(da_result, intraday_commit_rt="x").case.cfg)


def test_intraday_reference_maps_relative_values_to_absolute(da_result, monkeypatch):
    d = _cfg_with(da_result)
    T = d.case.cfg.T
    da_plan = rolling._da_plan_arrays(d)
    m = d.model.clone()
    scuc.solve_sced(m)
    rel = scuc.state_values(m)
    tau = 88
    snap = {k: {u: np.asarray(a)[tau:].copy() for u, a in da_plan[k].items()} for k in ("u", "E", "Vu", "Vh", "delta", "Qout")}
    for kind in ("E", "Vu", "Vh"):
        snap[kind] = {u: a + 1.0 for u, a in snap[kind].items()}
    # This probe reuses a full-day model, but an actual ID model has only T-tau
    # periods. Supply correctly sized solver outputs instead of relying on truncation.
    short = {kind: {u: {key: value[:T-tau] if isinstance(value, np.ndarray) else value
                        for key, value in rec.items()} for u, rec in units.items()} for kind, units in rel.items()}
    monkeypatch.setattr(scuc, "state_values", lambda model: short)
    ref = rolling.intraday_reference(da_plan, da_plan, dict(tau=tau, snap=snap, model=m), T)
    for kind, units in rel.items():
        for u, rec in units.items():
            got = ref["state_values"][kind][u]["value_native"]
            np.testing.assert_allclose(got[:tau], da_plan["state_values"][kind][u]["value_native"][:tau])
            np.testing.assert_allclose(got[tau:], rec["value_native"][:T - tau])
            assert got.shape == (T,)
    for s, arr in snap["E"].items():
        np.testing.assert_allclose(ref["E"][s][tau:], arr)
        np.testing.assert_allclose(ref["E"][s][:tau], da_plan["E"][s][:tau])
    assert ref["reference_source"] == f"intraday@{tau}"
    # 日前计划本身不被修改
    assert not np.allclose(da_plan["E"][next(iter(snap["E"]))][tau:], ref["E"][next(iter(snap["E"]))][tau:])


class _Stop(Exception):
    pass


def _capture(monkeypatch):
    seen = {}

    def fake_vmt(cfg, plan, t, t1, **kw):
        seen["vplan"] = plan
        return dict(state_value=[], state_band=[])

    def fake_case(da_case, c2, t, t1, issue, first, state, fixed, term, stage):
        seen["fixed"] = fixed
        raise _Stop()

    monkeypatch.setattr(rolling, "value_method_terms", fake_vmt)
    monkeypatch.setattr(rolling, "make_window_case", fake_case)
    return seen


@pytest.mark.parametrize("source,id_active,expect_ref", [("intraday", True, True), ("intraday", False, False),
                                                         ("day_ahead", True, False)])
def test_rt_uses_intraday_reference_and_falls_back(da_result, monkeypatch, source, id_active, expect_ref):
    d = _cfg_with(da_result, handoff_source=source)
    cfg = d.case.cfg
    da_plan = rolling._da_plan_arrays(d)
    ref = copy.deepcopy(da_plan)
    # This case tests selection of a valid reference. Keep the perturbation
    # inside physical bounds; out-of-range ID fallback has separate tests.
    bounds = {s["id"]: (s["soc_min"] * s["ecap"], s["soc_max"] * s["ecap"]) for s in cfg.units["storage"]}
    ref["E"] = {s: np.clip(a + 0.5, *bounds[s]) for s, a in ref["E"].items()}
    gt = [u["id"] for u in cfg.units["thermal"] if u["type"] == "gas_gt"]
    plan = copy.deepcopy(da_plan)
    seen = _capture(monkeypatch)
    with pytest.raises(Exception):
        rolling.run_rt_period(d.case, da_plan, plan, cfg, rolling.initial_state(cfg), 0, ref=ref, id_active=id_active)
    assert (seen["vplan"]["E"] is ref["E"]) == expect_ref
    locked = {g for (g, _k) in seen["fixed"]["u"]}
    assert all((g in locked) == expect_ref for g in gt)


def test_per_resource_switch_and_free_gt(da_result, monkeypatch):
    d = _cfg_with(da_result, handoff={"storage": False, "commitment": True}, intraday_commit_rt="free")
    cfg = d.case.cfg
    da_plan = rolling._da_plan_arrays(d)
    ref = copy.deepcopy(da_plan)
    # This case tests selection of a valid reference. Keep the perturbation
    # inside physical bounds; out-of-range ID fallback has separate tests.
    bounds = {s["id"]: (s["soc_min"] * s["ecap"], s["soc_max"] * s["ecap"]) for s in cfg.units["storage"]}
    ref["E"] = {s: np.clip(a + 0.5, *bounds[s]) for s, a in ref["E"].items()}
    seen = _capture(monkeypatch)
    with pytest.raises(Exception):
        rolling.run_rt_period(d.case, da_plan, da_plan, cfg, rolling.initial_state(cfg), 0, ref=ref, id_active=True)
    assert seen["vplan"]["E"] is da_plan["E"]          # 储能开关关：仍用日前
    assert seen["vplan"]["Vh"] is ref["Vh"]            # 水电开关开：用日内
    gt = {u["id"] for u in cfg.units["thermal"] if u["type"] == "gas_gt"}
    assert not gt & {g for (g, _k) in seen["fixed"]["u"]}   # free：实时仍可调 GT
