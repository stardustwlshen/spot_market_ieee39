import pytest

from app.sichuan_v5_ancillary import Award, prepare_energy_boundary


def test_v5_binding_separates_energy_and_auxiliary_awards_by_quarter():
    holds, disabled = prepare_energy_boundary(
        unit_ids=("C", "S"),
        storage_market={"S": ("spot", "regulation", "reserve", "none")},
        awards=(Award(0, "C", 6, 3, 11), Award(1, "S", 4, 5), Award(2, "S", reserve_up_mw=7)),
        quarters=4,
    )
    assert holds == ({"C": (17, 3)}, {"S": (4, 5)}, {"S": (7, 0)}, {})
    assert disabled == (frozenset(), frozenset({"S"}), frozenset({"S"}), frozenset({"S"}))


@pytest.mark.parametrize("choice, reg, reserve", [("spot", 1, 0), ("spot", 0, 1), ("reserve", 1, 1), ("regulation", 1, 1), ("none", 0, 1)])
def test_v5_storage_never_books_two_products(choice, reg, reserve):
    with pytest.raises(ValueError, match="conflicts with declaration"):
        prepare_energy_boundary(
            unit_ids=("S",), storage_market={"S": (choice,)},
            awards=(Award(0, "S", reg, 0, reserve),), quarters=1,
        )


@pytest.mark.parametrize("awards", [
    (Award(0, "missing"),), (Award(96, "C"),), (Award(-1, "C"),),
    (Award(0, "C", float("nan")),), (Award(0, "C", 0, -1),),
    (Award(0, "C"), Award(0, "C")),
])
def test_v5_rejects_invalid_award_boundaries(awards):
    with pytest.raises(ValueError):
        prepare_energy_boundary(unit_ids=("C",), storage_market={}, awards=awards)
