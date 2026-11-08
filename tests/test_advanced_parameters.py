"""Actual scene persistence, consumer evidence and failure-safe advanced edits."""
import copy
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pytest
import yaml

from smm.config import ROOT,load_config
from smm.advanced_parameters import (fields,get_value,prepare_parameter_edit,
                                    apply_parameter_edit,effective_rows,price_rows)
from smm.unit_editor import scene_fingerprint
from test_unit_editor import scene,input_bytes


def edit(scene,changes):
    plan=prepare_parameter_edit(scene,changes,expected=scene_fingerprint(scene))
    apply_parameter_edit(plan)
    return plan


def test_every_exposed_field_has_explanation_and_supported_key(scene):
    cfg=load_config(root=scene)
    specs=fields(cfg)
    assert len(specs)>120
    assert len({x.key for x in specs})==len(specs)
    assert all(len(x.help)>12 and x.label for x in specs)
    assert not any(x.key in {'base.dt_h','base.periods','mode.regulation.enabled'} for x in specs)
    assert not any('segments' in x.key or x.key.endswith('profile') for x in specs)


@pytest.mark.parametrize('changes,match',[
    ({'base.periods':1},'未开放'),({'base.load_scale':0},'正数'),
    ({'base.seed':1.5},'整数'),({'base.seed':True},'有限'),
    ({'base.date':'2026-02-30'},'day'),({'base.solver.threads':0},'范围'),
    ({'base.solver.mip_rel_gap':float('nan')},'有限'),
    ({'base.forecast.phi.wind':1},'小于1'),
    ({'base.forecast.sigma.wind.rt':.9},'da>=id>=rt'),
    ({'base.line_scale.L2-3':0},'正数'),
    ({'mode.price_limits.clear_min':1000},'clear_min'),
    ({'mode.price_limits.bid_min':1000},'报价下限'),
    ({'mode.bid_rules.by_type.coal.price_max':-100},'下限'),
    ({'mode.realtime.lookahead_h':.3},'整数倍'),
    ({'mode.reserve.providers':['coal','coal']},'不重复'),
    ({'mode.intraday.enabled':'false'},'布尔'),
])
def test_invalid_save_never_changes_input(scene,changes,match):
    before=input_bytes(scene)
    with pytest.raises((ValueError,TypeError),match=match):prepare_parameter_edit(scene,changes)
    assert input_bytes(scene)==before


def test_technical_parameters_reach_profiles_network_and_solver_and_frozen_input(scene,tmp_path):
    from smm.forecast import generate_profiles,apply_profile_overrides
    from smm.network import build_network
    from smm.scuc import _solver
    from smm.live_runner import freeze_inputs
    old_cfg=load_config(root=scene)
    before=input_bytes(scene)
    old=generate_profiles(old_cfg)
    oldnet=build_network(old_cfg)
    plan=prepare_parameter_edit(scene,{'base.load_scale':.72,'base.seed':42,
         'base.line_scale.L2-3':1.5,'base.line_capacity_multiplier':1.3,
         'base.solver.time_limit_s':33.,'base.solver.mip_rel_gap':.02})
    assert input_bytes(scene)==before
    apply_parameter_edit(plan)
    cfg=load_config(root=scene)
    new=generate_profiles(cfg)
    assert not np.allclose(old.load_da,new.load_da)
    assert not np.allclose(old.W1_da,new.W1_da)
    net=build_network(cfg)
    branch=net.branches.set_index('branch_id').loc['L2-3']
    assert branch.rate_mw==pytest.approx(branch.rate_orig_mw*1.5*1.3)
    assert _solver(cfg).highs_options['time_limit']==33.
    assert _solver(cfg).highs_options['mip_rel_gap']==.02
    snap=freeze_inputs(scene,tmp_path/'run',bids=json.loads((scene/'current_quote.json').read_bytes())['draft'])
    frozen=input_bytes(snap)
    edit(scene,{'base.solver.time_limit_s':44.})
    assert input_bytes(snap)==frozen
    assert _solver(load_config(root=snap)).highs_options['time_limit']==33.
    assert (scene/'config/units.yaml').read_bytes()==before['config/units.yaml']
    assert (scene/'config/fixed_costs.yaml').read_bytes()==before['config/fixed_costs.yaml']


def test_upload_columns_preserved_and_effective_even_after_generator_changes(scene):
    from smm.forecast import generate_profiles,apply_profile_overrides
    cfg=load_config(root=scene)
    data=generate_profiles(cfg)[['period','load_da']]
    data['load_da']=1234.
    path=scene/'data/profile_overrides.csv';data.to_csv(path,index=False)
    before=path.read_bytes()
    edit(scene,{'base.load_scale':.9,'base.seed':12})
    cfg=load_config(root=scene)
    assert (apply_profile_overrides(cfg,generate_profiles(cfg)).load_da==1234.).all()
    assert path.read_bytes()==before


def test_independent_price_limits_per_type_overrides_and_removal_reach_model(scene):
    from smm.bids import bid_rules_for_type
    from smm.scuc import prepare
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm.case import build_da_case
    edit(scene,{'mode.price_limits.bid_min':-100.,'mode.price_limits.bid_max':900.,
        'mode.price_limits.clear_min':-20.,'mode.price_limits.clear_max':700.,
        'mode.bid_rules.default.price_min':-80.,'mode.bid_rules.by_type.coal.price_min':-60.,
        'mode.bid_rules.by_type.coal.price_max':850.})
    cfg=load_config(root=scene)
    assert bid_rules_for_type(cfg,'coal')['price_min']==-60
    assert bid_rules_for_type(cfg,'wind')['price_min']==-80
    assert cfg.mode['price_limits']['clear_min']==-20
    assert cfg.mode['price_limits']['clear_max']==700
    bids=json.loads((scene/'current_quote.json').read_bytes())['draft']
    assert bids['units']['C1']['segments'][-1]['price']>0
    before=json.loads((scene/'current_quote.json').read_bytes())
    edit(scene,{'mode.bid_rules.by_type.coal.price_min':None})
    cfg=load_config(root=scene)
    assert bid_rules_for_type(cfg,'coal')['price_min']==-80
    assert json.loads((scene/'current_quote.json').read_bytes())==before
    assert next(r for r in price_rows(cfg) if r['类型']=='coal')['下限来源']=='默认覆盖'
    case=build_da_case(cfg,build_network(cfg),generate_profiles(cfg),bids)
    assert case.cfg.mode['price_limits']['clear_max']==700
    assert prepare(case).thermal['C1']['c']==[s['price'] for s in bids['units']['C1']['segments']]


def test_price_narrowing_rejected_without_clipping_quotes(scene):
    # Most existing generation prices exceed this candidate ceiling.
    before=input_bytes(scene)
    with pytest.raises(ValueError):prepare_parameter_edit(scene,{'mode.price_limits.bid_max':1.})
    assert input_bytes(scene)==before


def test_optimistic_conflict_repeat_and_cancel_are_readonly(scene):
    plan=prepare_parameter_edit(scene,{'base.seed':17})
    before=input_bytes(scene)
    assert input_bytes(scene)==before
    path=scene/'config/units.yaml';path.write_bytes(path.read_bytes()+b'\n# another editor\n')
    changed=input_bytes(scene)
    with pytest.raises(RuntimeError,match='另一页面'):apply_parameter_edit(plan)
    assert input_bytes(scene)==changed
    plan=prepare_parameter_edit(scene,{'base.seed':19})
    apply_parameter_edit(plan);saved=input_bytes(scene)
    with pytest.raises(RuntimeError):apply_parameter_edit(plan)
    assert input_bytes(scene)==saved


def test_interrupted_multifile_save_rolls_back_and_preserves_backup(scene,monkeypatch):
    import smm.unit_edit_transaction as tx
    plan=prepare_parameter_edit(scene,{'base.seed':18,'mode.price_limits.clear_max':750.})
    before=input_bytes(scene);real=tx.atomic_bytes;failed=False
    def fail_once(path,content):
        nonlocal failed
        if Path(path)==scene/'config/modes.yaml' and not failed:
            failed=True;raise OSError('injected interrupted write')
        real(path,content)
    monkeypatch.setattr(tx,'atomic_bytes',fail_once)
    with pytest.raises(OSError,match='injected'):apply_parameter_edit(plan)
    assert input_bytes(scene)==before
    assert not (scene/tx.JOURNAL).exists()
    assert list((scene/'parameter_edit_versions').glob('*.json'))


def test_highs_extra_shadow_is_not_saved_as_effective(scene):
    path=scene/'config/base.yaml';base=yaml.safe_load(path.read_bytes())
    base['solver']['highs_extra']={'time_limit':9}
    path.write_text(yaml.safe_dump(base))
    before=input_bytes(scene)
    with pytest.raises(ValueError,match='透传'):prepare_parameter_edit(scene,{'base.solver.time_limit_s':12.})
    assert input_bytes(scene)==before


def test_mode_values_consumed_in_case_and_solver(scene):
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm.case import build_da_case
    from smm.scuc import build_model,_solver
    from smm.rolling import _sub_cfg
    changes={'mode.reserve.rho_L':.04,'mode.penalties.line':12000.,
       'mode.auxiliary_pricing.dispatch_band.relative_to_dispatch':.04,
       'mode.realtime.time_limit_s':21.,'mode.realtime.mip_rel_gap':.025,
       'mode.realtime.value_method.band_fraction':.03,'mode.intraday.time_limit_s':19.}
    edit(scene,changes)
    cfg=load_config(root=scene)
    bids=json.loads((scene/'current_quote.json').read_bytes())['draft']
    case=build_da_case(cfg,build_network(cfg),generate_profiles(cfg),bids)
    for key,value in changes.items():assert get_value(case.cfg,key)==value
    rt=_sub_cfg(cfg,time_limit_s=cfg.mode['realtime']['time_limit_s'],mip_rel_gap=cfg.mode['realtime']['mip_rel_gap'])
    assert _solver(rt).highs_options['time_limit']==21
    assert _solver(rt).highs_options['mip_rel_gap']==.025


def test_date_change_preserves_current_hand_quotes(scene):
    from smm.unit_editor import _current_record
    old=_current_record(scene,load_config(root=scene))['draft']
    edit(scene,{'base.date':'2026-07-16'})
    assert json.loads((scene/'current_quote.json').read_bytes())['draft']==old


def test_frozen_reader_waits_for_atomic_advanced_bundle(scene,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import smm.unit_edit_transaction as tx
    from smm.live_runner import freeze_inputs
    plan=prepare_parameter_edit(scene,{'base.seed':81,'mode.price_limits.clear_max':750.})
    written=threading.Event();finish=threading.Event();real=tx.atomic_bytes
    def slow(path,content):
        real(path,content)
        if Path(path)==scene/'config/base.yaml':
            written.set();assert finish.wait(20)
    monkeypatch.setattr(tx,'atomic_bytes',slow)
    with ThreadPoolExecutor(2) as pool:
        saving=pool.submit(apply_parameter_edit,plan)
        assert written.wait(20)
        frozen=pool.submit(freeze_inputs,scene,tmp_path/'run')
        assert not frozen.done()
        finish.set();saving.result(timeout=30)
        cfg=load_config(root=frozen.result(timeout=30))
    assert cfg.base['seed']==81 and cfg.mode['price_limits']['clear_max']==750.


def test_model_objective_and_reserve_consumers_use_saved_changes(scene):
    from smm.case import build_da_case
    from smm.forecast import generate_profiles
    from smm.network import build_network
    from smm.scuc import build_model
    edit(scene,{'mode.reserve.up_requirement_mode':'formula','mode.reserve.rho_L':.045,'mode.penalties.line':12345.})
    cfg=load_config(root=scene);bids=json.loads((scene/'current_quote.json').read_bytes())['draft']
    case=build_da_case(cfg,build_network(cfg),generate_profiles(cfg),bids)
    expected=.045*case.profiles.load_da.to_numpy()+cfg.mode['reserve']['rho_RE']*sum(case.ren_fc.values())+case.reserve_info['R_ctg']
    assert np.allclose(case.R_up,expected)
    model=build_model(case)
    assert model._pen['line']==12345.
    assert model._case is case


def test_effective_table_resolves_actual_highs_overrides_and_shared_budget(scene):
    cfg=load_config(root=scene)
    cfg.base['solver']['highs_extra']={'time_limit':7.,'mip_rel_gap':.2,'threads':2,'output_flag':True}
    cfg.mode['realtime']['time_limit_s']=11.
    rows={r['参数']:r for r in effective_rows(cfg)}
    assert rows['日前MIP相对间隙']['有效值']=='0.2'
    assert rows['日前MIP时限选项（秒）']['有效值']=='7.0'
    assert rows['实时MIP线程数']['有效值']=='2'
    assert '透传覆盖' in rows['实时MIP线程数']['来源']
    assert rows['日前LP单次时限（秒）']['有效值']=='600.0'
    assert rows['实时LP单次时限（秒）']['有效值']=='11.0'
    assert rows['实时每窗口共享总预算（秒）']['有效值']=='11.0'


def test_hydro_soft_tracking_requires_explicit_compatible_storage_mode(scene):
    before=input_bytes(scene)
    with pytest.raises(ValueError,match='软跟踪要求'):
        prepare_parameter_edit(scene,{'mode.realtime.hydro':'window_end_da'})
    assert input_bytes(scene)==before
    plan=prepare_parameter_edit(scene,{'mode.realtime.hydro':'window_end_da',
                                       'mode.realtime.storage.mode':'sced_anchor'})
    assert plan['changes']['mode.realtime.storage.mode']=='sced_anchor'
    assert input_bytes(scene)==before


def test_activation_matches_two_hour_fallback_soft_hydro_id_anchors_and_handoff(scene):
    from smm.advanced_parameters import inactive_reason
    cfg=load_config(root=scene)
    cfg.mode['realtime']['storage'].update(mode='sced_anchor',anchor='two_hour_eq')
    cfg.mode['realtime']['hydro']='fixed_da'
    assert inactive_reason(cfg,'mode.realtime.value_method.fallback_penalty') is None
    assert inactive_reason(cfg,'mode.realtime.storage.anchor_penalty')
    cfg.mode['realtime']['hydro']='value'
    cfg.mode['realtime']['value_method']['terminal_policy']['mode']='hard'
    assert inactive_reason(cfg,'mode.realtime.value_method.hydro_lookahead')
    cfg.mode['intraday']['enabled']=True
    cfg.mode['realtime']['storage'].update(mode='fixed_da',anchor='window_end_pct')
    assert inactive_reason(cfg,'mode.realtime.storage.anchor') is None
    assert inactive_reason(cfg,'mode.realtime.storage.x')
    assert inactive_reason(cfg,'mode.realtime.storage.anchor_penalty') is None
    assert inactive_reason(cfg,'mode.realtime.handoff.storage')
    assert inactive_reason(cfg,'mode.realtime.handoff.pumped')
    assert inactive_reason(cfg,'mode.realtime.handoff.hydro') is None
    cfg.mode['realtime']['hydro']='fixed_da'
    assert inactive_reason(cfg,'mode.realtime.handoff.hydro')


def test_tracking_penalty_priority_is_not_mislabeled_as_anchor_or_fallback(scene):
    from smm.advanced_parameters import inactive_reason
    cfg=load_config(root=scene)
    cfg.mode['realtime']['storage'].update(mode='sced_anchor',anchor='hourly_eq')
    cfg.mode['realtime']['hydro']='window_end_da'
    assert inactive_reason(cfg,'mode.realtime.storage.anchor_penalty')
    cfg.mode['intraday']['enabled']=True
    assert inactive_reason(cfg,'mode.realtime.storage.anchor_penalty') is None  # ID still uses it.
    cfg.mode['realtime']['storage']['anchor']='two_hour_eq'
    assert inactive_reason(cfg,'mode.realtime.storage.anchor_penalty')
    assert inactive_reason(cfg,'mode.realtime.value_method.fallback_penalty')
    assert inactive_reason(cfg,'mode.realtime.tracking_policy.penalty_yuan_per_discharge_mwh') is None


def test_readonly_inventory_discloses_fixed_and_legacy_consumption(scene):
    from smm.advanced_parameters import readonly_rows
    rows={r['参数']:r for r in readonly_rows(load_config(root=scene))}
    assert 'cfg.dt' in rows['结算间隔声明（分钟）']['只读说明']
    assert '声明' in rows['边界时序说明（分钟）']['只读说明']
    assert rows['历史调频实验']['保存值']=='关闭'
    assert '未申报回初态' in rows['日前储能日末口径']['只读说明']


def test_threads_exceeding_highs_native_integer_range_are_rejected(scene):
    before=input_bytes(scene)
    with pytest.raises(ValueError,match='范围'):
        prepare_parameter_edit(scene,{'base.solver.threads':2147483648})
    assert input_bytes(scene)==before
