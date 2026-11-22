"""Immutable identities and separate storage for provincial research regimes.

The profile labels identify rules chosen for the run. Sharing an IEEE39
topology does not confer permission to reuse another regime's market inputs,
cleared values or settlement records.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class MarketProfile:
    id: str
    province: str
    display_name: str
    rules_status: str
    ruleset: str
    results_folder: str

    def identity(self):
        fields = asdict(self)
        payload = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        return {
            **fields,
            'schema': 'ieee39.market_profile.v1',
            'sha256': hashlib.sha256(payload).hexdigest(),
        }

    def default_results(self, package):
        return Path(package).resolve() / self.results_folder


GUANGDONG = MarketProfile(
    'guangdong_research_v3', '广东', '广东历史研究版', 'historical_research',
    '现有广东研究配置（非正式结算）', 'runs',
)
SICHUAN_V5 = MarketProfile(
    'sichuan_v5_consultation', '四川', '四川 V5.0 征求意见稿研究模拟',
    'consultation_draft', '四川电力现货交易实施细则（V5.0 征求意见稿）',
    'runs_sichuan_v5_consultation',
)
DEFAULT = SICHUAN_V5
PROFILES = {profile.id: profile for profile in (GUANGDONG, SICHUAN_V5)}


def identify(record, *, allow_untagged_historical=False):
    """Accept exact identities only; no fallback from a malformed identity."""
    if record is None and allow_untagged_historical:
        return GUANGDONG
    if not isinstance(record, dict):
        raise ValueError('缺少市场规则版本来源')
    profile_id = record.get('id')
    profile = PROFILES.get(profile_id) if isinstance(profile_id, str) else None
    if profile is None or record != profile.identity():
        raise ValueError('市场规则版本来源不一致')
    return profile


def require_profile(record, expected, *, allow_untagged_historical=False):
    """Reject cross-regime use, including legacy inputs declared as Sichuan."""
    actual = identify(record, allow_untagged_historical=allow_untagged_historical)
    if actual != expected:
        raise ValueError(f'市场规则不匹配：来源是「{actual.display_name}」，当前是「{expected.display_name}」')
    return actual
