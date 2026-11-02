import copy
import hashlib
from pathlib import Path

import pytest

from smm.bid_simulation import PRICE_SHIFT_LIMITS, simulate_bids
from smm.bids import default_bids, load_bids, validate_bids, bid_dir
from smm.config import load_config


def test_default_seed_reproducible_without_timestamp_or_file_side_effect():
    cfg=load_config()
    path=bid_dir(cfg)/'bids_v2.json'
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    a=simulate_bids(cfg,selected_unit_ids=['C1','ES1'],seed=17)
    b=simulate_bids(cfg,selected_unit_ids=['ES1','C1'],seed=17)
    assert a==b
    assert 'created' not in a
    assert before==hashlib.sha256(path.read_bytes()).hexdigest()


def test_independent_resource_random_stream_and_different_seed():
    cfg=load_config();base=load_bids(cfg,2)
    a=simulate_bids(cfg,selected_unit_ids=['C1'],seed=17,base_bids=base)
    b=simulate_bids(cfg,selected_unit_ids=['C1','ES1'],seed=17,base_bids=base)
    c=simulate_bids(cfg,selected_unit_ids=['C1'],seed=18,base_bids=base)
    assert a['units']['C1']==b['units']['C1']
    assert a['units']['C1']!=c['units']['C1']


def test_selected_preview_preserves_input_and_unselected_manual_edits():
    cfg=load_config();base=load_bids(cfg,2)
    base['units']['C2']['segments'][2]['price']+=.5
    base['note']='manual research notes'
    original=copy.deepcopy(base)
    result=simulate_bids(cfg,selected_unit_ids=['C1'],seed=3,base_bids=base)
    assert base==original
    assert result['note']==original['note']
    for uid,bid in original['units'].items():
        if uid!='C1':assert result['units'][uid]==bid
    for old,new in zip(base['units']['C1']['segments'],result['units']['C1']['segments']):
        assert old['p_start']==new['p_start'] and old['p_end']==new['p_end']
    assert base['units']['C1']['startup_cost']==result['units']['C1']['startup_cost']


def test_protected_manual_selected_resource_is_rejected_not_skipped():
    cfg=load_config();base=load_bids(cfg,2);original=copy.deepcopy(base)
    with pytest.raises(ValueError,match='受保护.*手工编辑'):
        simulate_bids(cfg,selected_unit_ids=['C1'],protected_unit_ids=['C1'],seed=5,base_bids=base)
    assert base==original


@pytest.mark.parametrize('seed',range(12))
def test_all_actual_types_valid_with_preserved_structure(seed):
    cfg=load_config();base=load_bids(cfg,2)
    out=simulate_bids(cfg,selected_unit_ids=base['units'],seed=seed,base_bids=base)
    assert not [i for i in validate_bids(cfg,out,require_complete=True) if i.level=='error']
    types={c['type'] for c in out['_simulation']['changes']}
    assert types==set(PRICE_SHIFT_LIMITS)
    for change in out['_simulation']['changes']:
        uid=change['unit_id'];shift=change['price_shift_yuan_per_mwh']
        assert shift!=0 and abs(shift)<=PRICE_SHIFT_LIMITS[change['type']]+1e-9
        for curve in change['curves']:
            for a,b in zip(base['units'][uid][curve],out['units'][uid][curve]):
                assert a['p_start']==b['p_start'] and a['p_end']==b['p_end']
                assert b['price']-a['price']==pytest.approx(shift)
        for key,value in base['units'][uid].items():
            if key not in change['curves']:assert out['units'][uid][key]==value


@pytest.mark.parametrize('seed',[True,-1,1.2,'1'])
def test_invalid_seed_rejected(seed):
    with pytest.raises(ValueError,match='seed'):
        simulate_bids(load_config(),selected_unit_ids=['C1'],seed=seed)


@pytest.mark.parametrize('selected',[[],['not-a-unit'],'C1',[1]])
def test_explicit_valid_resource_selection_required(selected):
    with pytest.raises(ValueError):simulate_bids(load_config(),selected_unit_ids=selected,seed=1)


def test_non_price_mode_not_silently_changed():
    cfg=load_config();base=default_bids(cfg);base['units']['ES1']['mode']='quantity_only'
    with pytest.raises(ValueError,match='不会擅自改'):
        simulate_bids(cfg,selected_unit_ids=['ES1'],seed=1,base_bids=base)


def test_invalid_base_is_not_repaired_or_clipped():
    cfg=load_config();base=default_bids(cfg);base['units']['C1']['segments'][-1]['price']=900
    with pytest.raises(ValueError,match='申报限价'):
        simulate_bids(cfg,selected_unit_ids=['C1'],seed=1,base_bids=base)
    assert base['units']['C1']['segments'][-1]['price']==900


def test_fully_pinned_translation_rejected_explicitly():
    cfg=load_config();base=default_bids(cfg);base['units']['C1']['segments'][0]['price']=-50
    base['units']['C1']['segments'][-1]['price']=800
    with pytest.raises(ValueError,match='无法生成非零平移'):
        simulate_bids(cfg,selected_unit_ids=['C1'],seed=1,base_bids=base)
