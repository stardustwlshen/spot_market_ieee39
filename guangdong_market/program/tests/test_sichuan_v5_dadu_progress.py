"""Progress is evidence of completed solver work; no estimate enters model inputs."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from sichuan_v5_dadu_progress import source_hashes_match
from sichuan_v5_dadu_policy import operator_hydro_envelope
import sichuan_v5_company as company
import sichuan_v5_energy as energy
from test_sichuan_v5_dadu_envelope import two_stations


def test_two_station_checks_count_completed_solves_and_cache_hits(monkeypatch):
    from collections import OrderedDict
    monkeypatch.setattr(company, "_CAPABILITY_CACHE", OrderedDict())
    case = two_stations()
    prices = [{"U": 200., "D": 200.} for _ in range(96)]
    values = {"U": .01, "D": .01}
    events = []
    first = company.prepare_company_submission(case, prices, values, progress=events.append)
    assert all(isinstance(e, str) and e.stage == "company" and e.total == 2 for e in events)
    assert [e.completed for e in events] == sorted(e.completed for e in events)
    assert {e.completed for e in events} == {0, 1, 2}
    assert any("线性规划" in e.label and e.completed < 2 for e in events)
    cache_events = []
    same = company.prepare_company_submission(case, prices, values, progress=cache_events.append)
    assert same == first
    assert cache_events[-1].completed == 2 and "复用" in cache_events[-1].label
    assert not any("线性规划" in e.label for e in cache_events)

    checks = []
    with_progress = operator_hydro_envelope(case, progress=checks.append)
    assert with_progress == operator_hydro_envelope(case)
    assert [e.completed for e in checks] == list(range(98))
    assert all(e.total == 97 and e.stage == "market" for e in checks)


def test_failed_commitment_does_not_report_sced(monkeypatch):
    case = replace(two_stations(), demand=tuple({1: .5} for _ in range(96)))
    phases = []

    def fail(*args, **kwargs):
        raise energy.ClearingError("injected failure")

    monkeypatch.setattr(energy, "_solve", fail)
    with pytest.raises(energy.ClearingError, match="injected failure"):
        energy.day_ahead(case, on_commitment_solved=lambda: phases.append("sced"))
    assert phases == []
    monkeypatch.setattr(energy, "_solve", lambda *args, **kwargs: phases.append("scuc"))
    monkeypatch.setattr(energy, "_pricing", fail)
    with pytest.raises(energy.ClearingError, match="injected failure"):
        energy.day_ahead(case, on_commitment_solved=lambda: phases.append("sced"))
    assert phases == ["scuc", "sced"]


def test_source_compatibility_only_waives_the_reviewed_telemetry_revision():
    path = "app/sichuan_v5_company.py"
    archived = {path: "20ce4656e4cf1bc87b1a3b0ec2facc7398687564d00c9708e7d8d2bfa1ec7423",
                "data/hydro.csv": "untouched"}
    instrumented = {**archived, path: "010bddcc13729aacba0c40c349c5277fac2c4fee3a618cd2fde76020d1d89e89"}
    assert source_hashes_match(archived, archived)
    assert source_hashes_match(archived, instrumented)
    assert source_hashes_match(instrumented, archived)
    assert not source_hashes_match(archived, {**instrumented, path: "other"})
    assert not source_hashes_match(archived, {**instrumented, "data/hydro.csv": "other"})
    assert not source_hashes_match(archived, {path: instrumented[path]})
    assert not source_hashes_match(None, None)


def test_matrix_keeps_versions_and_signed_boundaries_separate():
    from collections import defaultdict
    from copy import deepcopy
    from hashlib import sha256
    from test_sichuan_v5_dadu_acceptance import _record_cohort, _source_version

    path = "app/sichuan_v5_company.py"
    old = {path: "20ce4656e4cf1bc87b1a3b0ec2facc7398687564d00c9708e7d8d2bfa1ec7423"}
    new = {path: "010bddcc13729aacba0c40c349c5277fac2c4fee3a618cd2fde76020d1d89e89"}
    version = _source_version("market", "hydro", new)
    run = {"inputs": dict(market_base_sha="market", hydro_source_sha="hydro", source_hashes=old,
                          scenario="wet_low", seed=39, periods=96, boundary_hash="da",
                          assumptions={"rules_pdf_sha256": "8e8ee5bebadb0f0ae1873315790b47ed8694a68b8ca4d1e160ec669e74c42ad5"},
                          water_value_scale=1., corridor_scale=1., gas_plan_scale=1.),
           "company": dict(strategy="naive", submission_sha256="bid1", company_forecast_sha256="forecast"),
           "summary": {"boundary_hash": "da", "issued_boundary_hash": sha256(b"[]").hexdigest()},
           "issued_forecasts": []}
    pairs = defaultdict(dict)
    _record_cohort(pairs, run, version)
    other = deepcopy(run)
    other["inputs"]["source_hashes"] = new
    other["company"].update(strategy="water_value", submission_sha256="bid2")
    _record_cohort(pairs, other, version)
    assert list(pairs.values()) == [{"naive": "bid1", "water_value": "bid2"}]

    for area, field, value in (("inputs", "market_base_sha", "unknown-market"),
                                ("inputs", "source_hashes", {path: "different-code"}),
                                ("inputs", "assumptions", {"rules_pdf_sha256":
                                 "05cda5cabc552a8c8b8e5a58686f3a79684d8e24bf97255d00d8b3246d16e0b5"}),
                                ("company", "company_forecast_sha256", "other-forecast")):
        mismatch = deepcopy(other)
        mismatch[area][field] = value
        separate = defaultdict(dict)
        _record_cohort(separate, run, version)
        _record_cohort(separate, mismatch, version)
        assert len(separate) == 2

    for area, field in (("summary", "issued_boundary_hash"), ("company", "company_forecast_sha256")):
        mismatch = deepcopy(other)
        del mismatch[area][field]
        missing = defaultdict(dict)
        _record_cohort(missing, mismatch, version)
        assert not missing

    # Matching snapshots and bids still require evidence of the rules document on both sides.
    for assumptions in (None, {}, {"rules_pdf_sha256": ""}, {"rules_pdf_sha256": "unknown"}):
        unidentified = defaultdict(dict)
        for known in (run, other):
            copy = deepcopy(known)
            if assumptions is None:
                del copy["inputs"]["assumptions"]
            else:
                copy["inputs"]["assumptions"] = assumptions
            _record_cohort(unidentified, copy, version)
        assert not unidentified
