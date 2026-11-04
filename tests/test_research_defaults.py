import copy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from smm.config import load_config
from smm.research_defaults import (generate_research_defaults,
    is_known_legacy_research_record, research_record_hash)


def test_outputs_are_pure_and_compatible(tmp_path):
    from smm.bid_cost_basis import research_cost_worksheet
    from smm.profit import load_fixed_cost_profile
    cfg = load_config()
    before = copy.deepcopy(cfg)
    out = generate_research_defaults(cfg)
    assert cfg == before
    assert out == generate_research_defaults(cfg)
    assert set(out['profile']['units']) == {u['id'] for group in ('thermal','renewable','hydro','storage','pumped') for u in cfg.units[group]}
    assert 'TIE' not in out['evidence']
    (tmp_path / 'config').mkdir()
    for filename, key in [('fixed_costs.yaml', 'profile'), ('bid_cost_basis.yaml', 'worksheet')]:
        (tmp_path / 'config' / filename).write_text(yaml.safe_dump(out[key]))
    checked = replace(cfg, root=tmp_path)
    assert load_fixed_cost_profile(checked)['units'] == out['profile']['units']
    assert research_cost_worksheet(checked) == out['worksheet']
    for uid, rec in out['profile']['units'].items():
        assert out['evidence'][uid]['record_hash'] == research_record_hash(rec)


def test_types_capacity_scaling_and_separate_opportunity_costs():
    cfg = load_config()
    out = generate_research_defaults(cfg)
    units, sheet, evidence = out['profile']['units'], out['worksheet'], out['evidence']
    assert [units[x]['scalar_marginal_cost_yuan_per_mwh'] for x in ['C1', 'C2', 'C3', 'CCGT', 'GT']] == [280, 280, 280, 405, 620]
    assert units['C2']['fixed_online_cost_yuan_per_h'] == pytest.approx(6000 * 725 / 646)
    assert units['C2']['fixed_startup_cost_yuan_per_start'] == pytest.approx(250000 * 725 / 646)
    assert units['W2']['scalar_marginal_cost_yuan_per_mwh'] == 8
    assert units['H1']['scalar_marginal_cost_yuan_per_mwh'] == 6
    assert sheet['hydro']['H1']['water_opportunity'] == {'wet': 110, 'dry': 180}
    assert units['ES3']['discharge_degradation_yuan_per_mwh'] == 25
    assert evidence['ES1']['technical_parameters']['roundtrip_efficiency'] == pytest.approx(.95 ** 2)
    assert evidence['PS1']['technical_parameters']['roundtrip_efficiency'] == pytest.approx(9.6 / 12.6)
    assert sheet['storage']['ES1']['reservation'] == 410
    assert sheet['pumped']['PS1']['reservation'] == 390
    cfg.units['pumped'][0]['unit_gen_max'] *= 2
    cfg.units['pumped'][0]['unit_pump'] *= 3
    changed = generate_research_defaults(cfg)['profile']['units']['PS1']
    assert changed['gen_start_yuan_per_unit_start'] == 4000
    assert changed['pump_start_yuan_per_unit_start'] == 12000
    assert changed['generation_vom_yuan_per_mwh'] == 10


def test_rename_and_same_technology_do_not_change_values():
    cfg = load_config()
    original = generate_research_defaults(cfg)
    for family, specs in cfg.units.items():
        if family == '_disabled':
            continue
        for spec in specs:
            spec['id'] = 'renamed_' + spec['id']
    renamed = generate_research_defaults(cfg)
    for uid, rec in original['profile']['units'].items():
        assert renamed['profile']['units']['renamed_' + uid] == rec
        assert renamed['evidence']['renamed_' + uid] == original['evidence'][uid]
    cfg.units['storage'][0]['ecap'] *= 5
    cfg.units['storage'][0]['pmax'] *= 2
    resized = generate_research_defaults(cfg)
    assert resized['profile']['units']['renamed_ES1'] == renamed['profile']['units']['renamed_ES1']
    assert resized['worksheet']['storage']['renamed_ES1'] == renamed['worksheet']['storage']['renamed_ES1']


@pytest.mark.parametrize('family,field', [('thermal', 'pmax'), ('renewable', 'cap'),
    ('hydro', 'n_units'), ('hydro', 'unit_pmax'), ('storage', 'ecap'),
    ('storage', 'pmax'), ('storage', 'eta_c'), ('storage', 'eta_d'),
    ('pumped', 'unit_gen_max'), ('pumped', 'unit_pump'), ('pumped', 'e_gen'),
    ('pumped', 'e_pump'), ('pumped', 'n_units')])
@pytest.mark.parametrize('value', [None, 0, -1, float('nan'), float('inf'), True, '100'])
def test_invalid_required_physical_inputs_fail_closed(family, field, value):
    cfg = load_config()
    cfg.units[family][0][field] = value
    with pytest.raises(ValueError):
        generate_research_defaults(cfg)


def test_disabled_unknown_types_and_missing_fields():
    cfg = load_config()
    cfg.units['storage'].append({'id': 'disabled', 'enabled': False, 'type': 'unknown'})
    assert 'disabled' not in generate_research_defaults(cfg)['evidence']
    cfg.units['storage'][-1]['enabled'] = True
    with pytest.raises(ValueError, match='类型'):
        generate_research_defaults(cfg)
    cfg.units['storage'].pop()
    del cfg.units['thermal'][0]['type']
    with pytest.raises(ValueError, match='类型'):
        generate_research_defaults(cfg)


def test_legacy_recognition_is_exact_and_does_not_trust_labels():
    cfg = load_config()
    old = yaml.safe_load((Path(cfg.root) / 'config/fixed_costs.yaml').read_text())['units']
    legacy = {uid: record for uid, record in old.items() if uid not in {'PV2', 'PV3'}}
    assert len(legacy) == 13
    for uid, record in legacy.items():
        assert is_known_legacy_research_record(uid, record)
        assert not is_known_legacy_research_record('renamed_' + uid, record)
        custom = copy.deepcopy(record)
        custom['source_kind'] = 'research_assumption_not_observed_plant_data'
        assert not is_known_legacy_research_record(uid, custom)
        custom = copy.deepcopy(record)
        key = next(k for k in custom if k != 'type')
        custom[key] += .01
        assert not is_known_legacy_research_record(uid, custom)
        assert research_record_hash(record) != research_record_hash(custom)


def test_efficiency_and_integer_constraints():
    cfg = load_config()
    cfg.units['storage'][0]['eta_c'] = 1.1
    with pytest.raises(ValueError):
        generate_research_defaults(cfg)
    cfg = load_config()
    cfg.units['pumped'][0]['e_gen'] = 20
    with pytest.raises(ValueError):
        generate_research_defaults(cfg)
    cfg = load_config()
    cfg.units['hydro'][0]['n_units'] = 1.5
    with pytest.raises(ValueError):
        generate_research_defaults(cfg)


def test_legacy_worksheet_recognition_is_exact():
    from smm.research_defaults import is_known_legacy_worksheet_record
    cfg = load_config()
    old = yaml.safe_load((Path(cfg.root) / 'config/bid_cost_basis.yaml').read_text())
    for group in ('thermal', 'renewable_vom', 'hydro', 'storage', 'pumped'):
        for uid, record in old[group].items():
            if uid in {'PV2', 'PV3'}:
                assert not is_known_legacy_worksheet_record(group, uid, record)
                continue  # Added defaults must not be misidentified as original legacy records.
            assert is_known_legacy_worksheet_record(group, uid, record)
            assert not is_known_legacy_worksheet_record(group, 'renamed_' + uid, record)
            changed = copy.deepcopy(record)
            if isinstance(changed, dict):
                changed['source_kind'] = 'research_assumption_not_observed_plant_data'
            else:
                changed += .01
            assert not is_known_legacy_worksheet_record(group, uid, changed)
