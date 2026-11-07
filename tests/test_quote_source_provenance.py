"""Display provenance is not evidence of an external quotation change."""
from copy import deepcopy
import json

import pytest

from test_single_day_workspace import scene, app, step, item, files


@pytest.mark.parametrize('source', ['typical', 'technical', 'typical_then_technical'])
def test_own_scene_and_technical_saves_do_not_report_external_quotes(scene, source):
    from smm.config import load_config
    from smm.bids import load_bids
    from smm.typical_scenarios import prepare_typical, create_typical
    from smm.unit_editor import prepare_unit_edit, apply_unit_edit
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    root = scene.scenario_path('默认')
    if source.startswith('typical'):
        cfg = load_config(root=root)
        bids = load_bids(cfg)
        preview = prepare_typical(root, bids=bids, wind_multiplier=.8)
        root = create_typical('小风副本', preview, bids=bids, confirm_availability=True)
    if source != 'typical':
        cfg = load_config(root=root)
        unit = deepcopy(cfg.units['thermal'][0])
        unit['ramp'] += .3
        apply_unit_edit(prepare_unit_edit(root, 'thermal', unit, original_id=unit['id']))
    cfg = load_config(root=root)
    before = files(root)
    state = {}
    key, draft, baseline, info = get_scene_working_draft(state, root.name, cfg)
    assert info['basis_label'] in ('典型场景建立时复制的当前报价', '机组编辑前当前报价')
    assert not info['newer_basis']
    assert files(root) == before
    original = deepcopy(draft)
    draft['units']['C1']['segments'][-1]['price'] += 3.2
    persist_scene_working_draft(state, root.name, cfg, only_if_changed=True)
    saved = deepcopy(draft)
    assert get_scene_working_draft(state, root.name, cfg)[3]['newer_basis'] is False
    fresh = get_scene_working_draft({}, root.name, cfg)
    assert fresh[1] == saved and fresh[2] == baseline
    assert fresh[3]['newer_basis'] is False
    assert all(saved['units'][uid] == unit for uid, unit in original['units'].items() if uid != 'C1')

    at = step(app(), '3 报价')
    item(at.selectbox, '单日场景').set_value(root.name).run()
    step(at, '4 运行'); step(at, '3 报价')
    assert not at.exception
    assert at.session_state[key] == saved
    assert not at.session_state[key + ':meta']['newer_basis']
    assert not any('最新已保存报价与本页基础不同' in w.value or '外部已保存报价已变化' in w.value for w in at.warning)


@pytest.mark.parametrize('change', ['new_version', 'same_version'])
def test_real_archive_changes_still_warn_without_replacing_work(scene, change):
    from smm.config import load_config
    from smm.bids import bid_dir, load_bids, save_bids
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    cfg = load_config(root=scene.scenario_path('默认'))
    state = {}
    key, draft, baseline, _ = get_scene_working_draft(state, '默认', cfg)
    state[key + ':meta']['basis_label'] = '典型场景建立时复制的当前报价'
    draft['units']['C1']['noload_cost'] += 7
    persist_scene_working_draft(state, '默认', cfg)
    local = deepcopy(draft)
    newer = load_bids(cfg)
    if change == 'new_version':
        # Even identical offer values under a genuinely new version must warn.
        save_bids(cfg, newer, new_version=True)
    else:
        newer['units']['C2']['noload_cost'] += 13
        (bid_dir(cfg) / f"bids_v{newer['version']}.json").write_text(json.dumps(newer), encoding='utf-8')
    before = files(cfg.root)
    result = get_scene_working_draft(state, '默认', cfg)
    assert result[3]['newer_basis']
    assert result[1] == local and result[2] == baseline
    assert get_scene_working_draft({}, '默认', cfg)[3]['newer_basis']
    assert files(cfg.root) == before


def test_explicit_older_version_warns_when_initial_latest_already_is_newer(scene):
    from smm.config import load_config
    from smm.bids import load_bids, save_bids
    from bid_workspace import get_scene_working_draft
    cfg = load_config(root=scene.scenario_path('默认'))
    older = load_bids(cfg)
    save_bids(cfg, older, new_version=True)
    latest = load_bids(cfg)
    retained = deepcopy(older)
    retained['units']['C1']['noload_cost'] += 7
    state = {f"bid_draft:默认:{older['version']}": retained, 'quote_source:默认': older['version']}
    _, draft, baseline, info = get_scene_working_draft(state, '默认', cfg)
    assert info['initial_latest'] == latest
    assert draft == retained and baseline == older
    assert info['newer_basis']


@pytest.mark.parametrize('missing_snapshot', [False, True])
def test_legacy_source_record_without_initial_snapshot_uses_baseline(scene, missing_snapshot):
    from smm.config import load_config
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    cfg = load_config(root=scene.scenario_path('默认'))
    state = {}
    get_scene_working_draft(state, '默认', cfg)
    persist_scene_working_draft(state, '默认', cfg)
    path = cfg.root / 'current_quote.json'
    record = json.loads(path.read_bytes())
    record['basis_label'] = '自定义来源说明'
    if missing_snapshot:
        record.pop('initial_latest')
    else:
        record['initial_latest'] = None
    path.write_text(json.dumps(record), encoding='utf-8')
    before = files(cfg.root)
    assert not get_scene_working_draft({}, '默认', cfg)[3]['newer_basis']
    assert files(cfg.root) == before
