"""Read-only contiguous external-backup inspection. Does not grant resume permission."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned_json(path, expected):
    data = Path(path).read_bytes()
    require(digest(data) == expected, "pinned document changed")
    return json.loads(data)


def verify_quarter(item, quarter, origin):
    require(isinstance(item, dict), "quarter entry must be an object")
    require(set(item) == {"archive", "receipt", "receipt_sha256"}, "unknown quarter fields")
    receipt = pinned_json(item["receipt"], item["receipt_sha256"])
    require(isinstance(receipt, dict), "receipt must be an object")
    require(receipt.get("schema") == "gd.durable.download_verified.v1", "unverified receipt schema")
    require(bool(receipt.get("library_file_id")), "independent Library receipt absent")
    raw = Path(item["archive"]).read_bytes()
    require(digest(raw) == receipt["archive_sha256"] and len(raw) == receipt["archive_bytes"], "archive changed")
    with tarfile.open(item["archive"]) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        require(all(member.isfile() for member in members), "nonregular archive member")
        require(len(names) == len(set(names)), "duplicate archive member")
        manifest_raw = archive.extractfile("READY.json").read()
        require(digest(manifest_raw) == receipt["manifest_sha256"], "READY changed")
        manifest = json.loads(manifest_raw)
        require(isinstance(manifest, dict), "READY must be an object")
        require(isinstance(manifest.get("files"), list), "payload declarations must be a list")
        require(all(isinstance(entry, dict) for entry in manifest["files"]), "payload declaration must be an object")
        require(manifest["window"] == quarter and manifest["executed_end_exclusive"] == quarter + 1, "noncontiguous executed boundary")
        require(manifest["rt_clock_mode"] == "provincial_15min_execution" and manifest["grid_minutes"] == 15, "wrong execution clock")
        require(origin is None or manifest["output"] == origin, "mixed original outputs")
        require(len(manifest["files"]) == receipt["payload_files"], "payload count mismatch")
        require(set(names) == {"READY.json"} | {entry["member"] for entry in manifest["files"]}, "archive inventory mismatch")
        require(len({entry["member"] for entry in manifest["files"]}) == len(manifest["files"]), "duplicate payload declaration")
        for entry in manifest["files"]:
            payload = archive.extractfile(entry["member"]).read()
            require(len(payload) == entry["size"] and digest(payload) == entry["sha256"], "payload changed")
        return manifest["output"], {"quarter": quarter, "executed_end_exclusive": quarter + 1, "archive_sha256": receipt["archive_sha256"], "library_file_id": receipt["library_file_id"], "payload_files": receipt["payload_files"]}


def inspect(catalog_path, expected_sha):
    catalog = pinned_json(catalog_path, expected_sha)
    require(isinstance(catalog, dict), "catalog must be an object")
    require(set(catalog) == {"schema", "quarters"} and catalog["schema"] == "gd.backup.catalog.v1", "unsupported backup catalog")
    quarters = catalog["quarters"]
    require(isinstance(quarters, list) and 0 < len(quarters) <= 96, "catalog must contain 1 to 96 ordered slots")
    verified = []
    origin = None
    blocker = None
    for quarter, item in enumerate(quarters):
        try:
            require(item is not None, "missing quarter archive or external ACK")
            origin, record = verify_quarter(item, quarter, origin)
            verified.append(record)
        except (ValueError, KeyError, OSError, tarfile.TarError, TypeError) as error:
            blocker = {"quarter": quarter, "reason": str(error)}
            break
    return {
        "schema": "gd.backup.prefix_inspection.v1",
        "catalog_sha256": expected_sha,
        "original_output": origin,
        "download_verified_prefix_end_exclusive": len(verified),
        "download_verified_simulation_minutes": len(verified) * 15,
        "verified_archives": verified,
        "first_blocker": blocker,
        "later_slots_not_promoted": len(quarters) - len(verified),
        "computed_progress": None,
        "model_validation_progress": None,
        "resume_ready": False,
        "scope": "Backup bytes only; complete state, dependency closure, model validation and continuation must be checked separately.",
        "native_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description="只读盘点连续外存备份，不启动求解、不授权续算")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--catalog-sha", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = inspect(args.catalog, args.catalog_sha)
    with Path(args.report).open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
