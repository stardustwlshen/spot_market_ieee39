import pytest
from multiday_fixture import micro_day
from smm.series import validate_day


def test_missing_spot_profile_does_not_block_dispatch(tmp_path):
    day,_=micro_day();day.cfg.root=tmp_path
    validate_day(day)


def test_invalid_spot_profile_blocks_before_DA(tmp_path):
    day,_=micro_day();day.cfg.root=tmp_path
    (tmp_path/'config').mkdir();(tmp_path/'config/spot_market_costs.yaml').write_text('units: []')
    with pytest.raises(ValueError):validate_day(day)
