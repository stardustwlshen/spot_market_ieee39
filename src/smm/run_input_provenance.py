"""Create a new CLI run's effective-input evidence, never rewrite prior runs."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import shutil
import yaml


def freeze_cli_inputs(cfg, case, output):
    output = Path(output)
    # Exclusive creation also closes the preflight race between simultaneous CLIs.
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "scenario_snapshot"
    config = snapshot / "config"
    config.mkdir(parents=True)
    for name, contents in (("base.yaml", cfg.base), ("units.yaml", cfg.units),
                           ("modes.yaml", {cfg.rule_set: cfg.mode})):
        (config / name).write_text(yaml.safe_dump(contents, allow_unicode=True, sort_keys=False), encoding="utf-8")
    network = snapshot / "data/case39"
    network.mkdir(parents=True)
    for name in ("buses.csv", "branches.csv"):
        shutil.copyfile(cfg.root / "data/case39" / name, network / name)
    (snapshot / "resolved_bids.json").write_text(json.dumps(case.bids, ensure_ascii=False, indent=2, default=float) + "\n", encoding="utf-8")
    case.profiles.to_csv(snapshot / "effective_profiles.csv", index=False)
    source_root = Path(__file__).resolve().parents[2]
    source_hashes = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for family in ("src", "app", "scripts", "config")
                     for path in sorted((source_root / family).rglob("*"))
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    (snapshot / "source_fingerprint.json").write_text(json.dumps(dict(
        recorded_at_utc=datetime.now(timezone.utc).isoformat(), recorded_before_solve=True,
        scope="source_app_scripts_default_config;effective_overrides_separately_frozen", sha256=source_hashes),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (snapshot / "provenance.json").write_text(json.dumps(dict(
        source="effective_in_memory_cli_inputs_before_solve", command_line_overrides_included=True,
        completion="inputs_only_not_a_successful_run", frozen_config=True), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return snapshot
