"""配置读取。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    base: dict
    units: dict
    mode: dict
    root: Path = field(default=ROOT)

    @property
    def rule_set(self) -> str:
        return self.base["rule_set"]

    @property
    def T(self) -> int:
        return int(self.base["periods"])

    @property
    def dt(self) -> float:
        return float(self.base["dt_h"])


def load_config(rule_set: str | None = None, season: str | None = None, overrides: dict | None = None) -> Config:
    base = yaml.safe_load((ROOT / "config" / "base.yaml").read_text(encoding="utf-8"))
    if rule_set:
        base["rule_set"] = rule_set
    if season:
        base["season"] = season
    if overrides:
        base.update(overrides)
    units = yaml.safe_load((ROOT / "config" / "units.yaml").read_text(encoding="utf-8"))
    modes = yaml.safe_load((ROOT / "config" / "modes.yaml").read_text(encoding="utf-8"))
    return Config(base=base, units=units, mode=modes[base["rule_set"]])
