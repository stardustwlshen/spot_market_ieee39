"""Distributed fixed demand, historical supply compatibility, and real SCED checks."""
from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from smm.config import load_config
from smm.forecast import generate_profiles, apply_profile_overrides, forecast_view
from smm.fixed_load import tie_mode, nodal_demand, tie_profiles
from smm.case import build_da_case
from smm.bids import default_bids
from smm.network import build_network
from smm.rolling import make_window_case, initial_state


def inputs():
    cfg = load_config()
    cfg.base.update(tie_line_mode="fixed_load", tie_load_allocation="base_load_share")
    return cfg, build_network(cfg), generate_profiles(cfg)


def test_default_exact_baseline_weight_conversion_net_pressure_and_no_mutation():
    cfg, net, profiles = inputs()
    old = deepcopy(cfg); old.base.pop('tie_line_mode')
    before = deepcopy((cfg, profiles))
    bids = default_bids(cfg); bid_before = deepcopy(bids)
    new = build_da_case(cfg, net, profiles, bids)
    legacy = build_da_case(old, net, profiles, bids)
    assert tie_mode(old) == 'supply' and tie_mode(cfg) == 'fixed_load'
    assert not new.tie and all(k.startswith('TIE@') for k in new.fixed_load)
    v = profiles.TIE_sched.to_numpy()
    for bus in net.buses:
        np.testing.assert_allclose(new.load_bus.get(bus, 0)-legacy.load_bus.get(bus, 0), v*net.load_share[bus], atol=1e-10)
    np.testing.assert_allclose(sum(new.load_bus.values())-(sum(legacy.load_bus.values())-v), 2*v)
    np.testing.assert_allclose(new.R_up-legacy.R_up, cfg.mode['reserve']['rho_L']*v)
    assert cfg.base == before[0].base and cfg.units == before[0].units and bids == bid_before
    pd.testing.assert_frame_equal(profiles, before[1])


def test_four_stages_rt_actual_and_forecast_windows_include_fixed_load_once():
    cfg, net, profiles = inputs()
    for k, stage in enumerate(('actual','da','id','rt')):
        profiles[f'TIE_{stage}'] = np.arange(cfg.T) + 100*(k+1)
    case = build_da_case(cfg, net, profiles, default_bids(cfg))
    base39 = net.load_share[39]*profiles.load_da
    np.testing.assert_allclose(case.load_bus[39]-base39, profiles.TIE_da*net.load_share[39])
    for first_actual, stage in ((True, 'RT'), (False, 'ID')):
        w = make_window_case(case, cfg, 0, 20, 0, first_actual, initial_state(cfg), {}, {}, stage)
        ordinary = forecast_view(profiles, 'load', 0, range(20), first_actual)
        fixed = forecast_view(profiles, 'TIE', 0, range(20), first_actual)
        assert not w.tie
        np.testing.assert_allclose(w.load_bus[39]-net.load_share[39]*ordinary, fixed*net.load_share[39])
        np.testing.assert_allclose(sum(w.load_bus.values()), ordinary+fixed)
        if first_actual: assert sum(v[0] for b,v in w.fixed_load.values()) == pytest.approx(profiles.TIE_actual.iloc[0])
        np.testing.assert_allclose(w.R_up, cfg.mode['reserve']['rho_L']*(ordinary+fixed)+cfg.mode['reserve']['rho_RE']*sum(w.ren_fc.values())+w.reserve_info['R_ctg'])


def test_legacy_schedule_fallback_and_manual_stage_overrides_no_capacity_clip():
    cfg, net, prof = inputs()
    legacy = prof.drop(columns=[f'TIE_{s}' for s in ('actual','da','id','rt')])
    out = tie_profiles(cfg, legacy, cfg.units['tie'][0])
    for values in out.values(): np.testing.assert_array_equal(values, prof.TIE_sched)
    ov = pd.DataFrame({'TIE_actual':np.full(cfg.T, 3000.), 'TIE_id':np.full(cfg.T, 2000.)})
    updated = apply_profile_overrides(cfg, legacy, ov)
    assert (updated.TIE_actual == 3000).all() and (updated.TIE_id == 2000).all()
    np.testing.assert_array_equal(updated.TIE_da, prof.TIE_sched)
    np.testing.assert_array_equal(updated.TIE_rt, prof.TIE_sched)
    with pytest.raises(ValueError, match='四套'):
        tie_profiles(cfg, legacy.assign(TIE_da=1.), cfg.units['tie'][0])
    with pytest.raises(ValueError, match='非负'):
        apply_profile_overrides(cfg, prof, pd.DataFrame({'TIE_rt':[-1.]*cfg.T}))


def test_base_load_scale_does_not_move_or_double_scale_fixed_demand():
    cfg, net, prof = inputs()
    cfg2 = deepcopy(cfg); cfg2.base['load_scale'] *= 1.2
    p2 = generate_profiles(cfg2)
    for stage in ('actual','da','id','rt'):
        np.testing.assert_array_equal(prof[f'TIE_{stage}'], p2[f'TIE_{stage}'])
    # Explicit typical-scenario all-load multiplier scales each component once.
    cols = [f'{name}_{stage}' for name in ('load','TIE') for stage in ('actual','da','id','rt')]
    scaled = apply_profile_overrides(cfg, prof, prof[cols]*1.2)
    original = nodal_demand(cfg, net, prof, prof.load_da)[0]
    changed = nodal_demand(cfg, net, scaled, scaled.load_da)[0]
    for bus in original: np.testing.assert_allclose(changed[bus], original[bus]*1.2)


def test_real_da_rt_solve_records_shed_and_settles_only_node_demand():
    from test_da_stages import tiny_case
    from smm.da import run_da
    from smm import rolling, settlement
    tiny = tiny_case(reserve=0)
    cfg = tiny.cfg
    cfg.base.update(tie_line_mode="fixed_load", tie_load_allocation="base_load_share")
    cfg.units['tie'] = [dict(id='TIE', bus=2, pmax=400)]
    cfg.mode['reserve'].update(rho_L=0., rho_RE=0., rho_L_dn=0., rho_RE_dn=0., include_ctg=False)
    prof = pd.DataFrame({f'{n}_{s}':[v] for n,v in [('load',100.),('TIE',400.)] for s in ('actual','da','id','rt')})
    prof['TIE_sched'] = 400.
    case = build_da_case(cfg, tiny.net, prof, tiny.bids)
    result = run_da(case, verbose=False)
    assert result.tables['system'].load_mw.iloc[0] == 500.
    assert result.tables['system'].shed_mw.iloc[0] >= 100.-1e-6
    assert 'TIE' not in set(result.tables['units'].unit)
    prices = result.tables['prices'].set_index('bus')
    assert prices.loc[2,'load_mw'] == 500 and prices.loc[2,'fixed_load_mw'] == 400
    rr = rolling.run_rolling(result, periods=1, log=lambda *a: None)
    assert rr.rt[0]['snap']['load_bus'][2][0] == 500.
    settled = settlement.settle(result, rr)
    detail = settled['detail']
    assert 'TIE' not in set(detail.entity) and 'tie' not in set(detail.type)
    row = detail.loc[detail.entity == 'L2'].iloc[0]
    assert row.q_rt < 0 and row.total <= 0


def test_topology_does_not_reallocate_fixed_node_load_or_claim_supply(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
    from network_view import network_data, result_snapshot
    from fixed_load_controls import fixed_load_base
    from smm import scenario
    import yaml
    root = tmp_path/'fixed_load'; scenario._copy_tree(load_config().root, root)
    path = root/'config/base.yaml'; base = yaml.safe_load(path.read_text())
    base.update(tie_line_mode='fixed_load', tie_load_allocation='base_load_share')
    path.write_text(yaml.safe_dump(base))
    cfg = load_config(root=root); data = network_data(cfg.root)
    assert 'TIE' not in set(data['resources'].unit)
    assert data['buses'].fixed_load_weight.sum() == pytest.approx(1.)
    snap = result_snapshot(data, {'da_system':pd.DataFrame({'period':[0],'load_mw':[5000.]})}, 'da', 0)
    assert snap['buses'].load_mw.isna().all()
    old = {'load_scale':.68}
    assert fixed_load_base(old) == {'load_scale':.68, 'tie_line_mode':'fixed_load', 'tie_load_allocation':'base_load_share'} and old == {'load_scale':.68}


def test_all_four_stage_allocation_and_provenance_are_conservative():
    from smm.fixed_load import allocation_evidence
    cfg, net, prof = inputs()
    for stage in ('actual','da','id','rt'):
        total, fixed = nodal_demand(cfg, net, prof, prof[f'load_{stage}'], stage=stage)
        np.testing.assert_allclose(sum(v for b,v in fixed.values()), prof[f'TIE_{stage}'], atol=1e-10)
        for bus in net.buses:
            allocated = sum((v for b,v in fixed.values() if b == bus), np.zeros(cfg.T))
            np.testing.assert_allclose(allocated, prof[f'TIE_{stage}']*net.load_share[bus], atol=1e-10)
    case = build_da_case(cfg, net, prof, default_bids(cfg))
    evidence = allocation_evidence(case)
    assert evidence['allocation'] == 'base_load_share'
    assert sum(evidence['weights'].values()) == pytest.approx(1.)
    assert set(evidence['sources'][0]['curves_mw']) == {f'TIE_{s}' for s in ('actual','da','id','rt')}


def test_read_build_and_new_scene_copy_never_rewrite_old_scene_or_snapshot(tmp_path, monkeypatch):
    from smm import scenario
    import yaml
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path/'scenarios')
    new = scenario.create_scenario('new')
    assert load_config(root=new).base['tie_line_mode'] == 'supply'
    old_path = scenario.create_scenario('old')
    path = old_path/'config/base.yaml'
    base = yaml.safe_load(path.read_text()); base.pop('tie_line_mode'); base.pop('tie_load_allocation', None)
    path.write_text(yaml.safe_dump(base))
    # Simulate a separately frozen historical run and verify byte identity.
    import shutil
    frozen = tmp_path/'frozen'; shutil.copytree(old_path, frozen)
    before = {str(p): p.read_bytes() for root in (old_path,frozen) for p in root.rglob('*') if p.is_file()}
    for root in (old_path, frozen):
        cfg = load_config(root=root)
        case = build_da_case(cfg, build_network(cfg), generate_profiles(cfg), default_bids(cfg))
        assert case.tie and not case.fixed_load
    copied = scenario.create_scenario('old_copy', source='old')
    assert tie_mode(load_config(root=copied)) == 'supply'
    assert before == {str(p): p.read_bytes() for root in (old_path,frozen) for p in root.rglob('*') if p.is_file()}
