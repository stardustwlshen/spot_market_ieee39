"""Synthetic formula examples only: not researched default input values."""
from copy import deepcopy
import hashlib

import pytest

from smm.bid_cost_basis import cost_basis_anchors
from smm.bid_simulation import simulate_cost_based_bids
from smm.bids import default_bids, bid_dir
from smm.config import load_config


def record(**parameters):
    return {'parameters': parameters, 'provenance': {
        key: {'source': 'Synthetic unit-test assumption, not actual plant data', 'status': 'assumed'}
        for key in parameters}}


def thermal(fuel=40, heat=8, om=20):
    return record(fuel_price_yuan_per_gj=fuel, heat_rate_gj_per_mwh=heat,
                  variable_om_yuan_per_mwh=om)


def storage():
    return record(replacement_energy_yuan_per_mwh=300, variable_om_yuan_per_mwh=5,
                  degradation_yuan_per_mwh=15, charge_willingness_yuan_per_mwh=280)


def test_type_specific_formulas_and_unit_efficiency():
    cfg = load_config()
    sheet = {'C1': thermal(), 'CCGT': thermal(60, 6, 10), 'GT': thermal(60, 10, 10),
             'W1': record(variable_om_yuan_per_mwh=5), 'PV1': record(variable_om_yuan_per_mwh=3),
             'H1': record(water_opportunity_yuan_per_mwh=120, variable_om_yuan_per_mwh=10),
             'ES1': storage(), 'PS1': storage()}
    a = cost_basis_anchors(cfg, sheet)
    for uid, expected in {'C1': 340, 'CCGT': 370, 'GT': 610, 'W1': 5, 'PV1': 3, 'H1': 130}.items():
        assert a[uid]['curve_anchors_yuan_per_mwh']['segments'] == expected
    assert a['ES1']['curve_anchors_yuan_per_mwh']['dis_segments'] == pytest.approx(300 / .95**2 + 20)
    assert a['PS1']['curve_anchors_yuan_per_mwh']['gen_segments'] == pytest.approx(300 / (9.6/12.6) + 20)
    assert a['ES1']['curve_anchors_yuan_per_mwh']['ch_segments'] == 280


def test_unit_heat_rate_drives_differences_without_random_noise():
    a = cost_basis_anchors(load_config(), {'C1': thermal(40, 8), 'C2': thermal(40, 9)})
    assert a['C2']['curve_anchors_yuan_per_mwh']['segments'] - a['C1']['curve_anchors_yuan_per_mwh']['segments'] == 40


def test_preview_preserves_structure_original_manual_other_units_and_files():
    cfg = load_config(); base = default_bids(cfg)
    base['units']['C2']['segments'][-1]['price'] += .5
    original = deepcopy(base)
    paths = list(bid_dir(cfg).glob('bids_v*.json'))
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    out = simulate_cost_based_bids(cfg, worksheet={'C1': thermal()}, selected_unit_ids=['C1'], base_bids=base)
    assert base == original
    assert out['units']['C1']['segments'][1]['price'] == 340
    assert out['units']['C1']['segments'][0]['price'] == 40
    for old, new in zip(base['units']['C1']['segments'], out['units']['C1']['segments']):
        assert new['price'] - old['price'] == 40
        assert (old['p_start'], old['p_end']) == (new['p_start'], new['p_end'])
    for uid in base['units']:
        if uid != 'C1': assert out['units'][uid] == base['units'][uid]
    for key, value in base['units']['C1'].items():
        if key != 'segments': assert out['units']['C1'][key] == value
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_explicit_rounding_and_separate_seed_reproducibility():
    cfg = load_config(); kwargs = dict(worksheet={'C1': thermal(40,8,20.005)}, selected_unit_ids=['C1'])
    out = simulate_cost_based_bids(cfg, **kwargs)
    assert out['units']['C1']['segments'][1]['price'] == 340.01
    assert out['_cost_basis']['changes'][0]['unrounded_anchor_yuan_per_mwh'] == 340.005
    assert '_simulation' not in out
    a = simulate_cost_based_bids(cfg, **kwargs, strategy_seed=42)
    b = simulate_cost_based_bids(cfg, **kwargs, strategy_seed=42)
    assert a == b
    assert a['_cost_basis']['anchors'] == out['_cost_basis']['anchors']
    assert a['_simulation']['seed'] == 42


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf'), -1, '40'])
def test_bad_fuel_input_rejected(bad):
    with pytest.raises(ValueError): cost_basis_anchors(load_config(), {'C1': thermal(fuel=bad)})


def test_provenance_required_and_no_unknown_parameter_defaults():
    r = thermal(); r['provenance'].pop('heat_rate_gj_per_mwh')
    with pytest.raises(ValueError, match='provenance'): cost_basis_anchors(load_config(), {'C1': r})
    r = thermal(); r['parameters']['heat_rate_gj_per_mw'] = r['parameters'].pop('heat_rate_gj_per_mwh')
    with pytest.raises(ValueError, match='parameters'): cost_basis_anchors(load_config(), {'C1': r})


def test_no_clipping_and_no_protected_overwrite():
    cfg = load_config()
    with pytest.raises(ValueError, match='申报限价'):
        simulate_cost_based_bids(cfg, worksheet={'C1': thermal(200)}, selected_unit_ids=['C1'])
    with pytest.raises(ValueError, match='受保护'):
        simulate_cost_based_bids(cfg, worksheet={'C1': thermal()}, selected_unit_ids=['C1'], protected_unit_ids=['C1'])


def test_storage_separate_buy_and_sell_anchors_and_shapes():
    cfg = load_config(); base = default_bids(cfg)
    out = simulate_cost_based_bids(cfg, worksheet={'ES1': storage()}, selected_unit_ids=['ES1'], base_bids=base)
    unit = out['units']['ES1']
    assert unit['ch_segments'][0]['price'] == 280
    assert unit['dis_segments'][0]['price'] == pytest.approx(350)
    assert unit['ch_segments'][1]['price'] == 260
    assert unit['dis_segments'][1]['price'] == pytest.approx(380)


def test_research_proposal_all_units_values_and_quantization():
    from smm.bid_simulation import research_cost_bid_preview
    cfg = load_config(); base = default_bids(cfg); original = deepcopy(base)
    out = research_cost_bid_preview(cfg, selected_unit_ids=base['units'], base_bids=base)
    assert base == original
    for uid, anchor in {'C1':280, 'C2':297, 'C3':315, 'CCGT':405, 'GT':620}.items():
        assert out['units'][uid]['segments'][1]['price'] == anchor
    assert out['units']['C1']['segments'][0]['price'] == 280
    assert out['units']['GT']['segments'][0]['price'] == 620
    assert out['units']['GT']['segments'][-1]['price'] == 706.8
    assert [s['price'] for s in out['units']['W1']['segments']] == [10,20,30,40,50]
    assert [s['price'] for s in out['units']['W2']['segments']] == [10,20,30,40,50]
    assert out['units']['H1']['segments'][0]['price'] == 120
    assert out['units']['ES1']['dis_segments'][0]['price'] == 410
    assert out['units']['ES1']['ch_segments'][0]['price'] == 350
    assert out['units']['PS1']['gen_segments'][0]['price'] == 390
    assert out['units']['PS1']['pump_segments'][0]['price'] == 290
    for uid, bid in base['units'].items():
        for key, value in bid.items():
            if key.endswith('segments'):
                assert [(s['p_start'],s['p_end']) for s in value] == [(s['p_start'],s['p_end']) for s in out['units'][uid][key]]
            else: assert out['units'][uid][key] == value
    assert out['_cost_basis']['worksheet']['source_kind'] == 'research_assumption_not_observed_plant_data'
    assert out == research_cost_bid_preview(cfg, selected_unit_ids=reversed(list(base['units'])), base_bids=base)


def test_research_proposal_missing_scenario_worksheet_fails(tmp_path):
    from smm.bid_simulation import research_cost_bid_preview
    cfg = load_config(); cfg.root = tmp_path
    with pytest.raises(ValueError, match='不会回退'):
        research_cost_bid_preview(cfg, selected_unit_ids=['C1'])


def test_research_manual_guard_and_selected_only():
    from smm.bid_simulation import research_cost_bid_preview
    cfg = load_config(); base = default_bids(cfg)
    with pytest.raises(ValueError, match='受保护'):
        research_cost_bid_preview(cfg, selected_unit_ids=['C1'], protected_unit_ids=['C1'], base_bids=base)
    out = research_cost_bid_preview(cfg, selected_unit_ids=['C1'], base_bids=base)
    assert out['units']['C2'] == base['units']['C2']


@pytest.mark.parametrize('bad', [float('nan'), True, -1])
def test_research_invalid_worksheet_numbers(tmp_path, bad):
    import yaml
    from smm.bid_cost_basis import research_cost_worksheet
    cfg = load_config(); data = research_cost_worksheet(cfg)
    data['thermal']['C1']['heat_rate'] = bad
    (tmp_path/'config').mkdir(); (tmp_path/'config'/'bid_cost_basis.yaml').write_text(yaml.safe_dump(data))
    cfg.root = tmp_path
    with pytest.raises(ValueError): research_cost_worksheet(cfg)


def _offer_cost_per_hour(bid, power, online):
    """Independent arithmetic of the SCUC linear segment + on-cost convention."""
    if not online:
        assert power == 0
        return 0.0
    remaining = power
    total = bid['noload_cost']
    for segment in bid['segments']:
        quantity = min(remaining, segment['p_end'] - segment['p_start'])
        total += quantity * segment['price']
        remaining -= quantity
    assert remaining == pytest.approx(0)
    return total


def test_coal_minimum_load_cost_complete_once_and_off_cost_zero():
    from smm.bid_simulation import research_cost_bid_preview
    cfg = load_config(); base = default_bids(cfg)
    out = research_cost_bid_preview(cfg, selected_unit_ids=['C1','C2','C3'], base_bids=base)
    for uid, expected in {'C1':87200, 'C2':103025, 'C3':130350}.items():
        bid = out['units'][uid]; pmin = bid['segments'][0]['p_end']
        assert bid['noload_cost'] == base['units'][uid]['noload_cost']
        assert bid['segments'][0]['price'] == bid['segments'][1]['price']
        assert 'noload_cost_basis' not in bid
        assert _offer_cost_per_hour(bid, 0, False) == 0
        assert _offer_cost_per_hour(bid, pmin, True) == expected
        assert _offer_cost_per_hour(bid, pmin+10, True) == expected+10*bid['segments'][1]['price']
    # A second explicit preview leaves fixed no-load costs unchanged.
    repeated = research_cost_bid_preview(cfg, selected_unit_ids=['C1','C2','C3'], base_bids=out)
    for uid in ('C1','C2','C3'): assert repeated['units'][uid] == out['units'][uid]


def test_gas_base_energy_is_not_also_added_to_noload():
    from smm.bid_simulation import research_cost_bid_preview
    cfg = load_config(); base = default_bids(cfg)
    out = research_cost_bid_preview(cfg, selected_unit_ids=['CCGT','GT'], base_bids=base)
    for uid in ('CCGT','GT'):
        bid = out['units'][uid]; pmin = bid['segments'][0]['p_end']
        assert bid['noload_cost'] == base['units'][uid]['noload_cost']
        assert 'noload_cost_basis' not in bid
        assert _offer_cost_per_hour(bid, pmin, True) == bid['noload_cost']+pmin*bid['segments'][0]['price']


def _isolated_cost_config(tmp_path):
    import shutil
    cfg = load_config()
    root = tmp_path/'config'; root.mkdir()
    for name in ('bid_cost_basis.yaml','fixed_costs.yaml'):
        shutil.copyfile(cfg.root/'config'/name, root/name)
    cfg.root = tmp_path
    return cfg


def test_fixed_cost_edit_controls_next_preview_without_mutating_cost_or_base(tmp_path):
    import yaml
    from smm.bid_simulation import research_cost_bid_preview
    cfg = _isolated_cost_config(tmp_path); base = default_bids(cfg); original = deepcopy(base)
    path = tmp_path/'config'/'fixed_costs.yaml'
    first = research_cost_bid_preview(cfg, selected_unit_ids=['C1'], base_bids=base)
    profile = yaml.safe_load(path.read_text())
    profile['units']['C1']['scalar_marginal_cost_yuan_per_mwh'] += 100
    path.write_text(yaml.safe_dump(profile)); raw = path.read_bytes()
    second = research_cost_bid_preview(cfg, selected_unit_ids=['C1'], base_bids=base)
    assert [b['price']-a['price'] for a,b in zip(first['units']['C1']['segments'],second['units']['C1']['segments'])] == pytest.approx([100,100,102,105,109,114])
    assert second['_cost_basis']['changes'][0]['basis']['fixed_scalar_overrides_original_derivation']
    assert first['_cost_basis']['fixed_cost_profile_sha256'] != second['_cost_basis']['fixed_cost_profile_sha256']
    random = research_cost_bid_preview(cfg, selected_unit_ids=['C1'], base_bids=base, strategy_seed=3)
    assert random['_cost_basis']['fixed_cost_parameters'] == second['_cost_basis']['fixed_cost_parameters']
    assert path.read_bytes() == raw
    assert base == original


def test_all_nonthermal_anchors_use_fixed_cost_profile(tmp_path):
    import yaml
    from smm.bid_simulation import research_cost_bid_preview
    cfg = _isolated_cost_config(tmp_path)
    path = tmp_path/'config'/'fixed_costs.yaml'; profile = yaml.safe_load(path.read_text())
    profile['units']['W1']['scalar_marginal_cost_yuan_per_mwh'] = 38
    profile['units']['H1']['scalar_marginal_cost_yuan_per_mwh'] = 36
    profile['units']['ES1']['discharge_degradation_yuan_per_mwh'] = 55
    profile['units']['PS1']['generation_vom_yuan_per_mwh'] = 40
    path.write_text(yaml.safe_dump(profile))
    out = research_cost_bid_preview(cfg, selected_unit_ids=['W1','H1','ES1','PS1'])
    basis = {c['unit_id']:c['basis'] for c in out['_cost_basis']['changes']}
    assert basis['W1']['variable_om_yuan_per_mwh'] == 38
    assert basis['H1']['variable_om_yuan_per_mwh'] == 36
    assert basis['ES1']['fixed_discharge_expense_yuan_per_mwh'] == 55
    assert basis['PS1']['fixed_discharge_expense_yuan_per_mwh'] == 40
    assert basis['ES1']['charge_willingness_anchor'] == pytest.approx(.9025*(410-55))
    assert basis['PS1']['charge_willingness_anchor'] == pytest.approx((9.6/12.6)*(390-40))


def test_missing_fixed_profile_never_falls_back_to_old_worksheet(tmp_path):
    from smm.bid_simulation import research_cost_bid_preview
    cfg = _isolated_cost_config(tmp_path)
    (tmp_path/'config'/'fixed_costs.yaml').unlink()
    with pytest.raises(ValueError, match='fixed_costs.yaml'):
        research_cost_bid_preview(cfg, selected_unit_ids=['C1'])
