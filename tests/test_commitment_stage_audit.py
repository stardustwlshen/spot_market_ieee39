"""Commitment audit must apply the lock policy of the stage being checked."""
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from smm import full, regulation, storage_anchors


@pytest.fixture
def stage_evidence(monkeypatch):
    # Isolate commitment checks from unrelated physical/storage audits. No solve.
    monkeypatch.setattr(full, 'execution_safety_checks',
                        lambda *args: {'network_feasible': True, 'safety_tolerance_mw': 1e-5})
    monkeypatch.setattr(full, 'hydro_window_checks', lambda *args: {})
    monkeypatch.setattr(full.rolling, 'snapshot', lambda *args: {})
    monkeypatch.setattr(regulation, 'snapshot', lambda *args: {'enabled': False})
    monkeypatch.setattr(storage_anchors, 'hard_anchor_checks', lambda *args: {})
    units = [dict(id=g, type=kind, init={'online': True, 'hours': 2}, ut=0., dt=0.)
             for g, kind in [('C', 'coal'), ('G', 'gas_ccgt'), ('T', 'gas_gt')]]
    ic = dict(enabled=True, lock_types=['coal'], free_types=['gas_ccgt', 'gas_gt'])
    rc = dict(lock_types=['coal', 'gas_ccgt'], free_types=['gas_gt'],
              storage={'mode': 'sced'}, hydro='sced')
    cfg = NS(T=2, dt=1., mode={'intraday': ic, 'realtime': rc},
             units={'thermal': units, 'storage': [], 'pumped': [], 'hydro': []})
    empty_storage = pd.DataFrame(columns=['unit', 'period'])
    da_units = pd.DataFrame([dict(unit=u['id'], type=u['type'], period=t, on=1, P_mw=0.)
                             for u in units for t in range(2)])
    zero = dict(shed_mwh=0., surplus_mwh=0., line_slack_max=0., reserve_up_short_max=0.,
                reserve_dn_short_max=0., anchor_slack_max_mwh=0.)
    da = NS(case=NS(cfg=cfg, tie={}), model=object(), slack=zero,
            tables={'units': da_units, 'storage': empty_storage})
    # CCGT is allowed to change in ID but remains locked to DA at RT.
    id_u = {'C': np.ones(2), 'G': np.zeros(2), 'T': np.zeros(2)}
    rt_u = {'C': np.ones(1), 'G': np.ones(1), 'T': np.zeros(1)}
    reference = {g: dict(source='day_ahead', round=None) for g in ('C', 'G')}
    reference['T'] = dict(source='intraday@0', round=0)
    r = dict(t=1, snap={'u': rt_u}, slack=zero,
             thermal_reference_u={g: a.copy() for g, a in rt_u.items()},
             reference_metadata={'u': reference},
             handoff_config=dict(source='intraday', commitment=True, commit_rt='inherit',
                                 levels={'intraday': ic, 'realtime': rc}))
    # This probe audits only the execution slice starting at period 1. Supply
    # its actual entry state; do not invent a terminal state or claim a full day.
    initial = full.rolling.SysState(t=1,
        thermal={u['id']:dict(online=True, p0=0., hours=2.) for u in units},
        storage={}, hydro={}, hydro_used={}, pumped={}, hydro_hist={},
        hydro_p={}, hydro_online={}, pumped_used={}, pumped_hist={})
    rr = NS(id_rounds=[{'tau': 0, 'snap': {'u': id_u}}], rt=[r], states=[initial])
    rt = dict(storage=empty_storage,
              units=pd.DataFrame([dict(unit=u['id'], type=u['type'], period=1,
                                       on=rt_u[u['id']][0], P_mw=0.) for u in units]),
              system=pd.DataFrame([dict(period=1, shed_mw=0., surplus_mw=0., load_mw=0.)]),
              prices=pd.DataFrame([dict(period=1, inj_mw=0.)]))
    return da, rr, rt


def test_intraday_free_ccgt_is_not_a_da_lock_violation(stage_evidence):
    da, rr, rt = stage_evidence
    result = full.checks(da, rr, rt)
    assert result['locked_commitment_mismatch_id'] == 0
    assert result['locked_commitment_mismatch_rt'] == 0
    assert result['rt_vs_plan_commitment_mismatch'] == 0
    assert result['thermal_reference_evidence_complete']
    assert result['physical_feasible_checked_constraints']


def test_intraday_own_lock_types_still_catch_illegal_changes(stage_evidence):
    da, rr, rt = stage_evidence
    rr.id_rounds[0]['snap']['u']['C'][1] = 0
    result = full.checks(da, rr, rt)
    # Only the illegal coal change counts, not the two legal ID CCGT changes.
    assert result['locked_commitment_mismatch_id'] == 1
    assert result['locked_commitment_mismatch_rt'] == 0


def test_realtime_own_lock_types_still_catch_illegal_changes(stage_evidence):
    da, rr, rt = stage_evidence
    rt['units'].loc[rt['units'].unit == 'G', 'on'] = 0
    rr.rt[0]['snap']['u']['G'][0] = 0
    result = full.checks(da, rr, rt)
    assert result['locked_commitment_mismatch_id'] == 0
    assert result['locked_commitment_mismatch_rt'] == 1
    assert result['rt_vs_plan_commitment_mismatch'] == 1
    assert not result['physical_feasible_checked_constraints']


def test_original_same_lock_sets_remain_strict(stage_evidence):
    da, rr, rt = stage_evidence
    da.case.cfg.mode['intraday']['lock_types'].append('gas_ccgt')
    da.case.cfg.mode['intraday']['free_types'].remove('gas_ccgt')
    result = full.checks(da, rr, rt)
    assert result['locked_commitment_mismatch_id'] == 2
    assert result['locked_commitment_mismatch_rt'] == 0


def test_stage_probe_does_not_invent_terminal_state(stage_evidence):
    da, rr, rt = stage_evidence
    result = full.checks(da, rr, rt)
    assert result['terminal_pending_obligations'] is None


def test_stage_duration_audit_uses_explicit_slice_initial_state(stage_evidence):
    da, rr, rt = stage_evidence
    gt = next(u for u in da.case.cfg.units['thermal'] if u['id']=='T')
    gt['ut'] = 3.  # cfg history stays 2h; the actual audited slice can differ.
    rr.states[0].thermal['T']['hours'] = 4.
    assert full.checks(da, rr, rt)['min_updown_violations'] == 0
    rr.states[0].thermal['T']['hours'] = 1.
    result = full.checks(da, rr, rt)
    assert result['min_updown_violations'] == 1
    assert not result['physical_feasible_checked_constraints']
