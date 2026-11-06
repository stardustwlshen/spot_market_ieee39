"""Bid-only policy and fail-closed API checks; no optimization required."""
import copy
import json
from dataclasses import replace

import pytest

from smm.bids import default_bids, validate_bids, load_bids, save_bids, bid_dir
from smm.config import load_config


def errors(cfg, bids):
    return [i for i in validate_bids(cfg, bids) if i.level == 'error']


def test_default_profile_preserves_generated_quantities_and_prices():
    cfg = load_config()
    cfg.base['default_bid_model']='legacy_v1'  # Historical fixed-price profile compatibility
    old = copy.deepcopy(cfg)
    old.mode['price_limits']['bid_min'] = 0
    old.mode['bid_rules'] = {'profile': 'generic'}
    assert default_bids(cfg)['units'] == default_bids(old)['units']
    assert not errors(cfg, default_bids(cfg))
    current = load_bids(cfg, 2)
    assert not errors(cfg, current)
    assert current.get('_filled_default')  # explicit missing-resource provenance
    assert any(i.rule == '缺省报价补齐' for i in validate_bids(cfg, current))


def test_thermal_reference_floor_equal_prices_and_no_legacy_step():
    cfg = load_config(); bids = default_bids(cfg)
    segs = bids['units']['C1']['segments']
    segs[0]['price'] = -50
    segs[1]['price'] = -49  # not a 10-yuan step; accepted in reference thermal profile
    segs[2]['price'] = -49  # nondecreasing, not strictly increasing
    segs[-1]['price'] = 800
    assert not errors(cfg, bids)
    segs[0]['price'] = -50.01
    assert any(i.rule == '申报限价' for i in errors(cfg, bids))
    segs[0]['price'] = -50
    segs[-1]['price'] = 800.01
    assert any(i.rule == '申报限价' for i in errors(cfg, bids))


def test_negative_market_price_cannot_undercut_internal_base():
    cfg = load_config(); bids = default_bids(cfg)
    bids['units']['C1']['segments'][1]['price'] = -10
    assert any(i.rule == '价格单调' for i in errors(cfg, bids))


def test_settlement_cap_not_a_bid_cap_and_type_override_is_independent():
    cfg = load_config(); bids = default_bids(cfg)
    cfg.mode['price_limits']['clear_max'] = 50
    assert not errors(cfg, bids)
    cfg.mode['bid_rules']['by_type'] = {'coal': {'price_max': 300}}
    assert any(i.unit_id == 'C1' and i.rule == '申报限价' for i in errors(cfg, bids))
    assert not any(i.unit_id == 'GT' and i.rule == '申报限价' for i in errors(cfg, bids))


def test_thermal_market_count_excludes_internal_block():
    cfg = load_config(); bids = default_bids(cfg)
    unit = cfg.units['thermal'][0]; pmin, pmax = unit['pmin'], unit['pmax']
    for count, legal in [(10, True), (11, False)]:
        boundaries = [pmin + (pmax-pmin)*k/count for k in range(count+1)]
        bids['units'][unit['id']]['segments'] = [dict(p_start=0,p_end=pmin,price=0)] + [
            dict(p_start=a,p_end=b,price=300) for a,b in zip(boundaries[:-1],boundaries[1:])]
        assert (not any(i.rule == '段数' for i in errors(cfg,bids))) is legal


def test_thermal_width_coverage_and_internal_block():
    cfg = load_config(); b0 = default_bids(cfg)
    b = copy.deepcopy(b0); seg = b['units']['C1']['segments']; seg[-1]['p_end'] -= 1
    assert any(i.rule == '容量覆盖' for i in errors(cfg,b))
    b = copy.deepcopy(b0); seg = b['units']['C1']['segments']; seg[1]['p_end'] = seg[2]['p_start'] = seg[1]['p_start'] + 1
    assert any(i.rule == '段长下限' for i in errors(cfg,b))
    b = copy.deepcopy(b0); seg = b['units']['C1']['segments']; seg[0]['p_end'] = seg[1]['p_start'] = 10
    assert any(i.rule == '内部最小出力块' for i in errors(cfg,b))


def test_dispatchable_range_below_one_mw_uses_actual_width():
    cfg = load_config(); bids = default_bids(cfg)
    u = cfg.units['thermal'][0]; u['pmax'] = u['pmin'] + .5
    bids['units'][u['id']]['segments'] = [dict(p_start=0,p_end=u['pmin'],price=0),dict(p_start=u['pmin'],p_end=u['pmax'],price=300)]
    assert not errors(cfg,bids)


@pytest.mark.parametrize('value', [float('nan'),float('inf'),-float('inf'),True,'300',None])
def test_nonfinite_or_non_numeric_curve_rejected(value):
    cfg = load_config(); b=default_bids(cfg); b['units']['C1']['segments'][1]['price']=value
    assert any(i.rule == '输入格式' for i in errors(cfg,b))


@pytest.mark.parametrize('mutation', [
    lambda b:b['units']['C1']['startup_cost'].update(hot=-1),
    lambda b:b['units']['C1'].update(noload_cost=-1),
    lambda b:b['units']['C1']['startup_cost'].update(typo=0),
    lambda b:b['units']['ES1'].update(mode='typo'),
    lambda b:b['units']['ES1'].update(mode=[]),
    lambda b:b['units']['ES1'].update(soc_min=-.1,soc_max=1.2,soc_end=1.1),
    lambda b:b['units']['ES1'].update(soc_min=None),
    lambda b:b['units']['W1'].update(avail_pmax=[float('nan')]*96),
    lambda b:b['units']['W1'].update(avail_pmax=[-1]*96),
    lambda b:b['units']['W1'].update(avail_pmax=[1]*95),
    lambda b:b['units']['W1'].update(avail_pmax=[1e9]*96),
    lambda b:b['units']['C1'].update(segments=[{'p_start':0}]),
])
def test_invalid_input_fields_rejected_not_clipped(mutation):
    cfg=load_config();b=default_bids(cfg);mutation(b)
    assert errors(cfg,b)


def test_load_and_save_reject_invalid_before_version_write(tmp_path):
    cfg=replace(load_config(),root=tmp_path)
    cfg.base['default_bid_model']='legacy_v1'  # Isolate invalid-save atomicity, no cost archives
    b=default_bids(cfg);b['units']['C1']['segments'][-1]['price']=801
    with pytest.raises(ValueError,match='报价校验未通过'):
        save_bids(cfg,b)
    assert not bid_dir(cfg).exists()
    bid_dir(cfg).mkdir(parents=True);p=bid_dir(cfg)/'bids_v1.json';raw=json.dumps(b);p.write_text(raw)
    with pytest.raises(ValueError,match='申报限价'):
        load_bids(cfg,1)
    assert p.read_text()==raw


def test_direct_case_api_rejects_illegal_bid_before_model_construction():
    from smm.case import build_da_case
    cfg=load_config();b=default_bids(cfg);b['units']['W1']['avail_pmax']=[float('nan')]*96
    with pytest.raises(ValueError,match='avail_pmax'):
        build_da_case(cfg,None,None,b)


def test_storage_charge_direction_not_changed_by_thermal_reference():
    cfg=load_config();b=default_bids(cfg)
    assert not errors(cfg,b)
    b['units']['ES1']['ch_segments'][0]['price']=200
    assert any(i.rule=='价格严格单调' for i in errors(cfg,b))


@pytest.mark.parametrize('bad_price', [900., float('nan')])
def test_model_builder_revalidates_case_mutation(bad_price):
    from smm.case import build_da_case
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm import scuc
    cfg = load_config()
    case = build_da_case(cfg, build_network(cfg), generate_profiles(cfg, cfg.base['seed']), default_bids(cfg))
    # Mutating an already-valid case must not retain a stale validation approval.
    case.bids['units']['C1']['segments'][-1]['price'] = bad_price
    with pytest.raises(ValueError, match='报价校验未通过'):
        scuc.build_model(case)


@pytest.mark.parametrize('stage', ['RT', 'ID'])
def test_direct_window_case_rejected_before_any_solve(monkeypatch, stage):
    from test_da_stages import tiny_case
    from smm import scuc
    from smm.rolling import clear_window
    case = tiny_case()
    case.stage = stage
    case.bids['units']['CHEAP']['segments'][0]['price'] = 900
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid input must be rejected before invoking a solver')
    monkeypatch.setattr(scuc, 'solve_scuc', forbidden)
    with pytest.raises(ValueError, match='申报限价'):
        clear_window(case)



def test_missing_resource_at_model_boundary_is_clear_error_without_solve(monkeypatch):
    from smm.case import build_da_case
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm import scuc
    from smm.rolling import clear_window
    cfg = load_config()
    case = build_da_case(cfg, build_network(cfg), generate_profiles(cfg, cfg.base['seed']), default_bids(cfg))
    del case.bids['units']['C1']
    def forbidden(*args, **kwargs):
        raise AssertionError('Missing resource must fail before the solver')
    monkeypatch.setattr(scuc, 'solve_scuc', forbidden)
    with pytest.raises(ValueError, match='C1.*缺失报价'):
        clear_window(case)
    assert 'C1' not in case.bids['units']  # no silent optimization-boundary filling



@pytest.mark.parametrize('resource_type', ['wind', 'pv'])
def test_renewable_curve_must_cover_rated_capacity(resource_type):
    cfg=load_config();bids=default_bids(cfg)
    unit=next(u for u in cfg.units['renewable'] if u['type']==resource_type)
    bids['units'][unit['id']]['segments'][-1]['p_end']-=1
    assert any(i.unit_id==unit['id'] and i.rule=='容量覆盖' for i in errors(cfg,bids))
