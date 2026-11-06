"""New research defaults are strict; archived aggregate fixtures stay explicit."""
from pathlib import Path
import yaml
from smm import hydro_commitment as hc, scuc
from smm.config import load_config, ROOT
from smm.case import build_da_case
from smm.network import build_network
from smm.forecast import generate_profiles
from smm.bids import load_bids


def test_new_default_96_period_model_has_identified_obligations():
    cfg=load_config();assert cfg.T==96
    h=cfg.units['hydro'][0]
    assert h['id']=='H1' and hc.enabled(h)
    rows=h['commitment']['initial_units']
    assert [r['online'] for r in rows]==[True,True,False,False]
    assert [r['elapsed_h'] for r in rows]==[1.,1.,1.,1.]
    assert h['init_units_on']*h['init_p_unit']==200.
    case=build_da_case(cfg,build_network(cfg),generate_profiles(cfg),load_bids(cfg))
    m=scuc.build_model(case)
    assert len(m.hydro_commitment.u)==4*96
    assert sum(len(getattr(m.hydro_commitment,n)) for n in ('u','start','stop'))==1152
    assert m.hydro_commitment._initial_state['source']=='research_assumption'
    cfg.units['hydro'][0]['ut']=1.5
    state=hc.initial_state(cfg)
    assert state['stations']['H1']['units']['H1_U01']['elapsed_h']==1.
    assert hc.pending(state)[0]['remaining_h']==.5


def test_h2_remains_disabled_with_explicit_research_declaration():
    raw=yaml.safe_load((ROOT/'config/units.yaml').read_text())
    h=next(h for h in raw['hydro'] if h['id']=='H2')
    assert h['enabled'] is False and h['init_p_unit']==40
    assert h['commitment']['initial_source']=='research_assumption'
    assert all(r['online'] and r['elapsed_h']==1. for r in h['commitment']['initial_units'])
    assert 'H2' not in hc.specs(load_config())
