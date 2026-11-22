"""Explicit synthetic boundary update for V5.0 §8; never an auction."""
from dataclasses import dataclass, replace
from math import isfinite


@dataclass(frozen=True)
class Update:
    effective_quarter: int
    load_factor: float
    reason: str = '日内负荷预测显著变化（合成声明）'

    def __post_init__(self):
        if type(self.effective_quarter) is not int or not 0<=self.effective_quarter<96:
            raise ValueError('日内更新时段必须是0至95的整数')
        if (type(self.load_factor) not in (int,float) or not isfinite(self.load_factor)
                or not .8<=self.load_factor<=1.2 or abs(self.load_factor-1.)<1.e-12):
            raise ValueError('日内更新必须声明非零负荷变化，倍率限0.8–1.2')
        if not isinstance(self.reason,str) or not self.reason.strip() or len(self.reason)>120:
            raise ValueError('须说明日内负荷预测变化原因')


def amended_case(case, update):
    if not isinstance(update,Update):
        raise ValueError('日内边界更新未被显式声明')
    rows=tuple({b: round(p*update.load_factor,6) for b,p in q.items()}
               for q in case.demand[update.effective_quarter:])
    return replace(case,demand=(*case.demand[:update.effective_quarter],*rows))
