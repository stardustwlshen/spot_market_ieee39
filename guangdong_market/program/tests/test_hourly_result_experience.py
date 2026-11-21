"""Exercise the real Streamlit result pane after a simulated result was opened."""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_hourly_prices_directions_and_filter_even_without_costs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    accounts = [
        ('C1', 'coal', 'discharge', '发电'),
        ('VRE:33', 'vre', 'discharge', '发电'),
        ('ES1', 'independent_storage', 'discharge', '放电'),
        ('ES1', 'independent_storage', 'charge', '充电'),
        ('PS1', 'pumped_storage', 'discharge', '发电'),
        ('PS1', 'pumped_storage', 'charge', '抽水'),
    ]
    result = {'run_id': 'research-only', 'hour': 0, 'rows': [
        dict(resource=resource, technology=technology, direction=direction, node='1',
             start='2026-01-01T00:00:00', end='2026-01-01T01:00:00',
             QDA_MWh='10', simulated_executed_MWh='11', deviation_MWh='1',
             DA_hour_price='100', RT_hour_price='110', energy_amount_CNY='1110')
        for resource, technology, direction, _ in accounts
    ]}
    result['hour_prices'] = {stage: {'1': {'aggregation': 'quarter_clip_then_arithmetic_mean_four'}} for stage in ('DA','RT')}
    p = tmp_path / 'hour.json'
    p.write_text(json.dumps(result))
    app = AppTest.from_string(f"""
import sys,json
from pathlib import Path
sys.path.insert(0,{str(root/'app')!r})
from gd_settlement_panel import render_energy_hour
render_energy_hour(json.loads(Path({str(p)!r}).read_text()),economics_inputs={{'incomplete': True}})
""").run()
    assert not app.exception
    assert any('经济性来源检查失败' in x.value for x in app.error)
    assert any('4 个 15 分钟价格' in x.value for x in app.caption)
    result_grid = app.dataframe[-1].value
    assert list(result_grid['方向']) == [a[3] for a in accounts]
    for field, expected in [('日前小时电价（元/MWh）','100'),('实时小时电价（元/MWh）','110')]:
        assert set(result_grid[field]) == {expected}
    app.selectbox[0].select('PS1').run()
    assert not app.exception
    assert list(app.dataframe[-1].value['机组']) == ['PS1','PS1']
    assert list(app.dataframe[-1].value['方向']) == ['发电','抽水']
    app.selectbox[0].select('C1').run()
    assert not app.exception
    assert list(app.dataframe[-1].value['机组']) == ['C1']
    assert list(app.dataframe[-1].value['方向']) == ['发电']
