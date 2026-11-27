from pathlib import Path

import pytest


@pytest.fixture
def view(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'app'))
    import sichuan_v5_dadu_bid_decision
    return sichuan_v5_dadu_bid_decision


def example(strategy='water_value', model='run_of_river'):
    count = 5 if model == 'run_of_river' else 10
    unit = {'id': 'A', 'name': '测试站', 'model': model, 'capacity_mw': 100}
    width = 100 / count
    submitted = [20*i for i in range(count)]
    targets = submitted.copy()
    if strategy == 'water_value':
        submitted = [100 + 20*(i-(count-1)/2) for i in range(count)]
        targets = [100.] * count
    offer = {'segments': [[width,p] for p in submitted],
             'natural_inflow_m3s': [45]*96, 'minimum_mw': [0]*96,
             'available_mw': [90]*96, 'technical_available_mw': [95]*96}
    case = {'inputs': {}, 'company': {
        'strategy': strategy, 'as_of': 'D-1 09:45', 'offers': {'A': offer},
        'expected_price_yuan_mwh': [{'A': 185}]*96, 'marginal_water_cost_yuan_mwh': [{'A': 100.}]*96,
        'internal_plan_mw': [{'A': 0}, {'A': 40}, *[{'A': 65}]*94],
        'raw_daily_block_targets_yuan_mwh': {'A': targets}},
        'effective_offers': {'A': [[width,min(p,95)] for p in submitted]}}
    return case, unit


def test_raw_target_projection_and_actual_market_curve_stay_separate(view):
    result, unit = example()
    offer = result['company']['offers']['A']
    assert view.bid_evidence(result, unit, 0)['selected'] is None
    right_edge = view.bid_evidence(result, unit, 1)
    assert right_edge['selected'] == 1
    decision = view.bid_evidence(result, unit, 95)
    assert decision['selected'] == 3 and decision['sample_count'] == 96
    assert decision['blocks'][3] == dict(start=60., end=80., target=100., submitted=120.,
                                         effective=95., comparison=60.)
    assert offer['segments'][3] == [20.,120.]


def test_naive_replays_water_value_comparison_without_market_or_actuals(view):
    result, unit = example(strategy='naive', model='reservoir')
    decision = view.bid_evidence(result, unit, 95)
    assert decision['forecast'] == 185 and decision['marginal'] == 100
    assert decision['comparison'] == 'water_value' and len(decision['blocks']) == 10
    segment = decision['blocks'][6]
    assert segment['target'] == segment['submitted'] == 120
    assert segment['effective'] == 95 and segment['comparison'] == 130


def test_missing_price_and_changed_market_bands_are_never_substituted(view):
    result, unit = example()
    result['company'].pop('raw_daily_block_targets_yuan_mwh')
    result['company']['marginal_water_cost_yuan_mwh'][1] = {'A': None}
    result['effective_offers']['A'][1][0] += 1.
    decision = view.bid_evidence(result, unit, 1)
    assert decision['sample_count'] == 95 and decision['marginal'] is None
    assert all(b['target'] is None and b['effective'] is None for b in decision['blocks'])
    assert decision['blocks'][1]['submitted'] == 80.


@pytest.mark.parametrize('capacity', [None, [], [0.] * 95, [None] * 96, [0.] * 95 + [10.]])
def test_missing_cost_and_incomplete_or_available_capacity_do_not_imply_an_outage(view, capacity):
    result, unit = example('naive')
    result['company'].pop('marginal_water_cost_yuan_mwh')
    result['company']['offers']['A']['technical_available_mw'] = capacity
    evidence = view.bid_evidence(result, unit, 95)
    assert not evidence['outage'] and not evidence['complete_samples']
    assert all(b['comparison'] is None for b in evidence['blocks'])


def test_water_value_replay_requires_all_available_quarters_or_a_certified_outage(view):
    result, unit = example('naive')
    company = result['company']
    company['marginal_water_cost_yuan_mwh'][1] = {'A': None}
    assert all(b['comparison'] is None for b in view.bid_evidence(result, unit, 1)['blocks'])
    company['offers']['A']['technical_available_mw'][1] = 0.
    assert all(b['comparison'] is not None for b in view.bid_evidence(result, unit, 1)['blocks'])
    company.pop('marginal_water_cost_yuan_mwh')
    company['offers']['A']['technical_available_mw'] = [0.] * 96
    evidence = view.bid_evidence(result, unit, 95)
    assert evidence['outage'] and evidence['sample_count'] == 0
    assert [b['comparison'] for b in evidence['blocks']] == [0, 20, 40, 60, 80]


def test_curve_uses_exact_band_edges_and_only_positive_plan_points(view, monkeypatch):
    result, unit = example()
    result['company']['offers']['A']['segments'] = [[15, 0], [25, 40], [60, 80]]
    result['company']['internal_plan_mw'][1] = {'A': 15}
    rendered = []
    monkeypatch.setattr(view.st, 'vega_lite_chart', lambda spec, **kwargs: rendered.append(spec))
    monkeypatch.setattr(view.st, 'caption', lambda text: None)
    for quarter, availability in [(0, 90), (1, 90), (1, 0)]:
        view._frozen_curve(view.bid_evidence(result, unit, quarter), availability)
    assert [len(spec['layer']) for spec in rendered] == [1, 2, 1]
    curve, point = rendered[1]['layer']
    assert curve['mark']['interpolate'] == 'step-after'
    assert [(p['mw'], p['price']) for p in curve['data']['values']] == [(0.,0),(15.,40),(40.,80),(100.,80)]
    assert point['data']['values'] == [{'mw': 15, 'price': 0}]


def _company_page(company, unit):
    from sichuan_v5_dadu_bid_decision import render_bid_decision

    class CompanyStage(dict):
        def __getitem__(self, key):
            assert key in ('company', 'inputs'), key
            return super().__getitem__(key)

        def get(self, key, default=None):
            assert key in ('company', 'inputs'), key
            return super().get(key, default)

    render_bid_decision(CompanyStage(company=company, inputs={}), unit, 95)


def test_company_screen_uses_only_ex_ante_records_and_tolerates_missing_old_fields(view):
    from streamlit.testing.v1 import AppTest

    result, unit = example('naive')
    company = result['company']
    company.pop('marginal_water_cost_yuan_mwh')
    company.pop('expected_price_yuan_mwh')
    for field in ('natural_inflow_m3s', 'minimum_mw', 'available_mw', 'technical_available_mw'):
        company['offers']['A'].pop(field)
    app = AppTest.from_function(_company_page, args=(company, unit)).run()
    assert not app.exception
    copy = ''.join(item.value for item in (*app.markdown, *app.caption))
    assert '内部计划' in copy and '按朴素策略的固定阶梯' in copy
    assert '机组技术可用上限为 未归档' in copy and '无法从档案复算' in copy
    assert '未归档' in copy and '零目标' not in copy and '市场采用' not in copy
    assert '市场采用' not in app.dataframe[0].value
    assert len(app.get('vega_lite_chart')) == 1
