"""Current public scope is energy-only; archived regulation is explicit opt-in code."""
from smm.config import load_config
from smm.pipeline import prepare
from smm import scuc


def test_default_model_is_energy_only_without_regulation_guards_or_awards():
    cfg=load_config()
    assert cfg.mode['regulation']['enabled'] is False
    assert cfg.mode['penalties']['line']==5000
    assert cfg.base['line_capacity_multiplier']==1.2
    assert cfg.mode['intraday']['enabled'] is False
    assert cfg.mode['realtime']['hydro']=='window_end_da'  # Explicitly authorized 2h hard DA terminal-volume policy.
    case=prepare(cfg,bid_version=2);m=scuc.build_model(case)
    assert not hasattr(m,'q_agc')
    assert not hasattr(m,'c_agc_requirement')
    assert not hasattr(m,'c_agc_physical')
    assert m._agc=={'enabled':False}
