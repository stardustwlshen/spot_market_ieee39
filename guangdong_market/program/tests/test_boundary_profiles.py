import numpy as np
import pandas as pd
import pytest
from smm.config import load_config
from smm.forecast import generate_profiles,apply_profile_overrides


def test_full_stage_boundary_columns_are_used_without_changing_others():
    c=load_config();base=generate_profiles(c);cols=[f'{r["id"]}_{s}' for r in c.units['renewable'] for s in ('actual','da','id','rt')]
    override=base[cols]*.7
    result=apply_profile_overrides(c,base,override)
    np.testing.assert_array_equal(result[cols],override)
    pd.testing.assert_frame_equal(result.drop(columns=cols),base.drop(columns=cols))


def test_load_all_stages_scale_exactly_and_preserve_renewables():
    c=load_config();base=generate_profiles(c);cols=[f'load_{s}' for s in ('actual','da','id','rt')]
    result=apply_profile_overrides(c,base,base[cols]*1.1)
    np.testing.assert_array_equal(result[cols],base[cols]*1.1)
    pd.testing.assert_frame_equal(result.drop(columns=cols),base.drop(columns=cols))


@pytest.mark.parametrize('bad',[np.nan,np.inf,-1.,1041.])
def test_invalid_explicit_rt_boundary_fails_closed(bad):
    c=load_config();base=generate_profiles(c);override=base[['W1_rt']].copy();override.iloc[0,0]=bad
    with pytest.raises(ValueError,match='profile_overrides W1_rt'):
        apply_profile_overrides(c,base,override)


def test_no_override_and_da_only_preserve_old_path(tmp_path):
    c=load_config(root=None);c.root=tmp_path;base=generate_profiles(c)
    pd.testing.assert_frame_equal(apply_profile_overrides(c,base),base)
    override=base[['W1_da']]*.7;result=apply_profile_overrides(c,base,override)
    np.testing.assert_array_equal(result.W1_da,override.W1_da)
    pd.testing.assert_frame_equal(result.drop(columns='W1_da'),base.drop(columns='W1_da'))
