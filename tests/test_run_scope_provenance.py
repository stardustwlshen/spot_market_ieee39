import json
import pytest
from smm.live_runner import completion_metadata,file_fingerprint,capture_provenance


def test_prefix_computed_settlement_is_not_full_day_settled():
    s=completion_metadata(96,8)
    assert s['completed_scope']=='debug_prefix'
    assert s['settlement_scope']=='illustrative_prefix'
    assert s['settlement_calculated'] and not s['settled']
    assert s['expected_periods']==96 and s['executed_periods']==8
    assert completion_metadata(96,96)['settled']
    assert completion_metadata(96,0,mode='da')['settlement_scope']=='not_applicable'
    assert not completion_metadata(96,8,calculated=False)['settlement_calculated']


def test_fingerprint_is_bounded_content_addressed_and_not_later_state(tmp_path):
    root=tmp_path/'repo';out=tmp_path/'out';out.mkdir()
    for name in ('src','app','scripts','config','data','bids','src/.hidden','src/__pycache__','.venv'):
        (root/name).mkdir(parents=True,exist_ok=True)
    (root/'src/a.py').write_text('print(1)')
    (root/'app/u.py').write_text('x=1')
    (root/'scripts/r.py').write_text('x=2')
    (root/'config/m.yaml').write_text('a: 1')
    (root/'src/.hidden/secret.py').write_text('secret')
    (root/'src/__pycache__/cached.py').write_text('ignore')
    (root/'.venv/v.py').write_text('ignore')
    (root/'src/leak.py').symlink_to(root/'.venv/v.py')
    captured=capture_provenance(out,root,source_root=root)
    assert set(captured['source']['files'])=={'src/a.py','app/u.py','scripts/r.py'}
    assert set(captured['inputs']['files'])=={'config/m.yaml'}
    before=captured['source']['content_fingerprint']
    (root/'src/a.py').write_text('print(2)')
    assert file_fingerprint(root,('src','app','scripts'),suffix='.py')['content_fingerprint']!=before
    saved=json.loads((out/'run_provenance.json').read_text())
    assert saved['source']['content_fingerprint']==before
    assert saved['git_head'] is None

    with pytest.raises(FileExistsError):
        capture_provenance(out,root,source_root=root)


@pytest.mark.parametrize("remove_bids", [False, True])
def test_read_only_prepare_preserves_every_frozen_input_byte(tmp_path, remove_bids):
    import shutil
    from smm.config import load_config
    from smm.live_runner import freeze_inputs
    from smm.pipeline import prepare
    cfg=load_config()
    snap=freeze_inputs(cfg.root,tmp_path)
    if remove_bids:
        shutil.rmtree(snap/'bids')
    # Existing profile may reflect an earlier seed. It must remain untouched.
    old=snap/'data/profiles/old.csv';old.parent.mkdir(parents=True,exist_ok=True)
    old.write_text('historical profile bytes\n')
    frozen=load_config(root=snap)
    before={p.relative_to(snap):p.read_bytes() for p in snap.rglob('*') if p.is_file()}
    evidence=capture_provenance(tmp_path,snap)
    case=prepare(frozen,verbose=False,read_only=True)
    after={p.relative_to(snap):p.read_bytes() for p in snap.rglob('*') if p.is_file()}
    assert before == after
    assert evidence['inputs']==file_fingerprint(snap,('config','data','bids'))
    assert len(case.profiles)==frozen.T
    assert case.profiles.attrs['generation_metadata']['seed']==frozen.base['seed']
    if remove_bids:
        assert case.bids['version']==1
        with pytest.raises(FileNotFoundError):
            prepare(frozen,bid_version=99,verbose=False,read_only=True)


def test_scenario_runner_uses_read_only_preparation():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'scripts/run_scenario.py').read_text()
    assert 'prepare(cfg, a.bid_version, read_only=True)' in source
