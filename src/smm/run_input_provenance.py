"""Create a new CLI run's effective-input evidence, never rewrite prior runs."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import shutil
import yaml


def freeze_cli_inputs(cfg, case, output, *, source_options=None):
    """Freeze actual effective inputs and audit the source under one read lock."""
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(cfg.root):
        return _freeze_cli_inputs_unlocked(cfg, case, output, source_options=source_options)


def _freeze_cli_inputs_unlocked(cfg, case, output, *, source_options=None):
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
    from .live_runner import capture_provenance
    source = _cli_source_version(cfg, case, source_options)
    provenance_path = snapshot / 'provenance.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    provenance['frozen_network_verified'] = source.get('frozen_network_verified')
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    capture_provenance(output, snapshot, filename='frozen_input_provenance.json', source_version=source)
    return snapshot


def _cli_source_version(cfg, case, options):
    """Optional source reconciliation must not reject otherwise valid CLI inputs."""
    from .input_version import source_version, bid_digest
    from .config import load_config
    from .pipeline import prepare
    import pandas as pd
    def profile_hash(profiles):
        header = json.dumps([(str(c), str(t)) for c,t in zip(profiles.columns, profiles.dtypes)]).encode()
        values = pd.util.hash_pandas_object(profiles, index=False).to_numpy().tobytes()
        return hashlib.sha256(header + values).hexdigest()
    actual = dict(effective_config=dict(base=cfg.base, units=cfg.units, mode=cfg.mode),
                  effective_bids=bid_digest(case.bids), effective_profiles=profile_hash(case.profiles),
                  frozen_network_verified=None)
    try:
        from .network import build_network
        actual['effective_network'] = _network_digest(case.net)
        # Snapshot reconstructs the effective cfg plus the copied source CSV,
        # which is distinct from an unmodified source cfg's effective network.
        actual['frozen_network_verified'] = actual['effective_network'] == _network_digest(build_network(cfg))
    except (AttributeError, ValueError, KeyError, TypeError, OSError) as exc:
        actual['network_unverified_reason'] = str(exc)
    try:
        original = load_config(root=cfg.root)
        source = source_version(cfg.root, bid_version=case.bids.get('version'),
                                quote_policy='archived', options=options)
        source.update(actual)
        source['effective_config_matches_source'] = (
            cfg.base == original.base and cfg.units == original.units and cfg.mode == original.mode)
        expected = prepare(original, bid_version=case.bids.get('version'), read_only=True, verbose=False)
        source['effective_bids_match_source'] = source['effective_bids'] == source['resolved_bids']
        source['effective_profiles_match_source'] = source['effective_profiles'] == profile_hash(expected.profiles)
        source['effective_network_matches_source'] = source.get('effective_network') == _network_digest(expected.net)
        # Unsaved config, manual CLI bids or injected profiles have no complete
        # source reconciliation contract. Keep exact historical inputs, not equality.
        if not all(source[key] for key in ('effective_config_matches_source',
                                           'effective_bids_match_source', 'effective_profiles_match_source',
                                           'effective_network_matches_source', 'frozen_network_verified')):
            source['run_options'] = None
        return source
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        return {**actual, 'run_options': None, 'source_unverified_reason': str(exc)}


def _network_digest(net):
    """Every Network field consumed by model construction, including matrix bytes."""
    import numpy as np
    import pandas as pd
    from .input_version import digest
    def table_hash(table):
        header = str((list(table.columns), [str(t) for t in table.dtypes])).encode()
        return hashlib.sha256(header + pd.util.hash_pandas_object(table, index=True).to_numpy().tobytes()).hexdigest()
    matrix = np.asarray(net.ptdf)
    return digest(dict(buses=list(net.buses), branches=table_hash(net.branches),
                       ptdf=dict(dtype=str(matrix.dtype), shape=matrix.shape,
                                 sha256=hashlib.sha256(matrix.tobytes()).hexdigest()),
                       load_share=table_hash(net.load_share.to_frame('load_share')),
                       base_load_mw=float(net.base_load_mw), ref_bus=int(net.ref_bus)))
