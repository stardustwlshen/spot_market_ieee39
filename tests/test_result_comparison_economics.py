"""Pure saved-result comparisons and a read-only real-archive prefix regression."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pandas as pd
import numpy as np
import pytest
from result_comparison import type_energy_comparison, reserve_price_comparison
from result_economics import build_unit_economics,summarize_unit_economics

def test_type_prefix_dt_and_missing_type():
    da=pd.DataFrame([dict(unit=u,type=t,period=p,P_mw=v) for u,t,v in [('C','coal',10),('W','wind',20)] for p in range(4)])
    rt=pd.DataFrame([dict(unit='C',type='coal',period=p,P_mw=12) for p in range(2)])
    x=type_energy_comparison({'summary':{'dt_h':.25},'da_unit_awards':da,'rt_units':rt}).set_index('type')
    assert x.loc['coal','日前电量 MWh']==5
    assert x.loc['coal','实时电量 MWh']==6
    assert x.loc['wind','日前电量 MWh']==10
    assert pd.isna(x.loc['wind','实时电量 MWh'])

def test_type_missing_period_not_partial_sum():
    da=pd.DataFrame([dict(unit='C',type='coal',period=0,P_mw=10)])
    rt=pd.DataFrame([dict(unit='C',type='coal',period=p,P_mw=12) for p in range(2)])
    x=type_energy_comparison({'summary':{'dt_h':1},'da_unit_awards':da,'rt_units':rt})
    assert pd.isna(x.iloc[0]['日前电量 MWh'])
    assert x.iloc[0]['实时电量 MWh']==24

def test_reserve_selected_price_and_actual_weights():
    p=pd.DataFrame([dict(period=t,bus=b,lmp=999,lmp_clamped=price,lmp_raw=price+10,load_mw=w) for t in range(2) for b,price,w in [(1,10,1),(2,30,3)]])
    r={'summary':{'dt_h':1},'rt_reserve_system':pd.DataFrame([dict(period=0,R_up_short=0),dict(period=1,R_up_short=5)]),'da_nodal_prices':p,'rt_prices':p}
    x=reserve_price_comparison(r)
    assert len(x)==1
    assert x.iloc[0]['实时结算价（节点负荷加权）元/MWh']==25
    assert x.iloc[0]['日前结算价（节点负荷加权）元/MWh']==25
    assert x.iloc[0]['实时所选定价原始价（节点负荷加权）元/MWh']==35

def test_reserve_missing_weights_unknown():
    p=pd.DataFrame([dict(period=0,bus=1,lmp_clamped=10)])
    x=reserve_price_comparison({'rt_system':pd.DataFrame([dict(period=0,R_up_short=5)]),'rt_prices':p})
    assert pd.isna(x.iloc[0]['实时结算价（节点负荷加权）元/MWh'])

def test_reserve_missing_frozen_node_unknown():
    p=pd.DataFrame([dict(period=0,bus=1,lmp_clamped=10,load_mw=1)])
    x=reserve_price_comparison({'rt_system':pd.DataFrame([dict(period=0,R_up_short=5)]),'rt_prices':p},pd.DataFrame({'bus':[1,2],'load_mw':[1,3]}))
    assert pd.isna(x.iloc[0]['实时结算价（节点负荷加权）元/MWh'])

def test_real_eight_period_prefix_read_only():
    run=Path(__file__).resolve().parents[1]/'.local-app/results-preview-20261108-8509/repo/runs/默认/20261108-011528-491028_full'
    if not run.is_dir():pytest.skip('Local archive fixture unavailable')
    r={'dir':str(run),'full':True,'summary':json.loads((run/'summary.json').read_text(encoding='utf8'))}
    for f in run.glob('*.csv'):
        try:r[f.stem]=pd.read_csv(f)
        except pd.errors.EmptyDataError:pass
    original=build_unit_economics(r)
    r['summary']=dict(r['summary'],executed_periods=8,completed_scope='debug_prefix',settlement_scope='illustrative_prefix')
    for key,frame in list(r.items()):
        if isinstance(frame,pd.DataFrame) and 'period' in frame and (key.startswith('rt_') or key.startswith('settlement_')):
            r[key]=frame[frame.period.lt(8)].copy()
    # Aggregates are not usable for a shorter scope; retain only the independently saved detail.
    r.pop('settlement_by_entity',None);r.pop('settlement_by_type',None)
    x=build_unit_economics(r); summary=summarize_unit_economics(x)
    assert set(x.period)==set(range(8))
    assert summary.period_count.eq(8).all()
    assert x.groupby('phase').size().nunique()==1
    expected=original[original.period.lt(8)].sort_values(['unit','phase','period']).reset_index(drop=True)
    pd.testing.assert_frame_equal(x.sort_values(['unit','phase','period']).reset_index(drop=True),expected)
    assert np.allclose(x.profit_yuan,x.net_cash_yuan-x.operating_cost_yuan)

def test_type_entire_rt_unit_missing_within_same_type():
    da=pd.DataFrame([dict(unit=u,type='coal',period=p,P_mw=10) for u in ('C1','C2') for p in range(2)])
    rt=pd.DataFrame([dict(unit='C1',type='coal',period=p,P_mw=12) for p in range(2)])
    x=type_energy_comparison({'summary':{'dt_h':.25},'da_unit_awards':da,'rt_units':rt}).set_index('type')
    assert x.loc['coal','日前电量 MWh']==10
    assert pd.isna(x.loc['coal','实时电量 MWh'])
    assert pd.isna(x.loc['coal','实时−日前 MWh'])
