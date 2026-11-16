import io
import json
import sys
import tarfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from gd_restore_runtime.inspect_backups import digest, inspect


def quarter(tmp_path, q, origin="/original/run", payload=b"state", declaration_q=None):
    manifest = {"window": q if declaration_q is None else declaration_q, "executed_end_exclusive": q + 1, "output": origin, "rt_clock_mode": "provincial_15min_execution", "grid_minutes": 15, "files": [{"member": "state.json", "size": len(payload), "sha256": digest(payload)}]}
    ready = json.dumps(manifest).encode()
    archive = tmp_path / f"q{q}.tar"
    with tarfile.open(archive, "w") as target:
        for name, raw in [("READY.json", ready), ("state.json", payload)]:
            entry = tarfile.TarInfo(name); entry.size = len(raw)
            target.addfile(entry, io.BytesIO(raw))
    raw = archive.read_bytes()
    receipt = tmp_path / f"q{q}.ack"
    receipt.write_text(json.dumps({"schema": "gd.durable.download_verified.v1", "library_file_id": "test-only", "archive_sha256": digest(raw), "archive_bytes": len(raw), "manifest_sha256": digest(ready), "payload_files": 1}))
    return {"archive": str(archive), "receipt": str(receipt), "receipt_sha256": digest(receipt.read_bytes())}


def run(tmp_path, quarters):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema": "gd.backup.catalog.v1", "quarters": quarters}))
    return inspect(catalog, digest(catalog.read_bytes()))


@pytest.mark.parametrize("count", [1, 4, 17, 96])
def test_arbitrary_prefix_is_not_resume_authorization(tmp_path, count):
    result = run(tmp_path, [quarter(tmp_path, q) for q in range(count)])
    assert result["download_verified_prefix_end_exclusive"] == count
    assert result["download_verified_simulation_minutes"] == count * 15
    assert result["computed_progress"] is None and result["model_validation_progress"] is None
    assert result["resume_ready"] is False and result["native_calls"] == 0


def test_gap_does_not_promote_later_quarters(tmp_path):
    result = run(tmp_path, [quarter(tmp_path, 0), None, quarter(tmp_path, 2)])
    assert result["download_verified_prefix_end_exclusive"] == 1
    assert result["first_blocker"]["quarter"] == 1
    assert result["later_slots_not_promoted"] == 2


def test_changed_archive(tmp_path):
    items = [quarter(tmp_path, q) for q in range(3)]
    Path(items[1]["archive"]).write_bytes(b"wrong")
    result = run(tmp_path, items)
    assert result["download_verified_prefix_end_exclusive"] == 1
    assert result["first_blocker"]["reason"] == "archive changed"


def test_changed_ack(tmp_path):
    item = quarter(tmp_path, 0)
    Path(item["receipt"]).write_text("{}")
    result = run(tmp_path, [item])
    assert result["download_verified_prefix_end_exclusive"] == 0
    assert result["first_blocker"]["reason"] == "pinned document changed"


def test_mixed_run(tmp_path):
    result = run(tmp_path, [quarter(tmp_path, 0), quarter(tmp_path, 1, origin="/other")])
    assert result["download_verified_prefix_end_exclusive"] == 1
    assert result["first_blocker"]["reason"] == "mixed original outputs"


def test_wrong_quarter(tmp_path):
    result = run(tmp_path, [quarter(tmp_path, 0, declaration_q=1)])
    assert result["first_blocker"]["reason"] == "noncontiguous executed boundary"


def test_catalog_tamper(tmp_path):
    catalog = tmp_path / "catalog.json"; catalog.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        inspect(catalog, "0" * 64)


@pytest.mark.parametrize("malformed", [None, [], 7, "bad"])
def test_bad_receipt_retains_verified_prefix(tmp_path, malformed):
    items = [quarter(tmp_path, q) for q in range(3)]
    receipt = Path(items[1]["receipt"])
    receipt.write_text(json.dumps(malformed))
    items[1]["receipt_sha256"] = digest(receipt.read_bytes())
    result = run(tmp_path, items)
    assert result["download_verified_prefix_end_exclusive"] == 1
    assert result["first_blocker"] == {"quarter": 1, "reason": "receipt must be an object"}
    assert result["resume_ready"] is False


@pytest.mark.parametrize("malformed", [None, [], 7, "bad"])
def test_bad_catalog_rejected(tmp_path, malformed):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(malformed))
    with pytest.raises(ValueError, match="catalog must be an object"):
        inspect(catalog, digest(catalog.read_bytes()))


@pytest.mark.parametrize("malformed", [[], 7, "bad"])
def test_bad_quarter_retains_prefix(tmp_path, malformed):
    result = run(tmp_path, [quarter(tmp_path, 0), malformed])
    assert result["download_verified_prefix_end_exclusive"] == 1
    assert result["first_blocker"]["reason"] == "quarter entry must be an object"
