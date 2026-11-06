"""Offline validator must not turn absent or partial archived data into a pass."""
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('validate_runs_checked',ROOT/'scripts/validate_runs.py')
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)


@pytest.fixture
def archive(tmp_path,monkeypatch):
    root=tmp_path/'scenario_snapshot';(root/'config').mkdir(parents=True);(root/'data/case39').mkdir(parents=True)
    base={'rule_set':'MKT','periods':2,'dt_h':.25,'season':'wet','tie_line_mode':'supply'}
    units={'thermal':[{'id':'G'}],'renewable':[],
           'storage':[{'id':'S','soc_min':.1,'soc_max':.9,'ecap':200.}],
           'pumped':[{'id':'P','vup_min':0.,'vup_max':100.,'v_total':100.,'vdn_min':0.,'vdn_max':100.}],
           'hydro':[{'id':'H','seasons':{'wet':{'vmin':0.,'vmax':100.,'v0':50.,'v_end':50.}}}], 'tie':[]}
    mode={'price_limits':{'clear_min':-50.,'clear_max':800.},
          'realtime':{'hydro':'value','storage':{'mode':'value'},'lookahead_h':2.,'interval_min':15}}
    for name,data in [('base',base),('units',units),('modes',{'MKT':mode})]:
        (root/f'config/{name}.yaml').write_text(yaml.safe_dump(data))
    pd.DataFrame({'bus':[1,2],'load_mw':[0.,0.]}).to_csv(root/'data/case39/buses.csv',index=False)
    summary={'completed_scope':'full_day','executed_periods':2,'expected_periods':2,
             'checks':{'rt_balance_err_mw':0.},'realtime':{'fallback_count':0},
             'da_summary':{'slack':dict(shed_mwh=0.,surplus_mwh=0.,line_slack_max=0.,anchor_slack_max_mwh=0.,reserve_up_short_max=0.,reserve_dn_short_max=0.)}}
    (tmp_path/'summary.json').write_text(json.dumps(summary))
    def csv(name,rows):pd.DataFrame(rows).to_csv(tmp_path/name,index=False)
    csv('rt_system.csv',[dict(period=t,R_up_short=0.,R_dn_short=0.,reserve_up_short_max_win=0.,reserve_dn_short_max_win=0.,shed_mw=0.,surplus_mw=0.,line_slack_max_win=0.,
                            anchor_slack_max_mwh_win=0.,state_band_slack_max_win=0.) for t in range(2)])
    for prefix in ('da','rt'):
        csv(f'{prefix}_storage.csv',[dict(period=t,unit='S',E_mwh=100.,P_dis=0.,P_ch=0.) for t in range(2)])
        csv(f'{prefix}_pumped.csv',[dict(period=t,unit='P',V_up=50.,P_gen=0.,P_pump=0.) for t in range(2)])
        csv(f'{prefix}_hydro.csv',[dict(period=t,station='H',V_1e4m3=50.) for t in range(2)])
        csv('da_nodal_prices.csv' if prefix=='da' else 'rt_prices.csv',
            [dict(period=t,bus=bus,lmp=100.) for t in range(2) for bus in (1,2)])
    csv('rt_value_windows.csv',[dict(window_start=t,tau=1,kind=k,unit=u,state=x,lower=x,upper=x,band_slack=0.)
                              for t in range(2) for k,u,x in [('E','S',100.),('Vu','P',50.),('V','H',50.)]])
    csv('da_value_curves.csv',[dict(period=t,kind=k,unit=u,value_native=0.,value_yuan_per_mwh=0.,dual_raw=0.)
                             for t in range(2) for k,u in [('E','S'),('Vu','P'),('V','H')]])
    entities=['G','S_放电','S_充电','P_发电','P_抽水','H','平衡松弛(过剩)']
    csv('settlement_detail.csv',[dict(period=t,entity=e,q_da=0.,q_rt=0.,lmp_da=100.,lmp_rt=100.,total=0.) for e in entities for t in range(2)])
    csv('settlement_by_entity.csv',[dict(entity=e,da_amount=0.,rt_amount=0.,total=0.) for e in entities])
    csv('settlement_reconciliation.csv',[dict(stage=s,residual=0.) for s in ('日前','实时偏差','合计')])
    # Reserve auditor is independently covered by its solver-backed tests; this
    # fixture isolates the validator's file/grid gates, never fabricates solver proof.
    from smm import reserve_checks
    monkeypatch.setattr(reserve_checks,'audit',lambda payload:{'violations':[],'max_violation':0.,'passed':True})
    catalog=['G','S','P','H']
    (tmp_path/'reserve_window_evidence.json').write_text(json.dumps({'da':{'periods':2,'window_start':0,'resource_catalog':catalog,'down_enabled':False,'system':[dict(period=k,relative_period=k,R_up_short=0.,R_dn_short=0.) for k in range(2)]},
        'rt_windows':[{'periods':2-t,'window_start':t,'resource_catalog':catalog,'down_enabled':False,'system':[dict(period=t+k,relative_period=k,R_up_short=0.,R_dn_short=0.) for k in range(2-t)]} for t in range(2)]}))
    return tmp_path


def change(path,name,fn):
    p=path/name;a=pd.read_csv(p);a=fn(a);a.to_csv(p,index=False)


def test_complete_file_grid_fixture_passes(archive):
    result=validator.checks(archive)
    assert result['证据完整'] and result['验收通过'],result['说明']


@pytest.mark.parametrize('name,flag',[('da_nodal_prices.csv','价格限值'),('rt_prices.csv','价格限值'),
 ('rt_storage.csv','SOC库容边界'),('da_storage.csv','日末约束'),('rt_pumped.csv','日末约束'),
 ('rt_hydro.csv','日末约束'),('rt_system.csv','松弛为0'),('rt_value_windows.csv','松弛为0'),
 ('settlement_detail.csv','两部制恒等式<1元'),('settlement_by_entity.csv','两部制恒等式<1元'),
 ('settlement_reconciliation.csv','两部制恒等式<1元'),('da_value_curves.csv','价值曲线=−对偶(符号自洽)'),
 ('reserve_window_evidence.json','备用复核通过')])
def test_missing_evidence_cannot_pass(archive,name,flag):
    (archive/name).unlink()
    result=validator.checks(archive)
    assert not result[flag] and not result['证据完整'] and not result['验收通过']


@pytest.mark.parametrize('name',['rt_storage.csv','rt_pumped.csv','rt_hydro.csv','rt_prices.csv','rt_value_windows.csv'])
@pytest.mark.parametrize('damage',['empty','drop_row','duplicate','missing_column','nan'])
def test_partial_corrupt_csv_cannot_pass(archive,name,damage):
    p=archive/name;a=pd.read_csv(p)
    if damage=='empty':a=a.iloc[:0]
    elif damage=='drop_row':a=a.iloc[:-1]
    elif damage=='duplicate':a=pd.concat([a,a.iloc[:1]],ignore_index=True)
    elif damage=='missing_column':a=a.drop(columns=a.columns[-1])
    else:
        col=next(c for c in ['E_mwh','V_up','V_1e4m3','lmp','state'] if c in a);a.loc[a.index[0],col]=np.nan
    a.to_csv(p,index=False)
    result=validator.checks(archive)
    assert not result['验收通过'],(damage,name,result)


@pytest.mark.parametrize('name',['da_nodal_prices.csv','rt_prices.csv'])
def test_price_csv_extension_is_actually_read(archive,name):
    change(archive,name,lambda a:a.assign(lmp=801.))
    result=validator.checks(archive)
    assert not result['价格限值'] and not result['验收通过']


def test_frozen_legacy_floor_not_current_default(archive):
    p=archive/'scenario_snapshot/config/modes.yaml';m=yaml.safe_load(p.read_text());m['MKT']['price_limits']['clear_min']=0.
    p.write_text(yaml.safe_dump(m));change(archive,'rt_prices.csv',lambda a:a.assign(lmp=-1.))
    assert not validator.checks(archive)['价格限值']
    p.unlink();result=validator.checks(archive)
    assert not result['价格限值'] and '不使用当前仓库' in result['说明']


def test_nonzero_state_band_slack_never_hidden_by_old_summary(archive):
    change(archive,'rt_value_windows.csv',lambda a:a.assign(band_slack=60.))
    result=validator.checks(archive)
    assert not result['松弛为0'] and not result['验收通过']
    assert result['储能状态带最大松弛MWh']==60.


def test_missing_entire_resource_in_both_settlement_tables_fails(archive):
    for name in ('settlement_detail.csv','settlement_by_entity.csv'):
        change(archive,name,lambda a:a[a.entity!='S_充电'])
    assert not validator.checks(archive)['两部制恒等式<1元']


def test_metrics_hydro_csv_extension(archive):
    result=validator.metrics(archive)
    assert result['日前H日末库容万m3']==50.
    assert result['实时H日末库容万m3']==50.


def test_failure_exit_code_and_inputs_unchanged(archive,monkeypatch):
    before={p.relative_to(archive):p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    monkeypatch.setattr('sys.argv',['validate_runs.py',str(archive)])
    assert validator.main()==0
    assert before=={p.relative_to(archive):p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    (archive/'rt_prices.csv').unlink()
    assert validator.main()==1


@pytest.mark.parametrize('hourly', [[None, None], [10., None], []])
def test_undefined_zero_load_hourly_price_range_is_unknown(archive, hourly):
    path = archive / 'summary.json'
    summary = json.loads(path.read_text())
    summary['spread'] = {'da_lw_hourly': hourly, 'rt_lw_hourly': hourly}
    path.write_text(json.dumps(summary, allow_nan=False))
    result = validator.metrics(archive)
    assert result['日前小时价差'] is None
    assert result['实时小时价差'] is None
