"""Explicit external bids must not depend on ambient files or mutate callers."""
import copy
import json
from pathlib import Path
import runpy
import shutil
import sys

import pytest
from smm import pipeline
from smm.bids import default_bids, bid_dir
from smm.config import load_config

ROOT = Path(pipeline.__file__).resolve().parents[2]


@pytest.fixture
def isolated_cfg(tmp_path):
    cfg = load_config()
    shutil.copytree(cfg.root / 'data', tmp_path / 'data')
    cfg.root = tmp_path
    return cfg


def external(cfg):
    bids = default_bids(cfg, version=901)
    unit = cfg.units['thermal'][0]['id']
    bids['units'][unit]['segments'][0]['price'] = -45.
    return bids, unit


@pytest.mark.parametrize('ambient', ['missing', 'empty', 'v1'])
@pytest.mark.parametrize('read_only', [True, False])
def test_explicit_bids_are_used_without_loader_or_saved_defaults(isolated_cfg, monkeypatch, ambient, read_only):
    cfg = isolated_cfg
    d = bid_dir(cfg)
    if ambient != 'missing': d.mkdir(parents=True)
    if ambient == 'v1': (d/'bids_v1.json').write_text(json.dumps(default_bids(cfg)))
    bids, unit = external(cfg); original = copy.deepcopy(bids)
    before = {str(p.relative_to(cfg.root)):p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    def unexpected(*a, **kw): raise AssertionError('ambient bid loader/default writer must not run')
    monkeypatch.setattr(pipeline, 'load_bids', unexpected)
    monkeypatch.setattr(pipeline, 'ensure_default_bids', unexpected)
    case = pipeline.prepare(cfg, verbose=False, read_only=read_only, bid_data=bids)
    assert case.bids['version'] == 901
    assert case.bids['units'][unit]['segments'][0]['price'] == -45.
    assert bids == original
    assert sorted(p.name for p in d.glob('bids_v*.json')) == (['bids_v1.json'] if ambient == 'v1' else [])
    if read_only:
        assert before == {str(p.relative_to(cfg.root)):p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('bad', [None, [], {}, {'version':901, 'units':[]}])
def test_explicit_invalid_payload_never_falls_back_or_writes(isolated_cfg, bad):
    cfg = isolated_cfg
    before = {p:p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        pipeline.prepare(cfg, verbose=False, read_only=False, bid_data=bad)
    assert before == {p:p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('version', [None, True, 0, -1, '901', 1.5])
def test_explicit_version_must_be_a_positive_integer(isolated_cfg, version):
    bids, _ = external(isolated_cfg); bids['version'] = version
    with pytest.raises(ValueError, match='version'):
        pipeline.prepare(isolated_cfg, verbose=False, read_only=True, bid_data=bids)


def test_version_conflict_fails_and_matching_version_is_accepted(isolated_cfg):
    bids, _ = external(isolated_cfg)
    with pytest.raises(ValueError, match='冲突'):
        pipeline.prepare(isolated_cfg, 1, verbose=False, read_only=True, bid_data=bids)
    assert pipeline.prepare(isolated_cfg, 901, verbose=False, read_only=True, bid_data=bids).bids['version'] == 901


def test_missing_resource_is_explicitly_filled_without_mutating_source(isolated_cfg):
    bids, _ = external(isolated_cfg); bids['units'].pop('PS1')
    case = pipeline.prepare(isolated_cfg, verbose=False, read_only=True, bid_data=bids)
    assert 'PS1' not in bids['units'] and 'PS1' in case.bids['units']
    assert 'PS1' in case.bids['_default_fill_history']


def test_explicit_call_does_not_change_subsequent_normal_loading(isolated_cfg):
    bids, _ = external(isolated_cfg)
    assert pipeline.prepare(isolated_cfg, verbose=False, read_only=True, bid_data=bids).bids['version'] == 901
    assert pipeline.prepare(isolated_cfg, verbose=False, read_only=True).bids['version'] == 1


def test_main_full_passes_external_payload_when_bid_directory_is_missing(isolated_cfg, monkeypatch, tmp_path):
    from smm import config
    bids, unit = external(isolated_cfg)
    path = tmp_path/'external.json';path.write_text(json.dumps(bids));before=path.read_bytes()
    monkeypatch.setattr(config, 'load_config', lambda **kw: isolated_cfg)
    original_loader = pipeline.load_bids
    monkeypatch.setattr(pipeline, 'load_bids', original_loader)  # restore even buggy CLI global replacement
    real_prepare = pipeline.prepare; seen = {}
    class StopBeforeSolve(Exception): pass
    def prepare(*args, **kwargs):
        seen['case'] = real_prepare(*args, **kwargs)
        raise StopBeforeSolve
    monkeypatch.setattr(pipeline, 'prepare', prepare)
    monkeypatch.setattr(sys, 'argv', ['main_full.py','--bid-file',str(path),'--read-only-inputs','--no-plots'])
    with pytest.raises(StopBeforeSolve): runpy.run_path(str(ROOT/'scripts/main_full.py'),run_name='__main__')
    assert seen['case'].bids['version'] == 901
    assert seen['case'].bids['units'][unit]['segments'][0]['price'] == -45.
    assert pipeline.load_bids is original_loader
    assert path.read_bytes() == before and not bid_dir(isolated_cfg).exists()


def test_output_directory_never_reuses_existing_results(isolated_cfg):
    d = pipeline.out_dir(isolated_cfg, 1, '_review');(d/'summary.json').write_text('original')
    with pytest.raises(FileExistsError): pipeline.out_dir(isolated_cfg, 1, '_review')
    assert (d/'summary.json').read_text() == 'original'


@pytest.mark.parametrize('arguments',[['--tag','../old'],['--tag','/tmp/old'],['--tag',r'..\\old'],['--periods','0']])
def test_main_full_rejects_unsafe_output_arguments_before_inputs(monkeypatch,arguments):
    from smm import config
    monkeypatch.setattr(config,'load_config',lambda **kw:pytest.fail('loaded inputs before CLI rejection'))
    monkeypatch.setattr(sys,'argv',['main_full.py',*arguments])
    with pytest.raises(SystemExit) as exc:runpy.run_path(str(ROOT/'scripts/main_full.py'),run_name='__main__')
    assert exc.value.code==2


@pytest.mark.parametrize('arguments,field',[
 (['--rt-lookahead-h','0'],'lookahead_h'),
 (['--rt-lookahead-h','.3'],'lookahead_h'),
 (['--intraday','--id-interval-h','0'],'interval_h')])
def test_main_full_does_not_ignore_invalid_explicit_clock(isolated_cfg,monkeypatch,arguments,field):
    from smm import config
    monkeypatch.setattr(config,'load_config',lambda **kw:isolated_cfg)
    monkeypatch.setattr(sys,'argv',['main_full.py','--read-only-inputs',*arguments])
    with pytest.raises(ValueError,match=field):runpy.run_path(str(ROOT/'scripts/main_full.py'),run_name='__main__')
