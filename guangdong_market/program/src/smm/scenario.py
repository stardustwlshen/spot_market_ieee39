"""场景：一组边界条件（配置 YAML、网络 CSV、报价 JSON、曲线覆盖 CSV）的命名副本，供网页应用与 scripts/run_scenario.py 使用。

目录布局与仓库根目录一致，因此 load_config(root=场景目录) 后现有流程无需改动：
  scenarios/<名称>/config/{base,units,modes}.yaml
  scenarios/<名称>/data/case39/{buses,branches}.csv
  scenarios/<名称>/data/profile_overrides.csv      （可选：负荷/风光 96 点覆盖）
  scenarios/<名称>/bids/<日期>/MKT/bids_v*.json
  scenarios/<名称>/scenario.json                   （说明、创建时间）
运行结果：runs/<场景>/<YYYYmmdd-HHMMSS>_<da|full>/（meta.json、run.log、da/、实时与结算 CSV、summary.json）。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

from .config import ROOT

SCEN_DIR = ROOT / "scenarios"
RUNS_DIR = ROOT / "runs"
DEFAULT = "默认"
_NAME_OK = re.compile(r"^[\w\u4e00-\u9fff\-\.]{1,60}$")


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_OK.match(name) or name.startswith("."):
        raise ValueError("场景名只能含中文、字母、数字、下划线、短横线、点，长度 1–60")
    return name


def scenario_path(name: str) -> Path:
    return SCEN_DIR / _check_name(name)


def list_scenarios() -> list[str]:
    ensure_default()
    return sorted([p.name for p in SCEN_DIR.iterdir() if (p / "config" / "base.yaml").exists()],
                  key=lambda n: (n != DEFAULT, n))


def _copy_tree(src_root: Path, dst: Path):
    (dst / "config").mkdir(parents=True, exist_ok=True)
    for f in ("base.yaml", "units.yaml", "modes.yaml"):
        shutil.copy2(src_root / "config" / f, dst / "config" / f)
    shutil.copytree(src_root / "data" / "case39", dst / "data" / "case39", dirs_exist_ok=True)
    ov = src_root / "data" / "profile_overrides.csv"
    if ov.exists():
        shutil.copy2(ov, dst / "data" / "profile_overrides.csv")
    if (src_root / "bids").exists():
        shutil.copytree(src_root / "bids", dst / "bids", dirs_exist_ok=True)


def create_scenario(name: str, source: str | None = None, note: str = "") -> Path:
    """新建场景：source=None 复制仓库当前配置；否则复制已有场景。"""
    dst = scenario_path(name)
    if dst.exists():
        raise FileExistsError(f"场景 {name} 已存在")
    src_root = ROOT if source is None else scenario_path(source)
    _copy_tree(src_root, dst)
    meta = dict(name=name, source=source or "仓库 config/", note=note,
                created=_dt.datetime.now().isoformat(timespec="seconds"))
    (dst / "scenario.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def ensure_default() -> Path:
    SCEN_DIR.mkdir(parents=True, exist_ok=True)
    p = SCEN_DIR / DEFAULT
    if not (p / "config" / "base.yaml").exists():
        create_scenario(DEFAULT, note="由仓库 config/ 复制的默认边界条件")
    return p


def delete_scenario(name: str, delete_runs: bool = False):
    if name == DEFAULT:
        raise ValueError("默认场景不能删除（可新建副本后修改）")
    shutil.rmtree(scenario_path(name))
    if delete_runs and (RUNS_DIR / name).exists():
        shutil.rmtree(RUNS_DIR / name)


def scenario_meta(name: str) -> dict:
    p = scenario_path(name) / "scenario.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"name": name}


def update_meta(name: str, **kw):
    m = scenario_meta(name)
    m.update(kw)
    m["modified"] = _dt.datetime.now().isoformat(timespec="seconds")
    (scenario_path(name) / "scenario.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- YAML（保留注释）
def _yaml():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


def read_yaml(name: str, which: str):
    """which ∈ base/units/modes；返回 ruamel 往返对象（可原地修改后 write_yaml，注释保留）。"""
    with open(scenario_path(name) / "config" / f"{which}.yaml", encoding="utf-8") as f:
        return _yaml().load(f)


def write_yaml(name: str, which: str, data):
    p = scenario_path(name) / "config" / f"{which}.yaml"
    with open(p, "w", encoding="utf-8") as f:
        _yaml().dump(data, f)
    update_meta(name)


# ----------------------------------------------------------------------------- 运行目录
def list_runs(scenario: str | None = None) -> list[dict]:
    """按时间倒序返回运行记录（读取 meta.json）。"""
    out = []
    if not RUNS_DIR.exists():
        return out
    dirs = [RUNS_DIR / scenario] if scenario else [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    for sd in dirs:
        if not sd.exists():
            continue
        for rd in sd.iterdir():
            mp = rd / "meta.json"
            if mp.exists():
                try:
                    m = json.loads(mp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                m["dir"] = str(rd)
                out.append(m)
    return sorted(out, key=lambda m: m.get("started", ""), reverse=True)


def new_run_dir(scenario: str, mode: str) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    d = RUNS_DIR / _check_name(scenario) / f"{ts}_{mode}"
    d.mkdir(parents=True, exist_ok=False)
    return d
