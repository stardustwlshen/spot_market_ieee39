"""Batch orchestration tests use a fake entry point, never market solves."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("safe_k_batch", ROOT / "scripts/k_batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    root = tmp_path / "有 空格的项目"
    (root / "scripts").mkdir(parents=True)
    (root / "data/profiles").mkdir(parents=True)
    (root / "data/profiles/user.csv").write_bytes(b"uncommitted user input\n")
    (root / "outputs/old_result").mkdir(parents=True)
    (root / "outputs/old_result/summary.json").write_bytes(b'{"old": true}')
    (root / "scripts/main_full.py").write_text(
        "import json,sys\n"
        "assert '--read-only-inputs' in sys.argv\n"
        "print(json.dumps(sys.argv))\n"
        "raise SystemExit(7 if '--no-pumped' in sys.argv else 0)\n", encoding="utf-8")
    monkeypatch.setattr(batch, "ROOT", root)
    return root


def test_missing_negative_input_fails_before_launch_or_files(isolated_root, monkeypatch):
    monkeypatch.setattr(batch.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    before = set(isolated_root.rglob("*"))
    with pytest.raises(SystemExit) as exc:
        batch.main([])
    assert exc.value.code == 2
    assert set(isolated_root.rglob("*")) == before


@pytest.mark.parametrize("data", ["{bad", "[]", '{"units": []}'])
def test_invalid_negative_json_fails_without_outputs(isolated_root, data):
    path = isolated_root / "报价.json"
    path.write_text(data, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        batch.main(["--negative-bids", str(path)])
    assert exc.value.code == 2
    assert not (isolated_root / "outputs/k_batches").exists()


def test_dry_run_and_explicit_skip_are_read_only(isolated_root, capsys, monkeypatch):
    monkeypatch.setattr(batch.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    before = set(isolated_root.rglob("*"))
    assert batch.main(["--skip-negative", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["jobs"]) == 9 and "未验证" in report["skipped"][0]
    assert set(isolated_root.rglob("*")) == before


def test_real_subprocess_failures_preserve_inputs_and_old_outputs(isolated_root):
    sentinels = [isolated_root / "data/profiles/user.csv", isolated_root / "outputs/old_result/summary.json"]
    before = {path: path.read_bytes() for path in sentinels}
    assert batch.main(["2", "--skip-negative"]) == 1
    files = list((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(files[0].read_text())
    assert len(report["jobs"]) == 9 and not report["all_processes_succeeded"]
    assert next(r for r in report["jobs"] if r["name"] == "nops")["returncode"] == 7
    for record in report["jobs"]:
        assert "--read-only-inputs" in record["command"]
        assert Path(record["log"]).is_file()
    assert {path: path.read_bytes() for path in sentinels} == before
    assert "data/profiles/user.csv" in report["input_files_sha256"]
    assert report["package_manifest_status"].startswith("historical_only")


def test_repeated_batches_have_unique_tags_and_preserve_logs(isolated_root):
    assert batch.main(["3", "--skip-negative"]) == 1
    before = {p: p.read_bytes() for p in (isolated_root / "outputs/k_batches").rglob("*") if p.is_file()}
    assert batch.main(["3", "--skip-negative"]) == 1
    files = list((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    tags = [{j["output_tag"] for j in json.loads(f.read_text())["jobs"]} for f in files]
    assert len(files) == 2 and not (tags[0] & tags[1])
    assert all(p.read_bytes() == contents for p, contents in before.items())


def test_negative_path_spaces_preserved(isolated_root):
    path = isolated_root / "负价 引用.json"
    path.write_text('{"units": {}}', encoding="utf-8")
    assert batch.main(["--negative-bids", str(path)]) == 1
    manifest = next((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(manifest.read_text())
    record = next(j for j in report["jobs"] if j["name"] == "negprice")
    assert record["command"][record["command"].index("--bid-file") + 1] == str(path)
    assert report["negative_bids_sha256"]


def test_launch_failure_is_recorded(isolated_root, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected launch failure")
    monkeypatch.setattr(batch.subprocess, "Popen", fail)
    assert batch.main(["--skip-negative"]) == 1
    manifest = next((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(manifest.read_text())
    assert len(report["jobs"]) == 9
    assert all(j["status"] == "launch_failed" for j in report["jobs"])


def test_cancellation_terminates_only_owned_child_and_records_status(isolated_root, monkeypatch):
    (isolated_root / "scripts/main_full.py").write_text("import time\ntime.sleep(20)\n")
    monkeypatch.setattr(batch, "job_specs", lambda negative: [("owned", [])])
    original = subprocess.Popen
    started, owned = threading.Event(), []
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        owned.append(process)
        started.set()
        return process
    def interrupt(futures):
        assert started.wait(5)
        raise KeyboardInterrupt
        yield  # Keep the same lazy iteration boundary as as_completed.
    monkeypatch.setattr(batch.subprocess, "Popen", launch)
    monkeypatch.setattr(batch, "as_completed", interrupt)
    assert batch.main(["--skip-negative"]) == 130
    assert len(owned) == 1 and owned[0].poll() is not None
    manifest = next((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(manifest.read_text())
    assert report["interrupted"] and not report["all_processes_succeeded"]


def test_concurrent_input_change_is_detected_not_reverted(isolated_root, monkeypatch):
    (isolated_root / "scripts/main_full.py").write_text(
        "from pathlib import Path\nPath('data/profiles/user.csv').write_text('external edit')\n")
    monkeypatch.setattr(batch, "job_specs", lambda negative: [("change", [])])
    assert batch.main(["--skip-negative"]) == 1
    manifest = next((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(manifest.read_text())
    assert report["all_processes_succeeded"] and not report["source_inputs_unchanged"]
    assert (isolated_root / "data/profiles/user.csv").read_text() == "external edit"


def test_launcher_does_not_call_git_or_use_shared_jobs_file():
    text = (ROOT / "scripts/k_batch.sh").read_text()
    assert "git checkout" not in text and "/tmp/k_jobs" not in text and "/workspace/review_logs" not in text
    result = subprocess.run(["bash", "-n", str(ROOT / "scripts/k_batch.sh")], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_main_full_passes_explicit_read_only_flag():
    tree = ast.parse((ROOT / "scripts/main_full.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "prepare"]
    assert len(calls) == 1
    keyword = next(k for k in calls[0].keywords if k.arg == "read_only")
    assert ast.unparse(keyword.value) == "a.read_only_inputs"


@pytest.mark.parametrize("workers", ["0", "-1"])
def test_invalid_parallelism_rejected(isolated_root, workers):
    with pytest.raises(SystemExit) as exc:
        batch.main([workers, "--skip-negative"])
    assert exc.value.code == 2
    assert not (isolated_root / "outputs/k_batches").exists()


def test_cli_effective_config_is_frozen_and_prior_run_never_replaced(tmp_path):
    from types import SimpleNamespace
    import pandas as pd
    import yaml
    from smm.config import load_config
    from smm.run_input_provenance import freeze_cli_inputs
    cfg = load_config()
    cfg.mode["intraday"]["enabled"] = True
    output = tmp_path / "new_run"
    case = SimpleNamespace(bids={"version": 1, "units": {}}, profiles=pd.DataFrame({"period": [0], "load_da": [10]}))
    snapshot = freeze_cli_inputs(cfg, case, output)
    modes = yaml.safe_load((snapshot / "config/modes.yaml").read_text())
    assert modes[cfg.rule_set]["intraday"]["enabled"] is True
    assert (snapshot / "resolved_bids.json").is_file()
    import hashlib
    manifest = json.loads((snapshot / "source_fingerprint.json").read_text())
    assert manifest['recorded_before_solve'] is True
    assert manifest['sha256']['src/smm/rolling.py'] == hashlib.sha256((ROOT/'src/smm/rolling.py').read_bytes()).hexdigest()
    assert not any('__pycache__' in name for name in manifest['sha256'])
    assert (snapshot / "data/case39/buses.csv").read_bytes() == (cfg.root / "data/case39/buses.csv").read_bytes()
    assert (snapshot / "data/case39/branches.csv").is_file()
    before = {p: p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        freeze_cli_inputs(cfg, case, output)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_external_negative_bid_change_is_detected_not_reverted(isolated_root, monkeypatch):
    negative = isolated_root.parent / "external_bids.json"
    negative.write_text('{"units": {}}')
    (isolated_root / "scripts/main_full.py").write_text(
        f"from pathlib import Path\nPath({str(negative)!r}).write_text('external edit')\n")
    monkeypatch.setattr(batch, "job_specs", lambda path: [("change", [])])
    assert batch.main(["--negative-bids", str(negative)]) == 1
    manifest = next((isolated_root / "outputs/k_batches").glob("*/batch_manifest.json"))
    report = json.loads(manifest.read_text())
    assert report["all_processes_succeeded"] and not report["source_inputs_unchanged"]
    assert report["negative_bids_sha256_after"] != report["negative_bids_sha256"]
    assert negative.read_text() == "external edit"
