"""Current missing-resource warnings are not inherited from an older bid version."""
import copy
import json
from dataclasses import replace
from smm.bids import bid_dir, default_bids, load_bids, save_bids, validate_bids
from smm.config import load_config


def fixture_file(tmp_path, missing=False):
    cfg=replace(load_config(),root=tmp_path)
    cfg.base["default_bid_model"]="legacy_v1"  # Historical missing-resource metadata fixture
    bids=default_bids(cfg,version=3)
    bids['_filled_default']=['PS1']  # historical inherited metadata
    bids['_default_fill_history']=['old_disabled_unit']
    if missing:bids['units'].pop('PS1')
    d=bid_dir(cfg);d.mkdir(parents=True)
    path=d/'bids_v3.json'
    path.write_text(json.dumps(bids),encoding='utf-8')
    return cfg,path,bids


def warnings(cfg,bids):
    return [i for i in validate_bids(cfg,bids) if i.rule=='缺省报价补齐']


def test_complete_old_file_keeps_history_not_false_current_warning(tmp_path):
    cfg,path,raw=fixture_file(tmp_path)
    frozen=path.read_bytes()
    loaded=load_bids(cfg,3)
    assert loaded['units']==raw['units']
    assert '_filled_default' not in loaded
    assert loaded['_default_fill_history']==['old_disabled_unit','PS1']
    assert not warnings(cfg,loaded)
    assert path.read_bytes()==frozen


def test_actual_missing_resource_still_sets_fresh_warning(tmp_path):
    cfg,path,raw=fixture_file(tmp_path,missing=True)
    frozen=path.read_bytes()
    loaded=load_bids(cfg,3)
    assert loaded['_filled_default']==['PS1']
    assert [i.unit_id for i in warnings(cfg,loaded)]==['PS1']
    assert 'PS1' in loaded['units']
    assert path.read_bytes()==frozen


def test_save_load_archives_current_fill_without_changing_quotes_or_input(tmp_path):
    cfg,path,raw=fixture_file(tmp_path,missing=True)
    loaded=load_bids(cfg,3)
    loaded['units']['PS1']['gen_segments'][0]['price']+=10
    # Keep an explicit provenance seed; no metadata migration should alter it.
    loaded['custom_seed']=123
    before=copy.deepcopy(loaded);frozen=path.read_bytes()
    saved=save_bids(cfg,loaded,note='explicit current resource')
    assert loaded==before and path.read_bytes()==frozen
    disk=json.loads(saved.read_text(encoding='utf-8'))
    assert '_filled_default' not in disk
    assert disk['_default_fill_history']==['old_disabled_unit','PS1']
    again=load_bids(cfg,disk['version'])
    assert again['units']==loaded['units']
    assert again['custom_seed']==123
    assert not warnings(cfg,again)
