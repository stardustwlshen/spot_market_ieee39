"""Current public scope is energy-only; archived regulation is explicit opt-in code."""
from smm.config import load_config
from smm.pipeline import prepare
from smm import scuc


def test_default_model_is_energy_only_without_regulation_guards_or_awards():
    cfg=load_config()
    assert cfg.mode['regulation']['enabled'] is False
    assert cfg.mode['penalties']['line']==10000
    assert cfg.base['line_capacity_multiplier']==1.2
    assert cfg.mode['intraday']['enabled'] is False
    assert cfg.mode['realtime']['hydro']=='value'  # 第二轮D项：水电实时默认价值法
    assert cfg.mode['realtime']['storage']['mode']=='value'  # 第二轮C项：储能/抽蓄实时默认价值法
    assert cfg.mode['realtime']['tracking_policy']['mode']=='soft_deliverable_energy'  # 仅 window_end_da 时生效
    assert cfg.mode['auxiliary_pricing']['enabled'] is True
    assert cfg.mode['reserve']['down_enabled'] is False
    case=prepare(cfg,bid_version=2,read_only=True);m=scuc.build_model(case)
    assert not hasattr(m,'q_agc')
    assert not hasattr(m,'c_agc_requirement')
    assert not hasattr(m,'c_agc_physical')
    assert m._agc=={'enabled':False}
