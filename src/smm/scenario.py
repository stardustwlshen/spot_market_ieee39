"""场景：一组边界条件（配置 YAML、网络 CSV、报价 JSON、曲线覆盖 CSV）的命名副本，供网页应用与 scripts/run_scenario.py 使用。

目录布局与仓库根目录一致，因此 load_config(root=场景目录) 后现有流程无需改动：
  scenarios/<名称>/config/{base,units,modes}.yaml
  scenarios/<名称>/config/{fixed_costs,bid_cost_basis}.yaml （可选成本档案）
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
import tempfile
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


def _copy_tree(src_root: Path, dst: Path, *, copy_current_quote: bool = True):
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(src_root):
        return _copy_tree_unlocked(src_root, dst, copy_current_quote=copy_current_quote)


def _copy_tree_unlocked(src_root: Path, dst: Path, *, copy_current_quote: bool = True):
    # Claim a new destination atomically, including concurrent create requests.
    # Never merge into or overwrite an existing scene.
    dst.mkdir(parents=True, exist_ok=False)
    try:
        _copy_tree_contents(src_root, dst, copy_current_quote=copy_current_quote)
    except BaseException:
        # Only remove the directory claimed by this call, never a competitor's.
        shutil.rmtree(dst)
        raise


def _copy_tree_contents(src_root: Path, dst: Path, *, copy_current_quote: bool):
    (dst / "config").mkdir()
    for f in ("base.yaml", "units.yaml", "modes.yaml"):
        shutil.copy2(src_root / "config" / f, dst / "config" / f)
    for f in ("fixed_costs.yaml", "bid_cost_basis.yaml", "generated_research_costs.json", "spot_market_costs.yaml"):
        source = src_root / "config" / f
        if source.exists():
            shutil.copy2(source, dst / "config" / f)
    shutil.copytree(src_root / "data" / "case39", dst / "data" / "case39", dirs_exist_ok=True)
    ov = src_root / "data" / "profile_overrides.csv"
    if ov.exists():
        shutil.copy2(ov, dst / "data" / "profile_overrides.csv")
    if (src_root / "bids").exists():
        shutil.copytree(src_root / "bids", dst / "bids", dirs_exist_ok=True)
    # The entire workspace (draft, reset baseline, history and manual markers)
    # belongs to the scene, not only to typical scenarios. Quote-only writers
    # replace this file atomically; the outer scene lock also keeps unit edits
    # and their matching quote together without taking writer locks in reverse.
    if copy_current_quote and (src_root / "current_quote.json").exists():
        shutil.copy2(src_root / "current_quote.json", dst / "current_quote.json")
    # A copied typical scenario retains its original curve reference. Without
    # these files another composition would silently compound the old factors.
    if (src_root / "config/typical_scenario.json").exists():
        for relative in ("config/typical_scenario.json", "data/typical_baseline_profiles.csv",
                         "data/typical_source_overrides.csv", "data/typical_source_current_quote.json"):
            source = src_root / relative
            if source.exists():
                shutil.copy2(source, dst / relative)


def create_scenario(name: str, source: str | None = None, note: str = "") -> Path:
    """新建场景：source=None 复制仓库当前配置；否则复制已有场景。"""
    dst = scenario_path(name)
    if dst.exists():
        raise FileExistsError(f"场景 {name} 已存在")
    src_root = ROOT if source is None else scenario_path(source)
    _copy_tree(src_root, dst, copy_current_quote=source is not None)
    meta = dict(name=name, source=source or "仓库 config/", note=note,
                created=_dt.datetime.now().isoformat(timespec="seconds"))
    (dst / "scenario.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    from .research_cost_setup import initialize_missing_cost_defaults
    initialize_missing_cost_defaults(dst)
    from .spot_costs import initialize_new_scene
    initialize_new_scene(dst)
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
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(scenario_path(name)):
        _write_yaml_unlocked(name, which, data)


def _write_yaml_unlocked(name, which, data):
    p = scenario_path(name) / "config" / f"{which}.yaml"
    with open(p, "w", encoding="utf-8") as f:
        _yaml().dump(data, f)
    update_meta(name)


# ----------------------------------------------------------------------------- 运行目录
def _read_run_record(directory: Path, scenario: str) -> dict:
    """Quarantine one unreadable record in memory; never rewrite its evidence."""
    try:
        record = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("meta.json 顶层应为对象记录")
        for key in ("scenario", "mode", "status"):
            if not isinstance(record.get(key), str) or not record[key]:
                raise ValueError(f"meta.json {key} 缺失或不是文本")
        if record['mode'] not in ('da', 'full'):
            raise ValueError("meta.json 运行模式未识别")
        # Terminal batch statuses can appear in imported historical records, but
        # none is promoted to a completed market result. Unknown states stay unsafe.
        known_statuses = {'starting', 'running', 'cancelling', 'done', 'failed', 'stopped',
                          'launch_failed', 'cancelled_before_start', 'process_succeeded', 'process_failed'}
        if record['status'] not in known_statuses:
            raise ValueError("meta.json 运行状态未识别")
        started = record.get('started', '')
        if not isinstance(started, str):
            raise ValueError("meta.json started 不是时间文本")
        if started:
            _dt.datetime.fromisoformat(started.replace('Z', '+00:00'))
        for key in ('stage', 'display_label', 'demo_description'):
            if key in record and record[key] is not None and not isinstance(record[key], str):
                raise ValueError(f"meta.json {key} 不是文本")
        periods = record.get('periods')
        if periods is not None and (isinstance(periods, bool) or not isinstance(periods, int) or periods <= 0):
            raise ValueError("meta.json periods 不是正整数或空值")
        return {**record, 'dir': str(directory), 'started': started}
    except (OSError, ValueError, UnicodeError) as exc:
        return dict(dir=str(directory), scenario=scenario, mode='unknown', status='invalid',
                    started='', stage='运行元数据损坏', metadata_error=str(exc))


def list_runs(scenario: str | None = None) -> list[dict]:
    """Read saved runs newest first, retaining explicitly invalid records safely."""
    out = []
    if not RUNS_DIR.exists():
        return out
    dirs = [RUNS_DIR / scenario] if scenario else [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    for sd in dirs:
        if not sd.is_dir():
            continue
        for rd in sd.iterdir():
            if rd.is_dir() and (rd / 'meta.json').exists():
                out.append(_read_run_record(rd, sd.name))
    return sorted(out, key=lambda m: (m['status'] != 'invalid', m['started']), reverse=True)


def new_run_dir(scenario: str, mode: str) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    d = RUNS_DIR / _check_name(scenario) / f"{ts}_{mode}"
    try:
        d.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        # Wall-clock microseconds are not unique, especially on Windows. Keep
        # existing evidence untouched and let the OS claim a fresh directory
        # exclusively, including competing launches in other processes.
        d = Path(tempfile.mkdtemp(prefix=f"{ts}-", suffix=f"_{mode}", dir=d.parent))
    return d
