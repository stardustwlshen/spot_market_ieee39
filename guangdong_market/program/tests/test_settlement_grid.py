import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/gd_live_market_runtime'))
from settlement_grid import hour_ticks,hour_energy
from decimal import Decimal as D
import pytest

def test_hour_grid_distinct():
 assert hour_ticks(1,5)==list(range(12,24))
 assert hour_ticks(1,15)==list(range(4,8))
def test_DA_preserves_all_twelve_original_samples():
 assert hour_energy([0]*4+[120]*8,5)==80
 assert hour_energy([0,0,120,120],15)==60
@pytest.mark.parametrize('count,dt',[(4,5),(12,15),(3,15)])
def test_wrong_grid_rejected(count,dt):
 with pytest.raises(ValueError):hour_energy([100]*count,dt)
def test_equal_hour_energy_after_quarter_aggregation():
 original=[D(i) for i in range(12)]
 means=[sum(original[i:i+3])/3 for i in range(0,12,3)]
 assert hour_energy(original,5)==hour_energy(means,15)
def test_signed_charge_once():
 assert hour_energy([-100]*4,15)==-100
def test_nonfinite_rejected():
 with pytest.raises(ValueError):hour_energy(['NaN']*4,15)
def test_GD_hour_price_not_cell_cost_sum():
 # Hourly arithmetic market price, not an energy-weighted custom rule.
 qda=hour_energy([100]*12,5);actual=hour_energy([0,0,0,400],15)
 da_price=D(100);rt_prices=[D(10),D(20),D(30),D(400)]
 assert qda*da_price+(actual-qda)*sum(rt_prices)/4==10000
