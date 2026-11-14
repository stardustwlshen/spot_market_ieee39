"""Explicit energy chronology. A 98-point shape is not a production-rule proof.

Representative-point duration/ramp/state mapping has no hidden default. Its
interpretation is recorded as a research assumption, not inferred from dates.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must explicitly identify source or assumption")


@dataclass(frozen=True)
class EnergyPeriod:
    index: int
    duration_h: float
    ramp_elapsed_h: float
    role: str
    settle: bool

    def validate(self):
        if type(self.index) is not int or self.index < 0:
            raise ValueError("period index must be a nonnegative integer")
        _positive(self.duration_h, "duration_h")
        _positive(self.ramp_elapsed_h, "ramp_elapsed_h")
        if self.role not in ("delivery", "representative"):
            raise ValueError("unknown period role")
        if type(self.settle) is not bool:
            raise ValueError("settle must be an explicit Boolean")
        if self.role == "representative" and self.settle:
            raise ValueError("representative points cannot settle current-day energy")
        return self


@dataclass(frozen=True)
class EnergyHorizon:
    periods: tuple[EnergyPeriod, ...]
    mode: str
    time_mapping_source: str
    representative_mapping_assumption: str | None = None

    def validate(self):
        if not isinstance(self.periods, tuple) or not self.periods:
            raise ValueError("nonempty immutable period tuple required")
        if not all(isinstance(p, EnergyPeriod) for p in self.periods):
            raise ValueError("all periods must be EnergyPeriod")
        for period in self.periods:
            period.validate()
        if len(set(self.ids)) != len(self.periods):
            raise ValueError("period IDs must be unique; chronology is tuple order")
        if self.mode not in ("day_ahead_reference_98", "day_ahead_research_96", "day_ahead_research_5min", "real_time", "real_time_5min", "component"):
            raise ValueError("unsupported horizon mode")
        _text(self.time_mapping_source, "time_mapping_source")
        representative = any(p.role == "representative" for p in self.periods)
        if representative:
            _text(self.representative_mapping_assumption, "representative_mapping_assumption")
        elif self.representative_mapping_assumption is not None:
            raise ValueError("representative mapping provided without representative points")
        if self.mode in ("day_ahead_reference_98", "day_ahead_research_96"):
            expected = 98 if self.mode == "day_ahead_reference_98" else 96
            if len(self.periods) != expected:
                raise ValueError(f"{self.mode} requires exactly {expected} points")
            if any(p.role != "delivery" or not p.settle or abs(p.duration_h-.25) > 1e-12
                   for p in self.periods[:96]):
                raise ValueError("D-day must have 96 settling quarter-hour delivery points")
            if expected == 98 and any(p.role != "representative" or p.settle for p in self.periods[96:]):
                raise ValueError("D+1 tail must consist of two nonsettling representative points")
        if self.mode == "day_ahead_research_5min":
            if len(self.periods) < 288 or len(self.periods) % 3 or any(
                    p.role != "delivery" or not p.settle or abs(p.duration_h-1/12) > 1e-12
                    or abs(p.ramp_elapsed_h-1/12) > 1e-12 for p in self.periods):
                raise ValueError("DA five-minute research requires a full day and whole quarters, not official DA98")
        if self.mode == "real_time":
            if any(p.role != "delivery" for p in self.periods):
                raise ValueError("RT representative points are unsupported")
            if any(abs(p.duration_h-.25)>1e-12 for p in self.periods):
                raise ValueError("RT delivery points must be quarter-hours")
            if not self.periods[0].settle or any(p.settle for p in self.periods[1:]):
                raise ValueError("RT settles only the first executed interval")
        if self.mode == "real_time_5min":
            if len(self.periods) != 24:
                raise ValueError("five-minute RT requires exactly 24 physical periods")
            if any(p.role != "delivery" or abs(p.duration_h-1/12) > 1e-12
                   or abs(p.ramp_elapsed_h-1/12) > 1e-12 for p in self.periods):
                raise ValueError("five-minute RT requires five-minute duration and ramp elapsed time")
            if any(p.settle != (i < 3) for i, p in enumerate(self.periods)):
                raise ValueError("five-minute RT flags only the first three execution candidates")
        return self

    @property
    def ids(self):
        return tuple(p.index for p in self.periods)

    @property
    def duration_h(self):
        return {p.index: float(p.duration_h) for p in self.periods}

    @property
    def ramp_elapsed_h(self):
        return {p.index: float(p.ramp_elapsed_h) for p in self.periods}

    @property
    def previous(self):
        return {p.index: self.periods[n-1].index if n else None for n,p in enumerate(self.periods)}

    @property
    def start_h(self):
        clock = 0.
        result = {}
        for p in self.periods:
            result[p.index] = clock
            clock = math.fsum((clock, float(p.duration_h)))
        return result

    @property
    def end_h(self):
        starts = self.start_h
        return {p.index: math.fsum((starts[p.index], float(p.duration_h))) for p in self.periods}

    @property
    def settlement_ids(self):
        return tuple(p.index for p in self.periods if p.settle)

    def audit(self):
        self.validate()
        return {"mode": self.mode, "periods": len(self.periods),
                "settlement_ids": list(self.settlement_ids),
                "time_mapping_source": self.time_mapping_source,
                "representative_mapping_assumption": self.representative_mapping_assumption,
                "chronology": "explicit_linear_discrete_chain",
                "production_98_point_equivalence_verified": False,
                "periods_detail": [dict(index=p.index, duration_h=p.duration_h,
                    ramp_elapsed_h=p.ramp_elapsed_h, role=p.role, settle=p.settle,
                    previous=self.previous[p.index], start_h=self.start_h[p.index]) for p in self.periods]}
