"""Mutable full-day profiles must be validated before window clipping."""
import pytest
from smm.config import load_config
from smm.case import build_da_case
from smm.bids import default_bids
from smm.forecast import generate_profiles
from smm.network import build_network
from smm.rolling import initial_state, make_window_case

@pytest.mark.parametrize('column,value',[('load_actual',-1.),('W1_actual',1e9),('load_rt',float('nan'))])
def test_corrupted_source_profile_is_not_hidden_by_derived_window(column,value):
    cfg=load_config();profiles=generate_profiles(cfg)
    case=build_da_case(cfg,build_network(cfg),profiles,default_bids(cfg))
    case.profiles.loc[0,column]=value
    with pytest.raises(ValueError,match='profiles'):
        make_window_case(case,cfg,0,8,0,True,initial_state(cfg),{}, {},'RT')
