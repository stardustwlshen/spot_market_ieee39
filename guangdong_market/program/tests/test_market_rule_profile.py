import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from market_rule_profile import DEFAULT, GUANGDONG, SICHUAN_V5, identify, require_profile


def test_sichuan_defaults_to_its_own_research_namespace(tmp_path):
    assert DEFAULT is SICHUAN_V5
    assert DEFAULT.rules_status == 'consultation_draft'
    assert DEFAULT.default_results(tmp_path) != GUANGDONG.default_results(tmp_path)
    assert identify(DEFAULT.identity()) is DEFAULT


def test_untagged_is_never_sichuan_and_bad_record_is_never_legacy():
    assert identify(None, allow_untagged_historical=True) is GUANGDONG
    with pytest.raises(ValueError, match='缺少市场'):
        identify(None)
    with pytest.raises(ValueError, match='市场规则不匹配'):
        require_profile(None, SICHUAN_V5, allow_untagged_historical=True)
    with pytest.raises(ValueError, match='版本来源不一致'):
        identify({**SICHUAN_V5.identity(), 'province': '广东'}, allow_untagged_historical=True)
    with pytest.raises(ValueError, match='版本来源不一致'):
        identify({'id': []}, allow_untagged_historical=True)
