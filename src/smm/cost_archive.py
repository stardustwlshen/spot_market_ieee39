"""Explicit, missing-only import of the repository's synthetic cost assumptions.

Reading a scene never upgrades it. Each destination is published complete with
an exclusive hard link, never replace(): concurrent writers cannot be clobbered.
After a partial failure, completed files stay intact and a fresh plan can import
only what is still missing. No bids, physical inputs or existing costs are edited.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import tempfile

from .config import load_config
from .process_control import file_lock

COST_FILES = ('fixed_costs.yaml', 'bid_cost_basis.yaml')


def missing_cost_archives(root):
    # A dangling symlink is occupied, not permission to overwrite its target.
    return tuple(name for name in COST_FILES
                 if not os.path.lexists(Path(root) / 'config' / name))


@dataclass(frozen=True)
class CostArchiveImport:
    target: Path
    source: Path
    missing: tuple[str, ...]
    contents: tuple[bytes, ...]
    snapshot: tuple[tuple[str, str | None], ...]

    @property
    def fingerprint(self):
        return hashlib.sha256(repr(self).encode()).hexdigest()


def _snapshot(target, source, missing):
    paths = [target / 'config' / name for name in
             ('base.yaml', 'units.yaml', 'modes.yaml', *COST_FILES)]
    paths += [source / 'config' / name for name in missing]
    result = []
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f'成本补档不处理符号链接或非普通文件，请人工核对: {path}')
        result.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()
                       if path.exists() else None))
    return tuple(result)


def prepare_cost_archive_import(target, source):
    """Read and validate an explicit import plan without changing either root."""
    import yaml
    try:
        return _prepare_cost_archive_import(target, source)
    except yaml.YAMLError as exc:
        raise ValueError(f'场景或成本来源的 YAML 语法无效，未补档：{exc}') from exc


def _prepare_cost_archive_import(target, source):
    from .bid_cost_basis import research_cost_worksheet
    from .bids import _unit_meta
    from .profit import load_fixed_cost_profile

    target, source = Path(target).resolve(), Path(source).resolve()
    for root in (target, source):
        if (root / 'config').is_symlink() or not (root / 'config').is_dir():
            raise ValueError(f'请核对普通场景配置目录，不自动处理目录链接: {root / "config"}')
    if os.path.lexists(target / 'config' / 'generated_research_costs.json'):
        raise ValueError('当前场景已有按机组参数生成的成本，请使用页面生成流程；旧补档不会替换生效成本。')
    missing = missing_cost_archives(target)
    snapshot = _snapshot(target, source, missing)
    cfg = load_config(root=target)
    contents = tuple((source / 'config' / name).read_bytes() for name in missing)
    proposed = dict(zip(missing, contents))
    import yaml
    for name, content in proposed.items():
        data = yaml.safe_load(content)
        if not isinstance(data, dict) or data.get('source_kind') != 'research_assumption_not_observed_plant_data':
            raise ValueError(f'{name}: 来源未明确标识为合成研究假设，请人工核对')
    with tempfile.TemporaryDirectory(prefix='smm-cost-check-') as temporary:
        root = Path(temporary)
        (root / 'config').mkdir()
        for name in COST_FILES:
            content = proposed[name] if name in proposed else (target / 'config' / name).read_bytes()
            (root / 'config' / name).write_bytes(content)
        checked = replace(cfg, root=root)
        sheet = research_cost_worksheet(checked)
        fixed = load_fixed_cost_profile(checked)
        for uid, unit in _unit_meta(cfg).items():
            typ = unit['type']
            if uid not in fixed['units'] or fixed['units'][uid]['type'] != typ:
                raise ValueError(f'{uid}: 成本档案缺少匹配类型的记录，不会推测或补造')
            group = ('thermal' if typ in ('coal', 'gas_ccgt', 'gas_gt') else
                     'renewable_vom' if typ in ('wind', 'pv') else typ)
            if uid not in sheet[group]:
                raise ValueError(f'{uid}: 报价依据缺少研究记录，不会推测或补造')
            if typ == 'hydro' and cfg.base['season'] not in sheet[group][uid]['water_opportunity']:
                raise ValueError(f'{uid}: 报价依据缺少当前季节的水价值')
    if _snapshot(target, source, missing) != snapshot:
        raise RuntimeError('场景或来源在核对期间变化，请重新核对后采用。')
    return CostArchiveImport(target, source, missing, contents, snapshot)


def apply_cost_archive_import(plan):
    """Call only after explicitly choosing the displayed research assumptions."""
    created = []
    with file_lock(plan.target / '.cost_archive.lock'):
        fresh = prepare_cost_archive_import(plan.target, plan.source)
        if fresh != plan:
            raise RuntimeError('场景或成本来源已变化，请重新核对后采用；未覆盖任何文件。')
        try:
            for name, content in zip(plan.missing, plan.contents):
                destination = plan.target / 'config' / name
                # Stage on the same filesystem, fsync, then publish without overwrite.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.cost-', delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, destination)
                    created.append(name)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        except OSError as exc:
            completed = '、'.join(created) or '无'
            raise RuntimeError(f'补档未全部完成；已完整补充：{completed}。已有文件未覆盖，'
                               f'请刷新后重试，仅补剩余缺档。原因：{exc}') from exc
    return created
