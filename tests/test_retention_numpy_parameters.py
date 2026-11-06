"""Existing physical-input Real support also applies to the cache's source model."""
import numpy as np
from smm import result_retention as retention
from test_retention_value_math import probe


def test_original_numpy_real_parameters_match_original_sced_conversion():
    _,_,row,_=probe();model=row['model']
    for unit in model._md.pumped.values():unit['e_gen']=np.float64(unit['e_gen'])
    for unit in model._md.hydro.values():unit['water_rate']=np.float64(unit['water_rate'])
    record=retention.capture_state_evidence(row)
    assert all(type(v) is float for group in record['conversion_factors'].values() for v in group.values())
    assert retention.detach_id_models(row)['models_retained'] is False
