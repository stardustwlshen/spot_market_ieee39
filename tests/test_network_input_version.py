"""Network refresh and stale provenance, without running an optimization."""
import copy
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from network_view import network_data, input_snapshot, result_snapshot, topology_figure, layout
from smm.config import load_config
from smm.input_version import compare_source_version, run_options
from smm.live_runner import freeze_inputs, file_fingerprint
from smm.bids import load_bids


def scene(tmp_path):
    root=tmp_path/'scene'
    for folder in ('config','data','bids'):
        shutil.copytree(ROOT/folder,root/folder)
    return root


def frozen(tmp_path):
    root=scene(tmp_path);cfg=load_config(root=root); bids=load_bids(cfg)
    quote=dict(schema=1,draft=bids,manual=['C1'],saved_at='old')
    (root/'current_quote.json').write_text(json.dumps(quote))
    out=tmp_path/'run';out.mkdir()
    opts=run_options('full',8)
    freeze_inputs(root,out,bids=bids,run_options=opts,bid_version=bids['version'])
    return root,out,opts


def test_equal_source_quotes_and_options_then_each_change_is_stale(tmp_path):
    root,out,opts=frozen(tmp_path)
    assert compare_source_version(out,root,options=opts)['status']=='equal'
    targets=['config/base.yaml','config/fixed_costs.yaml','data/case39/branches.csv',
             'data/profile_overrides.csv','current_quote.json']
    for relative in targets:
        path=root/relative
        original=path.read_bytes() if path.exists() else None
        if relative=='current_quote.json':
            record=json.loads(original); record['manual'].append('C2');path.write_text(json.dumps(record))
        elif original is None:
            path.write_text('period,load_da\n0,4000\n')
        else:
            path.write_bytes(original+b'\n')  # saved byte change, independent of unchanged display
        assert compare_source_version(out,root,options=opts)['status']=='changed',relative
        path.unlink() if original is None else path.write_bytes(original)
        assert compare_source_version(out,root,options=opts)['status']=='equal'
    assert compare_source_version(out,root,options=run_options('full',9))['status']=='changed'
    assert compare_source_version(out,root,options=run_options('da'))['status']=='changed'
    assert compare_source_version(out,root,options=None)['status']=='unknown'


def test_manual_bid_execution_and_no_volatile_metadata_false_positive(tmp_path):
    root,out,opts=frozen(tmp_path);p=root/'current_quote.json'
    quote=json.loads(p.read_text());quote['saved_at']='new';quote['history']={'old':'not active'}
    quote['draft']['created']='different display timestamp';p.write_text(json.dumps(quote))
    assert compare_source_version(out,root,options=opts)['status']=='equal'
    quote['draft']['units']['C1']['noload_cost'] += 10;p.write_text(json.dumps(quote))
    result=compare_source_version(out,root,options=opts)
    assert result['status']=='changed' and '当前报价 / 人工报价标记' in result['changes']


def test_legacy_and_incomplete_hashes_remain_unknown(tmp_path):
    root=scene(tmp_path)
    assert compare_source_version(tmp_path,root,options=run_options())['status']=='unknown'
    (tmp_path/'frozen_input_provenance.json').write_text(json.dumps({'source_version':{'schema':1}}))
    assert compare_source_version(tmp_path,root,options=run_options())['status']=='unknown'


def test_old_result_stays_frozen_after_current_unit_and_network_changes(tmp_path):
    from test_network_view import fixture_result
    root,out,opts=frozen(tmp_path); snap=out/'scenario_snapshot'
    before=result_snapshot(network_data(snap),fixture_result(),'rt',0)
    unitpath=root/'config/units.yaml'; units=yaml.safe_load(unitpath.read_text())
    units['storage'][0]['bus']=20;units['hydro'][0]['enabled']=False
    unitpath.write_text(yaml.safe_dump(units,sort_keys=False))
    current=network_data(root)
    assert current['resources'].set_index('unit').loc['ES1','bus']==20
    assert compare_source_version(out,root,options=opts)['status']=='changed'
    after=result_snapshot(network_data(snap),fixture_result(),'rt',0)
    for key in ('buses','branches','resources'):
        pd.testing.assert_frame_equal(before[key],after[key])


@pytest.mark.parametrize('stage',['actual','da','id','rt'])
def test_input_period_uses_exact_model_profiles_and_no_side_effects(tmp_path,stage):
    from smm.forecast import generate_profiles,apply_profile_overrides
    from smm.fixed_load import nodal_demand
    from smm.network import build_network
    root=scene(tmp_path);cfg=load_config(root=root)
    before=file_fingerprint(root,('config','data','bids'))
    cfg.base['load_scale'] *= .9;cfg.base['line_capacity_multiplier']=.73
    data=network_data(root,base=cfg.base)
    view=input_snapshot(cfg,data,stage,17)
    profiles=apply_profile_overrides(cfg,generate_profiles(cfg)); net=build_network(cfg)
    expected,_=nodal_demand(cfg,net,profiles,profiles['load_'+stage],stage=stage)
    for row in view['buses'].itertuples():
        assert row.load_mw==pytest.approx(expected[row.bus][17] if row.bus in expected else 0)
    pd.testing.assert_series_equal(data['branches'].rate_eff,net.branches.rate_mw,check_names=False)
    assert 'lmp' not in view['buses'] and 'P_mw' not in view['resources']
    assert file_fingerprint(root,('config','data','bids'))==before


def test_price_layers_never_reclamp_raw_price_or_guess_missing_settlement():
    from test_network_view import fixture_result
    r=fixture_result(); r['rt_prices'].loc[0,'lmp']=9000.
    view=result_snapshot(network_data(ROOT),r,'rt',0)
    assert view['buses'].set_index('bus').loc[19,'legacy_lmp']==9000
    assert view['buses'].lmp.isna().all()
    assert view['buses'].lmp_clamped.isna().all()
    r['rt_prices']['lmp_clamped']=[1500.,48.]
    view=result_snapshot(network_data(ROOT),r,'rt',0)
    assert view['buses'].set_index('bus').loc[19,'lmp_clamped']==1500
    for layer in ('loading','raw_price','settlement','power'):
        assert topology_figure(view,layer=layer).data


def test_coordinates_fill_canvas_without_scaleanchor_cluster_and_resource_symbols_do_not_overlap():
    data=network_data(ROOT);pos=layout(tuple(data['buses'].bus),tuple(zip(data['branches'].from_bus,data['branches'].to_bus)))
    assert np.ptp(list(pos.values()),axis=0)==pytest.approx([2,2])
    row=data['resources'].iloc[0].copy();row['unit']='same_bus'
    data['resources']=pd.concat([data['resources'],row.to_frame().T],ignore_index=True)
    fig=topology_figure(data)
    assert fig.layout.yaxis.scaleanchor is None
    assert fig.layout.legend.xanchor=='left'  # Override the app template's right anchor.
    assert fig.layout.margin.autoexpand is False
    assert fig.layout.xaxis.domain==(0,1) and fig.layout.yaxis.domain==(0,1)
    coal=next(t for t in fig.data if t.name=='燃煤')
    assert len(set(zip(coal.x,coal.y)))==len(coal.x)
    assert {'燃煤','储能','光伏','区外来电','负荷接入点'}<=set(t.name for t in fig.data)


def test_app_input_switch_period_and_saved_refresh(tmp_path):
    from streamlit.testing.v1 import AppTest
    root=scene(tmp_path)
    script=f'''
import sys
sys.path[:0]=[{str(ROOT/'app')!r},{str(ROOT/'src')!r}]
from network_view import render_input_network
from smm.config import load_config
render_input_network(load_config(root={str(root)!r}),'net')
'''
    at=AppTest.from_string(script,default_timeout=30).run();assert not at.exception
    at.radio(key='net:input_view').set_value('时段负荷输入').run()
    at.selectbox(key='net:input_stage').set_value('rt').run()
    at.select_slider(key='net:input_period').set_value(17).run()
    value=next(m.value for m in at.metric if '节点负荷需求' in m.label)
    path=root/'config/base.yaml';base=yaml.safe_load(path.read_text());base['load_scale']*=.8
    path.write_text(yaml.safe_dump(base));at.run();assert not at.exception
    assert next(m.value for m in at.metric if '节点负荷需求' in m.label)!=value
    assert not any('结算节点价' in m.label for m in at.metric)


def test_saved_csv_price_contract_uses_clipped_lmp_and_raw_lmp_raw():
    from test_network_view import fixture_result
    r=fixture_result();r['rt_prices']['lmp_raw']=[9000.,-600.]
    r['rt_prices']['lmp']=[1500.,-50.]
    v=result_snapshot(network_data(ROOT),r,'rt',0)['buses'].set_index('bus')
    assert v.loc[19,'lmp']==9000. and v.loc[19,'lmp_clamped']==1500.


@pytest.mark.parametrize('mutate',['none','config','bids','profiles'])
def test_cli_does_not_claim_unsaved_effective_inputs_equal(tmp_path,mutate):
    from smm.pipeline import prepare
    from smm.run_input_provenance import freeze_cli_inputs
    root=scene(tmp_path);cfg=load_config(root=root)
    case=prepare(cfg,read_only=True,verbose=False)
    if mutate=='config':cfg.mode['intraday']['enabled']=not cfg.mode['intraday']['enabled']
    if mutate=='bids':case.bids['units']['C1']['noload_cost']+=10
    if mutate=='profiles':case.profiles.loc[0,'load_actual']+=1
    opts=run_options('full',8);out=tmp_path/'cli'
    freeze_cli_inputs(cfg,case,out,source_options=opts)
    result=compare_source_version(out,root,options=opts)
    assert result['status']==('equal' if mutate=='none' else 'unknown')


def test_missing_digest_or_incomplete_run_options_never_claim_equal(tmp_path):
    root,out,opts=frozen(tmp_path);p=out/'frozen_input_provenance.json';original=json.loads(p.read_text())
    assert compare_source_version(out,root,options={})['status']=='unknown'
    for key in ('current_quote','resolved_bids'):
        bad=copy.deepcopy(original);bad['source_version'][key]=None;p.write_text(json.dumps(bad))
        assert compare_source_version(out,root,options=opts)['status']=='unknown'
    bad=copy.deepcopy(original);bad['source_version']['run_options']={};p.write_text(json.dumps(bad))
    assert compare_source_version(out,root,options=opts)['status']=='unknown'


def test_cli_reads_source_and_copies_network_under_scene_lock(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from smm.pipeline import prepare
    import smm.run_input_provenance as module
    import smm.unit_edit_transaction as transaction
    root=scene(tmp_path);cfg=load_config(root=root);case=prepare(cfg,read_only=True,verbose=False)
    original=transaction.scene_input_lock;depth=[0];copied=[];copyfile=module.shutil.copyfile
    @contextmanager
    def tracked(path,*args,**kwargs):
        with original(path,*args,**kwargs):
            depth[0]+=1
            try:yield
            finally:depth[0]-=1
    def checked_copy(*args,**kwargs):
        assert depth[0]>0;copied.append(args[0]);return copyfile(*args,**kwargs)
    monkeypatch.setattr(transaction,'scene_input_lock',tracked)
    monkeypatch.setattr(module.shutil,'copyfile',checked_copy)
    module.freeze_cli_inputs(cfg,case,tmp_path/'locked',source_options=run_options('full',8))
    assert len(copied)==2 and depth[0]==0


def test_cli_external_bid_version_keeps_effective_snapshot_without_false_equal(tmp_path):
    from smm.pipeline import prepare
    from smm.run_input_provenance import freeze_cli_inputs
    root=scene(tmp_path);cfg=load_config(root=root);case=prepare(cfg,read_only=True,verbose=False)
    case.bids['version']=99999
    out=tmp_path/'external';freeze_cli_inputs(cfg,case,out,source_options=run_options('full',8))
    actual=json.loads((out/'scenario_snapshot/resolved_bids.json').read_text())
    assert actual['version']==99999
    assert compare_source_version(out,root,options=run_options('full',8))['status']=='unknown'


def test_expected_legacy_missing_provenance_has_short_business_notice(tmp_path):
    result=compare_source_version(tmp_path,ROOT,options=run_options())
    assert result['status']=='unknown'
    assert '历史运行未记录源输入清单' in result['message']
    assert 'Errno' not in result['message'] and str(tmp_path) not in result['message']
    (tmp_path/'frozen_input_provenance.json').write_text('{broken')
    broken=compare_source_version(tmp_path,ROOT,options=run_options())
    assert broken['status']=='unknown' and broken.get('detail')
    assert 'line 1' not in broken['message']


def test_price_node_numbers_have_wcag_contrast_on_every_viridis_color():
    from network_view import contrast_text_color,price_text_colors
    from plotly.colors import sample_colorscale
    for position in np.linspace(0,1,101):
        rgb=sample_colorscale('Viridis',[float(position)])[0]
        channels=[float(c.strip())/255 for c in rgb[4:-1].split(',')]
        linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
        luminance=sum(c*w for c,w in zip(linear,(.2126,.7152,.0722)))
        text=contrast_text_color(rgb)
        contrast=(luminance+.05)/.05 if text=='#000000' else 1.05/(luminance+.05)
        assert contrast>=4.5
    assert price_text_colors([0.,1.,np.nan],0,1)==['#ffffff','#000000','#000000']
    from test_network_view import fixture_result
    r=fixture_result();r['rt_prices']['lmp_raw']=[39.,48.]
    view=result_snapshot(network_data(ROOT),r,'rt',0)
    fig=topology_figure(view,layer='settlement')
    nodes=next(t for t in fig.data if t.name=='母线编号')
    assert len(nodes.textfont.color)==39
    plain=topology_figure(network_data(ROOT))
    assert next(t for t in plain.data if t.name=='母线编号').textfont.color=='white'


@pytest.mark.parametrize('field',['rate','endpoint','reactance','ptdf','load_share','base_load','buses','ref_bus'])
def test_cli_actual_network_mutation_is_unknown_and_blocks_false_frozen_topology(tmp_path,field):
    from smm.pipeline import prepare
    from smm.run_input_provenance import freeze_cli_inputs
    from network_view import frozen_network_usable
    root=scene(tmp_path);cfg=load_config(root=root);case=prepare(cfg,read_only=True,verbose=False)
    if field=='rate':case.net.branches.loc[0,'rate_mw']+=1
    if field=='endpoint':case.net.branches.loc[0,'to_bus']=3
    if field=='reactance':case.net.branches.loc[0,'x_pu']+=.001
    if field=='ptdf':case.net.ptdf[0,0]+=.001
    if field=='load_share':case.net.load_share.iloc[0]+=.001
    if field=='base_load':case.net.base_load_mw+=1
    if field=='buses':case.net.buses=case.net.buses[::-1]
    if field=='ref_bus':case.net.ref_bus=30
    opts=run_options('full',8);out=tmp_path/'net';snap=freeze_cli_inputs(cfg,case,out,source_options=opts)
    saved=json.loads((out/'frozen_input_provenance.json').read_text())['source_version']
    assert saved['effective_network_matches_source'] is False
    assert saved['frozen_network_verified'] is False
    assert compare_source_version(out,root,options=opts)['status']=='unknown'
    assert not frozen_network_usable(snap)


def test_app_rejects_explicitly_unverified_cli_network_without_fallback(tmp_path):
    from streamlit.testing.v1 import AppTest
    root=scene(tmp_path)
    (root/'provenance.json').write_text(json.dumps({'frozen_network_verified':False}))
    script=f"""
import sys
sys.path[:0]=[{str(ROOT/'app')!r},{str(ROOT/'src')!r}]
from network_view import render_result_network
render_result_network({{'snapshot_root':{str(root)!r}}})
"""
    at=AppTest.from_string(script,default_timeout=30).run()
    assert not at.exception and not at.selectbox
    assert any('冻结网络证据不足' in x.value for x in at.warning)
