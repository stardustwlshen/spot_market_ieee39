"""One percent DA/RT MIP stopping tolerance must never relax continuous LPs."""
from pathlib import Path
from smm.config import load_config
from smm import scuc,rolling


def test_current_da_rt_gap_defaults_and_existing_budgets():
    cfg=load_config()
    assert cfg.base['solver']['mip_rel_gap']==.01
    assert cfg.mode['realtime']['mip_rel_gap']==.01
    assert cfg.base['solver']['time_limit_s']==600
    assert cfg.mode['realtime']['time_limit_s']==180
    assert cfg.mode['reserve']['T_dur_h']==.25
    assert cfg.mode['intraday']['enabled'] is False


def test_active_scenario_matches_da_rt_gap_and_reserve_policy():
    root=Path(__file__).resolve().parents[1]/'scenarios/当前规则_有界下泄_20261102'
    if not root.exists():return  # standalone source snapshots may omit user scenarios
    cfg=load_config(root=root)
    assert cfg.base['solver']['mip_rel_gap']==.01
    assert cfg.mode['realtime']['mip_rel_gap']==.01
    assert cfg.mode['realtime']['time_limit_s']==180
    assert cfg.mode['reserve']['T_dur_h']==.25


def test_solver_scope_is_mip_only_and_lp_tolerances_untouched():
    cfg=load_config();mip=scuc._solver(cfg);lp=scuc._solver(cfg,mip=False)
    assert mip.highs_options['mip_rel_gap']==.01
    assert 'mip_rel_gap' not in lp.highs_options
    for field in ['primal_feasibility_tolerance','dual_feasibility_tolerance','ipm_optimality_tolerance']:
        assert field not in mip.highs_options and field not in lp.highs_options


def test_rt_subconfig_uses_rt_gap_without_mutating_da_config():
    cfg=load_config();rt=rolling._sub_cfg(cfg,mip_rel_gap=cfg.mode['realtime']['mip_rel_gap'],time_limit_s=180)
    assert scuc._solver(rt).highs_options['mip_rel_gap']==.01
    assert cfg.base['solver']['time_limit_s']==600
    assert scuc._solver(rt,mip=False).highs_options['time_limit']==180
