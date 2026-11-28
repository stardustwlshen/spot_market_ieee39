"""Closed-loop, pre-scheduled pumped hydro for the Dadu day-ahead study."""
from dataclasses import replace

from sichuan_v5_dadu_case import build_case
from sichuan_v5_energy import ClearingError, DT, PERIODS, Resource


PSH = 'PSH1'


def build_day_case(template, seed=39):
    case, meta = build_case(template,seed)
    unit = Resource(PSH,16,'pumped_storage','storage',1200.,0.,energy_mwh=7200.,
                    initial_energy_mwh=3600.,charge_efficiency=.75,discharge_efficiency=1.)
    # Inventory is deliverable electricity, so the complete cycle loss is on pumping.
    pv = [u.id for u in case.resources if u.kind=='renewable']
    net = [sum(d.values())-sum(a[u] for u in pv)-sum(gas.values())
           for d,a,gas in zip(case.demand,case.available,case.fixed_schedule)]
    pump_n,gen_n = 16,12
    # Optimize two contiguous blocks jointly on D-1 forecasts. Leave at least
    # one idle quarter between modes. Earliest equal-score pair wins.
    pump,gen = max(((a,b) for a in range(PERIODS-pump_n-gen_n)
                    for b in range(a+pump_n+1,PERIODS-gen_n+1)),
                   key=lambda ab:sum(net[ab[1]:ab[1]+gen_n])/gen_n-sum(net[ab[0]:ab[0]+pump_n])/pump_n)
    signed = [0.]*PERIODS
    signed[pump:pump+pump_n] = [-unit.capacity_mw]*pump_n
    signed[gen:gen+gen_n] = [unit.capacity_mw]*gen_n
    filing = dict(id=PSH,name='模拟抽水蓄能电站',bus=unit.bus,capacity_mw=unit.capacity_mw,
                  pump_capacity_mw=unit.capacity_mw,usable_energy_mwh=unit.energy_mwh,
                  initial_mwh=unit.initial_energy_mwh,terminal_mwh=unit.initial_energy_mwh,
                  cycle_efficiency=unit.charge_efficiency,pump_start=pump,pump_quarters=pump_n,
                  generate_start=gen,generate_quarters=gen_n,signed_mw=signed,
                  plan_basis='事前预测负荷扣除光伏和气电计划，选取先抽后发、平均净负荷差最大的连续 4 小时和 3 小时。')
    asset = dict(id=PSH,name=filing['name'],bus=unit.bus,capacity_mw=unit.capacity_mw,kind=unit.kind,role=unit.role)
    market = replace(case,resources=(*case.resources,unit),
                     available=tuple({**a,PSH:unit.capacity_mw} for a in case.available),
                     storage_plan=tuple({PSH:mw} for mw in signed))
    return market,dict(meta,assets=[*meta['assets'],asset],pumped_storage=filing)


def audit_pumped(case, rows):
    unit = next(u for u in case.resources if u.id==PSH)
    stock = unit.initial_energy_mwh
    pumped = generated = residual = 0.
    for t,r in enumerate(rows):
        pump,gen = r['charge'][PSH],r['power'][PSH]
        if (min(pump,gen)<-1e-6 or min(pump,gen)>1e-6 or max(pump,gen)>case.available[t][PSH]+1e-6
                or abs(gen-pump-case.storage_plan[t][PSH])>1e-5):
            raise ClearingError(f'抽蓄第 {t+1} 段的抽发功率与日前计划不符')
        pumped += DT*pump
        generated += DT*gen
        stock += DT*(unit.charge_efficiency*pump-gen/unit.discharge_efficiency)
        residual = max(residual,abs(stock-r['energy'][PSH]),-stock,stock-unit.energy_mwh)
    residual = max(residual,abs(stock-unit.initial_energy_mwh))
    if len(rows)!=PERIODS or residual>1e-4:
        raise ClearingError('抽蓄库存、时段数或日末回补校核失败')
    return dict(pumping_mwh=pumped,generation_mwh=generated,cycle_loss_mwh=pumped-generated,
                terminal_mwh=stock,max_inventory_error_mwh=residual)
