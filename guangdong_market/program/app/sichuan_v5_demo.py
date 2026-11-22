"""Declared synthetic inputs built only from the public IEEE39 topology."""
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
import csv
import hashlib
import math
from pathlib import Path

from sichuan_v5_energy import Case, Resource, Line, DT, PERIODS, ClearingError
from sichuan_v5_rules import (
    BidMode, Participant, participation_rule, spot_clear_bounds,
)
from sichuan_v5_segments import research_curve
from sichuan_v5_settlement import SettlementInput, UniformPriceInput, settle_quarter, uniform_price

TOPOLOGY = Path(__file__).resolve().parents[1] / 'data/case39'
RULE_PDF_SHA256 = '8e8ee5bebadb0f0ae1873315790b47ed8694a68b8ca4d1e160ec669e74c42ad5'
D = Decimal
CATEGORY = {
    'public_coal': 'public_coal',
    'provincial_hydro': 'provincial_hydro',
    'central_renewable': 'centralized_renewables',
    'competitive_pv': 'centralized_renewables',
    'independent_storage': 'independent_storage',
}


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(month, *, load_factor=1., solar_factor=1., coal_offer=320.):
    if (type(month) is not int or not 1<=month<=12 or not .65<=load_factor<=1.25
            or not .5<=solar_factor<=1.3 or not 220<=coal_offer<=500):
        raise ClearingError('合成场景或报价超出允许演示范围')
    buses, demand0 = [], {}
    with (TOPOLOGY/'buses.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            b = int(row['bus'])
            buses.append(b)
            demand0[b]=float(row['load_mw'])
    with (TOPOLOGY/'branches.csv').open(newline='') as f:
        lines=tuple(Line(row['branch_id'],int(row['from_bus']),int(row['to_bus']),
                    float(row['x_pu']),float(row['rate_mw']))
                    for row in csv.DictReader(f))
    pv=participation_rule(Participant.COMPETITIVE_PV,month).bid_mode
    # On quantity-only days solar is an eligible zero-objective-priority
    # variable within its available quantity, not a submitted zero-price offer.
    rs = (
        Resource('C1',31,'public_coal','thermal',1800.,coal_offer,280.,220.,650.,8,8,24,24000.,segments=research_curve('coal',1800.,coal_offer,coal_minimum_mw=280.)),
        Resource('C2',4,'public_coal','thermal',1200.,390.,140.,180.,500.,8,8,24,22000.,segments=research_curve('coal',1200.,390.,coal_minimum_mw=140.)),
        Resource('C3',36,'public_coal','thermal',1100.,420.,130.,220.,300.,8,8,24,22000.,segments=research_curve('coal',1100.,420.,coal_minimum_mw=130.)),
        Resource('H1',30,'provincial_hydro','hydro',1200.,105.,daily_water_budget_mwh=14000.,segments=research_curve('reservoir',1200.,105.)),
        Resource('H2',38,'provincial_hydro','hydro',1250.,120.,daily_water_budget_mwh=17500.,segments=research_curve('reservoir',1250.,120.)),
        Resource('H3',39,'provincial_hydro','hydro',1350.,125.,daily_water_budget_mwh=18600.,segments=research_curve('reservoir',1350.,125.)),
        Resource('W1',37,'central_renewable','renewable',550.,15.,segments=research_curve('renewable',550.,15.)),
        Resource('PV1',35,'competitive_pv','renewable',600.,40. if pv==BidMode.QUANTITY_PRICE else 0.,
                 segments=research_curve('renewable',600.,40.) if pv==BidMode.QUANTITY_PRICE else ()),
        Resource('G1',34,'gas','fixed',220.,0.),
        Resource('ES1',33,'independent_storage','storage',90.,0.,energy_mwh=500.,initial_energy_mwh=230.),
    )
    demand=[]; available=[]; storage=[]
    for t in range(PERIODS):
        h=t/4.
        shape=.73 + .143*math.exp(-((h-11.5)/4.2)**2) + .20*math.exp(-((h-19)/3.2)**2)
        demand.append({b:round(demand0[b]*shape*load_factor,6) for b in buses})
        available.append({
            'W1':round(330.+95.*math.cos(2*math.pi*(h-4)/24.),6),
            'PV1':round(min(600.,500.*solar_factor*max(0.,math.sin(math.pi*(h-6.)/12.))) if 6<h<18 else 0.,6),
            'G1':160.,
        })
        # Charge and discharge balance exactly at 0.9 × 0.9 efficiency.
        storage.append({'ES1': -35. if 11<=h<14 else 28.35 if 18<=h<21 else 0.})
    case = Case(tuple(buses),lines,rs,tuple(demand),tuple(available),tuple(storage))
    declarations = {r.id: participation_rule(Participant(r.role),month).bid_mode.value for r in rs}
    return case, {
        'month':month, 'load_factor':load_factor, 'solar_factor':solar_factor, 'coal_offer':coal_offer,
        'declared_participation':declarations, 'study_type':'synthetic_lossless_dc',
        'rules_pdf_sha256':RULE_PDF_SHA256,
        'topology_sha256': {name:_digest(TOPOLOGY/name) for name in ('buses.csv','branches.csv')},
        'case':asdict(case),
    }


def forecast_callback(error):
    """Use only previous synthetic measurements to perturb RT hours.

    First cell uses the synthetic realized interval mean; not an actual
    pre-interval forecast. A known synthetic error is not read in future cells.
    """
    if not 0.<=error<=.08:
        raise ClearingError('合成实时偏差无效')
    from dataclasses import replace
    def source(case,t,stop):
        demand=list(case.demand)
        for k in range(t,stop):
            obs=t if k==t else max(0,t-1)
            scale=1.+error*math.sin(2*math.pi*(obs+7)/96)*(1. if k==t else .7**(k-t))
            demand[k]={b:round(mw*scale,6) for b,mw in demand[k].items()}
        return replace(case,demand=tuple(demand))
    return source


def research_values(case,row):
    """Bind displayed RT research amount to exact executed physical quarter."""
    lo,hi=(D(str(x)) for x in spot_clear_bounds())
    bad=(max(row['shed'].values())>1e-6 or max(row['spill'].values())>1e-6
         or row['reliability_reserve_short_mw']>1e-6)
    raw=row['raw_nodal_prices']
    nodal={b:None if bad else min(hi,max(lo,D(str(raw[b])))).quantize(D('.01'),rounding=ROUND_HALF_UP)
           for b in case.buses}
    val=lambda mw:D(str(mw))*D(str(DT))
    eligible=[UniformPriceInput(r.id,CATEGORY[r.role],val(row['power'][r.id]),nodal[r.bus])
              for r in case.resources if r.role in CATEGORY]
    uniform=uniform_price(eligible)
    out=[]
    for r in case.resources:
        if r.role not in CATEGORY:
            continue # Fixed non-market injections are not assigned spot money.
        role='independent_storage' if r.kind=='storage' else 'generator'
        inp=SettlementInput(row['quarter'],r.id,role,val(row['power'][r.id]),
                            val(row['charge'].get(r.id,0.)),nodal[r.bus],uniform)
        out.append(_result(inp))
    for b,mw in row['demand'].items():
        if mw>0:
            out.append(_result(SettlementInput(row['quarter'],f'负荷-{b}','user',D(0),
                      val(mw-row['shed'][b]),None,uniform)))
    return {'quarter':row['quarter'],'valuation_status':'suppressed_reliability_slack' if bad else
            'provisional_rt_only','cleared_nodal_price':{b:str(v) if v is not None else None for b,v in nodal.items()},
            'uniform_price':str(uniform) if uniform is not None else None,'rows':out}


def _result(inp):
    acc=settle_quarter(inp)
    output = {**asdict(inp), **asdict(acc)}
    return {key:str(value) if isinstance(value,D) else value for key,value in output.items()}
