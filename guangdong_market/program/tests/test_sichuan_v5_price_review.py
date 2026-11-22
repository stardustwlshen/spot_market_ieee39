from decimal import Decimal as D

import pytest

from app.sichuan_v5_price_review import Incident, Vector, choose_rt_price


def vector(stage, k=3, values=None):
    return Vector(stage, k, values if values is not None else {1: D("20"), 2: D("250")}, stage + "-reference")


def test_normal_path_preserves_rt_and_applies_draft_limits_only():
    result = choose_rt_price(3, (1, 2), initial=vector("rt_initial", values={1: D("-5"), 2: D("803")}))
    assert result.source == "rt_initial"
    assert result.nodal_yuan_mwh == {1: D("0.00"), 2: D("800.00")}


def test_valid_recalculation_takes_precedence_over_dayahead():
    result = choose_rt_price(3, (1, 2), rerun=vector("rt_recomputed"),
        incident=Incident("program_fault", "ticket"), same_quarter_da=vector("da_preclear"))
    assert result.source == "rt_recomputed" and result.incident_id == "ticket"


def test_prices_remain_missing_without_documented_rerun_blocker():
    result = choose_rt_price(3, (1, 2), incident=Incident("missing_boundary", "ticket"),
                            same_quarter_da=vector("da_preclear"))
    assert result.source == "withheld" and result.nodal_yuan_mwh is None


def test_same_quarter_da_is_tagged_as_rt_proxy_not_da_cash():
    result = choose_rt_price(3, (1, 2), same_quarter_da=vector("da_preclear"),
        incident=Incident("incorrect_boundary", "ticket", documented_rerun_blocker="synthetic source unavailable"))
    assert result.source == "da_proxy_rt"
    assert result.evidence_id == "da_preclear-reference"


@pytest.mark.parametrize("candidate", [vector("da_preclear", 4),
    vector("da_preclear", values={1: D("1")}),
    vector("da_preclear", values={1: D("NaN"), 2: D(1)}), vector("rt_initial")])
def test_no_proxy_from_wrong_quarter_stage_or_incomplete_data(candidate):
    with pytest.raises(ValueError):
        choose_rt_price(3, (1, 2), incident=Incident("program_fault", "ticket", "synthetic blocker"), same_quarter_da=candidate)


def test_normal_flow_cannot_use_da_on_unknown_rt_result():
    with pytest.raises(ValueError, match="没有价格异常记录"):
        choose_rt_price(3, (1, 2), same_quarter_da=vector("da_preclear"))
    assert choose_rt_price(3, (1, 2)).source == "withheld"
