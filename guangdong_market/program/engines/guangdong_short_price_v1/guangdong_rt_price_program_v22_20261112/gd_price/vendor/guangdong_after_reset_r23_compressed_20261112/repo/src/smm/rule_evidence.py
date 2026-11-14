"""Evidence gate for the Guangdong rebuild, not a market-rule implementation.

The legacy market never calls this module. New orchestrators must explicitly
select a dated, reviewed profile. No default profile or implicit latest-version
selection exists. A pass verifies provenance completeness, not legal compliance,
real telemetry, or numerical correctness of an implementation. Formality and
issuer identity remain reviewed declarations; matching hashes do not authenticate
the issuer or establish that an operational condition is legally satisfied.
"""
from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

SCOPES = frozenset({"energy", "reserve", "regulation", "stage_timing", "pricing",
                    "settlement", "safety", "storage", "pumped", "information_set"})


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: ISO date string required")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid ISO date") from exc
    if result.isoformat() != value:
        raise ValueError(f"{label}: YYYY-MM-DD required")
    return result


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: mapping required")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: nonempty text required")
    return value


def _ids(value, label):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{label}: nonempty identifier list required")
    if len(value) != len(set(value)):
        raise ValueError(f"{label}: duplicate identifiers")
    return value


def verify_local_document(record, path):
    """Verify supplied public-rule bytes only. Never fetch or execute anything."""
    expected = _text(record.get("sha256"), "document.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("document.sha256: lowercase SHA256 required")
    p = Path(path)
    if not p.is_file():
        raise ValueError("document: supplied local file is missing")
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("document: content hash mismatch")
    return {"sha256": actual, "size_bytes": p.stat().st_size}


def assess_profile(catalog, profile_id, *, rule_as_of, scenario_date, condition_evidence):
    """Return explicit blockers for one selected rule profile, without mutation.

    Dates concern rule applicability and the curve's scenario separately. It is
    permissible to simulate a historical curve using a later rule set, but the
    returned scope always labels that as a counterfactual, not historical replay.
    Conditional operation requires dated primary evidence, not a Boolean switch.
    """
    catalog = _mapping(catalog, "catalog")
    if type(catalog.get("schema_version")) is not int or catalog["schema_version"] != 1:
        raise ValueError("catalog.schema_version: unsupported schema")
    documents = _mapping(catalog.get("documents"), "catalog.documents")
    claims = _mapping(catalog.get("claims"), "catalog.claims")
    profiles = _mapping(catalog.get("profiles"), "catalog.profiles")
    _text(profile_id, "profile_id")
    selected = _mapping(profiles.get(profile_id), "selected profile")
    rule_date, curve_date = _date(rule_as_of, "rule_as_of"), _date(scenario_date, "scenario_date")
    condition_evidence = _mapping(condition_evidence, "condition_evidence")
    claim_ids = _ids(selected.get("claim_ids"), "profile.claim_ids")
    required = _ids(selected.get("required_scopes"), "profile.required_scopes")
    if set(required) != SCOPES:
        raise ValueError("profile.required_scopes must cover every declared Guangdong rebuild scope")
    coverage_plans = _mapping(catalog.get("coverage_plans"), "catalog.coverage_plans")
    coverage_id = _text(selected.get("coverage_plan_id"), "profile.coverage_plan_id")
    coverage = _mapping(coverage_plans.get(coverage_id), "selected coverage plan")
    mandatory_claims = _ids(coverage.get("required_claim_ids"), "coverage.required_claim_ids")
    blockers, covered, checked_documents = [], set(), set()

    def block(code, item):
        blockers.append({"code": code, "item": item})

    def document_ok(doc_id, *, check_date=True):
        if not isinstance(doc_id, str) or not doc_id.strip():
            block("invalid_document_identifier", str(doc_id))
            return False
        d = documents.get(doc_id)
        if not isinstance(d, dict):
            block("missing_document", doc_id)
            return False
        start = len(blockers)
        for field in ("title", "issuer", "version", "locator"):
            if not isinstance(d.get(field), str) or not d[field].strip():
                block("missing_document_" + field, doc_id)
        url = urlparse(d.get("source_url", "") if isinstance(d.get("source_url"), str) else "")
        if url.scheme != "https" or not url.netloc:
            block("invalid_source_url", doc_id)
        if d.get("source_kind") != "primary" or d.get("status") != "formal":
            block("not_formal_primary_evidence", doc_id)
        if d.get("content_verified") is not True:
            block("document_content_not_verified", doc_id)
        if not isinstance(d.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", d["sha256"]):
            block("missing_document_hash", doc_id)
        try:
            published = _date(d.get("published_on"), doc_id + ".published_on")
            effective = _date(d.get("effective_from"), doc_id + ".effective_from")
            end = _date(d["effective_until"], doc_id + ".effective_until") if d.get("effective_until") is not None else None
            if end is not None and end <= effective:
                block("invalid_effective_interval", doc_id)
            if check_date and (published > rule_date or effective > rule_date or (end is not None and rule_date >= end)):
                block("document_not_applicable_at_selected_date", doc_id)
        except ValueError:
            block("invalid_document_dates", doc_id)
        checked_documents.add(doc_id)
        return len(blockers) == start

    if coverage.get("review_complete") is not True or not isinstance(coverage.get("review_revision"), str) or not coverage["review_revision"].strip():
        block("coverage_plan_not_reviewed", coverage_id)
    for claim_id in sorted(set(mandatory_claims)-set(claim_ids)):
        block("required_claim_not_selected", claim_id)

    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if not isinstance(claim, dict):
            block("missing_claim", claim_id)
            continue
        scope = claim.get("scope")
        if not isinstance(scope, str) or scope not in SCOPES:
            block("unknown_claim_scope", claim_id)
        if (claim.get("review_status") != "verified"
                or any(not isinstance(claim.get(k), str) or not claim[k].strip()
                       for k in ("implementation_ref", "test_ref"))):
            block("claim_not_implemented_and_reviewed", claim_id)
        for field in ("jurisdiction", "rule_text"):
            if not isinstance(claim.get(field), str) or not claim[field].strip():
                block("missing_claim_" + field, claim_id)
        try:
            _ids(claim.get("applies_to"), claim_id + ".applies_to")
        except ValueError:
            block("missing_claim_applicability", claim_id)
        if not isinstance(claim.get("clause"), str) or not claim["clause"].strip():
            block("missing_clause_locator", claim_id)
        ok = document_ok(claim.get("document_id"))
        if ok and isinstance(scope, str) and scope in SCOPES:
            covered.add(scope)
        if claim.get("operation_conditions_reviewed") is not True:
            block("operation_conditions_not_reviewed", claim_id)
        conditions = claim.get("operation_conditions")
        if not isinstance(conditions, list) or any(not isinstance(c, str) or not c for c in conditions) or len(conditions) != len(set(conditions)):
            block("invalid_operation_conditions", claim_id)
            continue
        for condition in conditions:
            proof = condition_evidence.get(condition)
            if not isinstance(proof, dict) or proof.get("confirmed") is not True:
                block("operation_condition_not_confirmed", condition)
                continue
            if not isinstance(proof.get("clause"), str) or not proof["clause"].strip():
                block("missing_operation_condition_clause", condition)
            document_ok(proof.get("document_id"))
    for scope in sorted(SCOPES-covered):
        block("scope_not_supported_by_applicable_evidence", scope)
    if selected.get("review_complete") is not True:
        block("profile_review_incomplete", profile_id)
    if not isinstance(selected.get("data_scope"), str) or selected["data_scope"] not in {"synthetic_case", "verified_operational_data"}:
        block("data_scope_not_explicit", profile_id)
    # Stable deduplication, preserving the first concrete explanation.
    blockers = list({(b["code"], str(b["item"])): b for b in blockers}.values())
    return {"evidence_gate_passed": not blockers, "profile_id": profile_id,
            "rule_as_of": rule_date.isoformat(), "scenario_date": curve_date.isoformat(),
            "same_rule_and_scenario_date": curve_date == rule_date,
            "historical_replay_certified": False,
            "date_scope": "same_date" if curve_date == rule_date else "counterfactual_rule_date_not_historical_replay",
            "data_scope": selected.get("data_scope"), "documents_checked": sorted(checked_documents),
            "blockers": blockers, "local_document_hashes_checked": False, "attestation_scope": "declared_metadata_only_not_legal_or_model_compliance"}


def require_profile(catalog, profile_id, *, rule_as_of, scenario_date, condition_evidence, document_paths=None):
    """Runtime gate additionally checks every selected public document's bytes."""
    result = assess_profile(catalog, profile_id, rule_as_of=rule_as_of,
        scenario_date=scenario_date, condition_evidence=condition_evidence)
    if not result["evidence_gate_passed"]:
        raise ValueError("Rule profile blocked: " + "; ".join(f"{r['code']}:{r['item']}" for r in result["blockers"]))
    paths = _mapping(document_paths, "local public document paths")
    hashes = {}
    for doc_id in result["documents_checked"]:
        if doc_id not in paths:
            raise ValueError(f"Rule profile missing local document: {doc_id}")
        hashes[doc_id] = verify_local_document(catalog["documents"][doc_id], paths[doc_id])
    result.update(local_document_hashes_checked=True, verified_documents=hashes,
        attestation_scope="reviewed_metadata_and_matching_source_bytes_only_not_legal_or_model_compliance")
    return result
