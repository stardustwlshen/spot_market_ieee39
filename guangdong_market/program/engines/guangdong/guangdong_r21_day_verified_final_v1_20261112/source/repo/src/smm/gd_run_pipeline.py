"""Phase-4-based reconstructed reference pipeline, not recovered phase 5/6.

New DA -> that exact DA's continuous RT -> conditional DA research price ->
independent actual-information benchmark. Unsupported RT pricing stops explicitly.
No history stitching, automatic reserve changes, solver fallback or zero filling.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import csv
import importlib
import json
from pathlib import Path
import sys

from .gd_run_archive import (RunArchive, RunArchiveError, STAGES, atomic_json,
    digest, file_sha256, no_symlinks, read_run, within, REQUIRED_INPUT_FILES)

ROOT = Path(__file__).resolve().parents[3]
INPUT_FILES = REQUIRED_INPUT_FILES
DEFAULT_STAGES = ('market_da', 'market_rt', 'da_research_price', 'actual_hindsight')


class StageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RunConfig:
    snapshot_dir: Path = ROOT / 'inputs/ieee39_v6'
    output_root: Path = ROOT / 'reconstruction/runs'
    execution_mode: str = 'prepare'
    requested_stages: tuple[str, ...] = DEFAULT_STAGES
    run_id: str | None = None
    execution_count: int = 96
    window_periods: int = 16
    acknowledge_offline_rt: bool = False
    acknowledge_synthetic_actual: bool = False
    native_solver_authorized: bool = False

    def validate(self):
        if self.execution_mode not in {'prepare', 'build', 'native_highs', 'contract_test'}:
            raise ValueError('explicit prepare/build/native_highs/contract_test mode required')
        if (not isinstance(self.requested_stages, tuple) or len(set(self.requested_stages)) != len(self.requested_stages)
                or any(s not in STAGES[1:] for s in self.requested_stages)):
            raise ValueError('explicit unique requested stage tuple required')
        if not {'market_da', 'market_rt'} <= set(self.requested_stages):
            raise ValueError('this pipeline requires market_da and market_rt')
        if type(self.execution_count) is not int or not 1 <= self.execution_count <= 96:
            raise ValueError('execution_count must be 1..96; a short run is not full-day acceptance')
        if type(self.window_periods) is not int or self.window_periods < 1:
            raise ValueError('window_periods must be positive integer')
        for flag in ('acknowledge_offline_rt', 'acknowledge_synthetic_actual', 'native_solver_authorized'):
            if type(getattr(self, flag)) is not bool:
                raise ValueError(flag + ': explicit Boolean required')
        return self

    def record(self):
        result = asdict(self)
        result.update(snapshot_dir=str(Path(self.snapshot_dir).absolute()),
                      output_root=str(Path(self.output_root).absolute()),
                      requested_stages=list(self.requested_stages), backend='highs',
                      threads=1, requested_mip_rel_gap=.001,
                      model_version='phase4_based_reconstructed_reference',
                      rules_2026_10_fully_implemented=False)
        return result


@dataclass(frozen=True)
class StageResult:
    summary: dict
    selected_path: Path | None = None
    selected_dispatch_id: str | None = None
    native_verified: bool = False


@dataclass
class RunContext:
    config: RunConfig
    archive: RunArchive
    snapshot_dir: Path | None = None

    @property
    def selected(self):
        return deepcopy(self.archive.manifest['selected_dispatch'])

    def directory(self, stage):
        path = within(self.archive.path, 'stages/' + stage)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def emit(self, stage, event):
        self.archive.event(stage, event)

    def summary(self, stage, summary):
        return self.archive.write(stage, 'summary.json', dict(
            schema='gd_rebuilt_stage/v1', run_id=self.archive.manifest['run_id'], stage=stage,
            selected_dispatch_id=None if self.selected is None else self.selected['dispatch_id'],
            input_sha256=self.archive.manifest['input_sha256'],
            source_sha256=self.archive.manifest['source_sha256'],
            execution_mode=self.config.execution_mode,
            model_version='phase4_based_reconstructed_reference', official_price=None,
            settlement_cash=None, summary=summary), role='stage_summary')


def _source_files():
    # Explicit implementation dependencies, not UI files being edited separately.
    names = ('__init__', 'gd_run_archive', 'gd_run_pipeline', 'gd_reference_workflow', 'gd_rt_roll',
        'gd_rt_inputs', 'gd_hindsight', 'gd_price_research', 'gd_lmp_reference_adapter',
        'gd_lmp', 'gd_price_basis', 'gd_solve', 'gd_energy_core', 'gd_storage_core',
        'gd_horizon', 'gd_ieee39_inputs', 'gd_piecewise_bids', 'gd_dispatch_reserves',
        'gd_pumped_plan_boundary', 'gd_service_boundaries', 'gd_agc_delivery', 'gd_regulation_math',
        'highs_solver', 'highs_worker', 'highs_gap', 'rule_evidence')
    return [*(ROOT / 'repo/src/smm' / (n + '.py') for n in names),
        *(ROOT / 'experiments' / (n + '.py') for n in ('run_rebuilt_market',
            'run_ieee39_da_reference', 'run_ieee39_rt_reference', 'run_ieee39_da_pricing_research'))]


def _freeze_inputs(ctx, source_files):
    archive = ctx.archive
    snapshot = no_symlinks(ctx.config.snapshot_dir)
    dest = ctx.directory('inputs') / 'frozen'
    for name in INPUT_FILES:
        source = within(snapshot, name)
        if not source.is_file():
            raise RunArchiveError('missing explicit frozen input: ' + name)
        data = source.read_bytes()
        target = within(dest, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        sha = file_sha256(target)
        if file_sha256(source) != sha:
            raise RunArchiveError('input changed while copying: ' + name)
        archive.manifest['inputs'].append(dict(name=name, source_path=str(source), sha256=sha))
        archive.register('inputs', target, role='frozen_input')
    archive.manifest['input_sha256'] = digest({r['name']: r['sha256'] for r in archive.manifest['inputs']})
    if not source_files:
        raise RunArchiveError('explicit implementation source inventory required')
    for path in source_files:
        path = no_symlinks(path)
        archive.manifest['sources'].append(dict(name=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name,
            source_path=str(path), sha256=file_sha256(path)))
    if len({r['name'] for r in archive.manifest['sources']}) != len(archive.manifest['sources']):
        raise RunArchiveError('duplicate implementation source identity')
    archive.manifest['source_sha256'] = digest({r['name']: r['sha256'] for r in archive.manifest['sources']})
    archive.persist()
    ctx.snapshot_dir = dest


def _block_remaining(archive, reason):
    for name, row in archive.manifest['stages'].items():
        if row['status'] == 'not_started':
            archive.stage(name, 'blocked', reason=reason)


def run_market_pipeline(config=None, *, backend=None, source_files=None):
    """Run one new archive and return read_run view, including on handled failures.

    `contract_test` requires an injected non-native backend and cannot expose
    numerical result capabilities. Native solving needs three explicit opt-ins;
    it is implemented but was NOT executed or licensed during reconstruction.
    Hard process kills retain `running`/incomplete, never a completion marker.
    There is intentionally no resume/retry/import-history API.
    """
    config = (config or RunConfig()).validate()
    archive = RunArchive(config.output_root, config=config.record(),
                         requested=('inputs', *config.requested_stages), run_id=config.run_id)
    ctx = RunContext(config, archive)
    current = 'inputs'
    try:
        if config.execution_mode == 'native_highs':
            if not config.native_solver_authorized:
                raise StageUnavailable('native HiGHS not authorized; no solve attempted')
            if not config.acknowledge_offline_rt:
                raise StageUnavailable('native RT requires explicit acknowledgement of offline future-conditioned curves')
            if 'actual_hindsight' in config.requested_stages and not config.acknowledge_synthetic_actual:
                raise StageUnavailable('actual benchmark requires explicit synthetic-actual acknowledgement')
        if config.execution_mode == 'contract_test' and backend is None:
            raise StageUnavailable('contract_test requires an explicit test backend; no solver substitute')
        backend = backend or PackagedReferenceBackend()
        expected = 'contract_test' if config.execution_mode == 'contract_test' else 'highs'
        if getattr(backend, 'backend_name', None) != expected:
            raise StageUnavailable('backend mismatch; HiGHS only, no native/stub substitution')
        archive.stage('inputs', 'running')
        _freeze_inputs(ctx, _source_files() if source_files is None else source_files)
        backend.prepare(ctx, build=config.execution_mode == 'build')
        archive.collect('inputs')
        archive.verify_frozen()
        ctx.summary('inputs', dict(input_files=len(INPUT_FILES), input_hash=archive.manifest['input_sha256'],
            native_solver_called=False, built=config.execution_mode == 'build',
            full_model_feasibility_verified=False))
        archive.stage('inputs', 'completed')
        if config.execution_mode in {'prepare', 'build'}:
            _block_remaining(archive, 'no native solve requested; new selected DA is not available')
            archive.finish('prepared' if config.execution_mode == 'prepare' else 'build_checked')
            return read_run(archive.path)
        if config.execution_mode == 'native_highs':
            from .gd_reference_workflow import native_workflow_path_intact
            if type(backend) is not PackagedReferenceBackend or not native_workflow_path_intact():
                raise StageUnavailable('native HiGHS requires original internal solver path; mock backend is not native evidence')
        for current in STAGES[1:]:
            if current not in config.requested_stages:
                continue
            archive.verify_frozen()
            if current != 'market_da':
                selected = ctx.selected
                if selected is None:
                    raise RunArchiveError('no selected dispatch from this run')
                archive.manifest['stages'][current]['selected_dispatch_id'] = selected['dispatch_id']
            archive.stage(current, 'running')
            method = getattr(backend, current, None)
            if method is None:
                raise StageUnavailable(current + ': no implemented adapter; no fabricated output')
            if config.execution_mode == 'native_highs' and (getattr(method, '__module__', None) != __name__ or
                    Path(getattr(getattr(method, '__func__', method), '__code__').co_filename).name != 'gd_run_pipeline.py'):
                raise StageUnavailable('patched stage method is not native evidence')
            result = method(ctx)
            if config.execution_mode == 'native_highs' and not native_workflow_path_intact():
                raise StageUnavailable('native solver path changed during stage')
            if not isinstance(result, StageResult) or not isinstance(result.summary, dict):
                raise RunArchiveError('adapter must return typed StageResult')
            if result.native_verified != (config.execution_mode == 'native_highs'):
                raise RunArchiveError('native result evidence does not match execution mode')
            if current != 'market_da' and result.selected_dispatch_id != ctx.selected['dispatch_id']:
                raise RunArchiveError('downstream adapter returned another selected dispatch identity')
            archive.verify_frozen()
            archive.collect(current)
            if current == 'market_da':
                if result.selected_path is None:
                    raise RunArchiveError('DA succeeded without selected dispatch artifact')
                selected = archive.register(current, result.selected_path, role='selected_dispatch')
                binding = dict(dispatch_id=archive.manifest['run_id'] + ':market_da:selected',
                    run_id=archive.manifest['run_id'], path=selected['path'], sha256=selected['sha256'],
                    input_sha256=archive.manifest['input_sha256'], source_sha256=archive.manifest['source_sha256'])
                archive.manifest['selected_dispatch'] = binding
                archive.manifest['stages'][current]['selected_dispatch_id'] = binding['dispatch_id']
                archive.collect(current)
            ctx.summary(current, result.summary)
            archive.stage(current, 'completed')
        archive.verify_frozen()
        archive.finish('contract_test_completed' if config.execution_mode == 'contract_test' else 'completed')
    except BaseException as exc:
        # KeyboardInterrupt/SystemExit are retained as interrupted, not successful.
        interrupted = not isinstance(exc, Exception)
        state = 'interrupted' if interrupted else 'unavailable' if isinstance(exc, StageUnavailable) else 'failed'
        reason = type(exc).__name__ + ': ' + str(exc)
        try:
            archive.collect(current)
            archive.write(current, 'failure.json', dict(error=reason, interrupted=interrupted,
                no_automatic_retry=True, no_automatic_boundary_adjustment=True), role='failure')
            if archive.manifest['stages'][current]['status'] not in {'completed', 'failed', 'interrupted', 'unavailable'}:
                archive.stage(current, state, reason=reason)
            _block_remaining(archive, 'stopped after ' + current + ': ' + reason)
            archive.finish('interrupted' if interrupted else 'unavailable' if isinstance(exc, StageUnavailable) else 'failed', reason=reason)
        except Exception as recording_error:
            # Leave the last atomic manifest as running/incomplete if storage fails.
            raise RunArchiveError('run stopped; failure recording also failed: ' + str(recording_error)) from exc
    return read_run(archive.path)


def _experiments():
    directory = str(ROOT / 'experiments')
    if directory not in sys.path:
        sys.path.insert(0, directory)
    modules = [importlib.import_module(name) for name in
        ('run_ieee39_da_reference', 'run_ieee39_rt_reference', 'run_ieee39_da_pricing_research')]
    for module in modules:
        if Path(module.__file__).resolve().parent != ROOT / 'experiments':
            raise RunArchiveError('experiment module from another checkout is forbidden')
    return modules


def _write_rows(path, rows):
    rows = list(rows)
    if not rows:
        raise RunArchiveError('empty result rows cannot represent a successful stage')
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


def _validate_native_stages(result):
    for solution in result['stages'].values():
        row = solution.metrics
        if (row.get('backend') != 'highs' or not row.get('solver_version') or not row.get('native_status') or
                not isinstance(row.get('solver_settings'), dict) or row.get('requested_mip_rel_gap') != .001 or
                row.get('requested_gap_met') is not True or
                not 0 <= row.get('gap', float('inf')) <= .001 + 1e-9 or
                row.get('termination') not in {'optimal', 'maxTimeLimit'}):
            raise RunArchiveError('original native HiGHS stage/gap evidence failed')


class PackagedReferenceBackend:
    """Adapters for the preserved phase-4 components. No physical rules changed."""
    backend_name = 'highs'

    def prepare(self, ctx, *, build):
        self.da, self.rt, self.price = _experiments()
        mapping = ctx.directory('inputs') / 'mapping'
        mapping.mkdir()
        self.workflow = self.da.prepare(mapping, snapshot_dir=ctx.snapshot_dir)
        self.actual = self.hindsight_kwargs = None
        if 'actual_hindsight' in ctx.config.requested_stages:
            self._prepare_actual(ctx, mapping)
        if build:
            import pyomo.environ as pyo
            import smm.gd_reference_workflow as wf
            from .gd_energy_core import build_energy_core
            builds = [('market_da', self.workflow)]
            if self.hindsight_kwargs is not None:
                builds.append(('actual_hindsight', self.hindsight_kwargs))
            for name, kw in builds:
                model = wf._attach(build_energy_core(**kw['core_inputs']), kw['core_inputs'], 'DA_SCUC',
                    kw['reserve_by_stage']['DA_SCUC'], kw['storage_by_stage']['DA_SCUC'],
                    piecewise_bids=kw['piecewise_bids'])
                ctx.archive.write('inputs', name + '_build.json', dict(
                    variables=sum(1 for _ in model.component_data_objects(pyo.Var)),
                    constraints=sum(1 for _ in model.component_data_objects(pyo.Constraint, active=True)),
                    native_solver_called=False, full_model_feasibility_verified=False,
                    scope='SCUC build only; SCED requires actual selected SCUC commitment'), role='build_check')

    def _prepare_actual(self, ctx, mapping):
        from .gd_hindsight import HindsightActualInputs, FIXED_BOUNDARIES, prepare_hindsight_reference
        frozen = json.loads((mapping / 'frozen_source_inputs.json').read_text())
        profiles, units, net = frozen['profiles'], frozen['config']['units'], frozen['network']
        ids = self.workflow['core_inputs']['horizon'].ids
        buses = self.workflow['core_inputs']['buses']
        required = ['load_actual', *(u['id'] + '_actual' for u in units['renewable'])]
        if any(name not in profiles or len(profiles[name]) != 96 for name in required):
            raise ValueError('96 explicit actual values required; no forecast fallback')
        source = 'Same-run frozen synthetic actual research; not measured operations.'
        provenance = {name: dict(kind='synthetic_actual', source=source + ' ' + name)
                      for name in ['load', *(u['id'] for u in units['renewable'])]}
        provenance['physical_tie'] = dict(kind='fixed_external_schedule', source=source + ' Fixed TIE_sched, not measured tie.')
        self.actual = HindsightActualInputs(
            load_mw={(b, t): net['load_share'][str(b)] * profiles['load_actual'][t] for b in buses for t in ids},
            renewable_available_mw={u['id']: {t: profiles[u['id'] + '_actual'][t] for t in ids} for u in units['renewable']},
            physical_tie_mw=deepcopy(self.workflow['core_inputs']['physical_tie_mw']), provenance=provenance,
            fixed_boundaries={key: dict(kind='fixed_scenario_boundary',
                source=source + ' Preserved phase-4 DA-template ' + key + ', including synthetic zero DA AGC; no 5 MW SCUC reservation.')
                for key in FIXED_BOUNDARIES})
        self.hindsight_kwargs, audit = prepare_hindsight_reference(workflow_inputs=self.workflow, actual=self.actual)
        ctx.archive.write('inputs', 'actual_input_audit.json', self.da.plain(audit), role='input_audit')

    def _run_workflow(self, ctx, stage, callable_, **kwargs):
        from .gd_reference_workflow import WorkflowSolveError
        out = ctx.directory(stage)
        try:
            result = callable_(**kwargs, stage_callback=lambda e: ctx.emit(stage, e))
        except WorkflowSolveError as exc:
            for name, solution in exc.stages.items():
                self.da.export_stage(out, name, solution)
            ctx.archive.write(stage, 'workflow_failure.json', dict(error=str(exc), events=exc.events), role='failure')
            raise
        _validate_native_stages(result)
        for name, solution in result['stages'].items():
            self.da.export_stage(out, name, solution)
            physical = json.loads((out / name / 'physical_summary.json').read_text())
            if any(r['residual'] > 1e-5 for r in physical['active_constraint_violations_gt_1e_6']):
                raise RunArchiveError('native stage constraint residual above 1e-5: ' + name)
        ctx.archive.write(stage, 'workflow_audit.json', self.da.plain(result['audit']), role='workflow_audit')
        ctx.archive.write(stage, 'terminal_state.json', self.da.plain(result['executed_boundary']), role='terminal_state')
        return result

    def market_da(self, ctx):
        from .gd_reference_workflow import run_day_ahead_reference
        self.market_result = self._run_workflow(ctx, 'market_da', run_day_ahead_reference, **self.workflow)
        selected = self.market_result['selected_dispatch']
        self.market_selected_fingerprint = digest(self.price.snapshot(selected.model))
        name = next(n for n, value in self.market_result['stages'].items() if value is selected)
        from .gd_hindsight import dispatch_rows, resource_rows
        out = ctx.directory('market_da')
        _write_rows(out / 'dispatch_by_period.csv', dispatch_rows(selected.model, self.workflow['services']))
        _write_rows(out / 'resource_dispatch.csv', resource_rows(selected.model))
        return StageResult(dict(selected_stage=name, metrics=selected.metrics,
            physical_rows='dispatch_by_period.csv', resource_rows='resource_dispatch.csv',
            AGC_boundary='preserved phase-4 synthetic zero DA', physical_cost_yuan=None),
            selected_path=out / name / 'variables.csv', native_verified=True)

    def market_rt(self, ctx):
        from .gd_rt_roll import RTRollError, run_rt_roll
        selected = ctx.selected
        out = ctx.directory('market_rt')
        provider = self.rt.IEEE39RTProvider(out,
            offline_future_truth_conditioned=ctx.config.acknowledge_offline_rt,
            window_periods=ctx.config.window_periods, snapshot_dir=ctx.snapshot_dir,
            selected_da_source=within(ctx.archive.path, selected['path']),
            selected_dispatch_id=selected['dispatch_id'])
        if provider.reference.source_sha256 != selected['sha256']:
            raise RunArchiveError('RT read a different selected DA')
        kwargs = provider.roll_kwargs(tuple(range(ctx.config.execution_count)))
        completed = 0
        def event(entry):
            nonlocal completed
            provider.on_event(entry)
            if entry['event'] == 'window_completed':
                completed += 1
                # Persist only runner-confirmed executed intervals for the UI.
                self.rt.write_rows(out / 'executed_confirmed_intervals.csv', provider.executed_rows[:completed])
                ctx.archive.write('market_rt', 'progress.json', dict(completed_execution_intervals=completed,
                    requested_execution_intervals=ctx.config.execution_count,
                    selected_dispatch_id=selected['dispatch_id']), role='progress')
            ctx.emit('market_rt', entry)
        kwargs['event_callback'] = event
        try:
            report = run_rt_roll(**kwargs)
        except RTRollError as exc:
            self.rt.save(out / 'roll_report.json', exc.report)
            self.rt.write_rows(out / 'executed_confirmed_intervals.csv', provider.executed_rows[:len(exc.report['completed_windows'])])
            raise
        self.rt.save(out / 'roll_report.json', report)
        count = len(report['completed_windows'])
        if (not report['completed'] or not report['native_numerical_acceptance'] or count != ctx.config.execution_count
                or report.get('selected_da_source_sha256') != selected['sha256']):
            raise RunArchiveError('RT completion or DA identity validation failed')
        self.market_rows = [dict(r, duration_h=.25, renewable_available_mw=r['renewable_mw'] + r['curtailment_mw'])
                            for r in provider.executed_rows[:count]]
        self.market_summary = dict(completed=True,
            scope='Same-run first executed RT intervals, offline future-conditioned forecasts, not actual replay',
            quoted_objective_cost_yuan=sum(r['quoted_objective_cost_yuan'] for r in self.market_rows),
            cost_components_yuan={k: sum(r[k] for r in self.market_rows) for k in self.market_rows[0] if k.endswith('_cost')},
            selected_dispatch_id=selected['dispatch_id'])
        return StageResult(dict(**self.market_summary, completed_execution_intervals=count,
            requested_execution_intervals=ctx.config.execution_count, full_RT96_completed=count == 96,
            physical_rows='executed_confirmed_intervals.csv', causal_online_forecast=False,
            actual_replay=False, AGC_boundary='preserved phase-4 synthetic C1 5 MW'),
            selected_dispatch_id=selected['dispatch_id'], native_verified=True)

    def da_research_price(self, ctx):
        import pyomo.environ as pyo
        from pyomo.core.base.componentuid import ComponentUID
        import smm.gd_reference_workflow as wf
        from .gd_lmp import build_pricing_lp, solve_pricing_lp
        selected = self.market_result['selected_dispatch']
        before = self.price.snapshot(selected.model)
        if digest(before) != self.market_selected_fingerprint:
            raise RunArchiveError('selected DA in-memory model changed since its registered dispatch export')
        source, held = wf._pricing_source(selected)
        context = dict(stage='DA', horizon=self.workflow['core_inputs']['horizon'],
            adjusted_thermal_bounds=deepcopy(self.market_result['adjusted_thermal_bounds']),
            held_boundaries=deepcopy(held), original_dispatch_metrics=deepcopy(self.market_result['dispatch'].metrics),
            source_scope='phase4_based_reconstructed_reference; not official LMP',
            sensitivity_policy='reviewed_stage_constraints_hold_load_caps_constant')
        spec = self.price.factory(source, context)
        expected = {str(ComponentUID(c)) for comp in (source.gd_reserve.c_up, source.gd_reserve.c_down) for c in comp.values()}
        if spec.stage != 'DA' or {r.constraint_ref for r in spec.omitted_dispatch_rows} != expected:
            raise RunArchiveError('independent DA research pricing row review mismatch')
        lp = build_pricing_lp(source, spec, sced_result=selected.native_result)
        settings = self.workflow['settings']
        price = solve_pricing_lp(lp, time_limit_s=settings.time_limit_s, threads=1, mip_rel_gap=.001)
        if price.backend != 'highs' or price.termination_condition != 'optimal':
            raise RunArchiveError('conditional pricing did not solve with HiGHS')
        if before != self.price.snapshot(selected.model):
            raise RunArchiveError('independent pricing mutated selected DA')
        rows = [dict(node=n, period=int(t), raw_conditional_yuan_per_mwh=v,
                     final_official_price=None) for (n, t), v in price.raw_lmp_yuan_per_mwh.items()]
        if len(rows) != len(selected.model.B) * len(selected.model.T):
            raise RunArchiveError('conditional price coverage incomplete')
        ctx.archive.write('da_research_price', 'raw_prices.json', rows, role='research_price_rows')
        ctx.archive.write('da_research_price', 'pricing_audit.json', dict(
            audit=price.audit, held_boundaries=held, sensitivity_policy=context['sensitivity_policy'],
            omitted_dispatch_rows=[asdict(r) for r in spec.omitted_dispatch_rows],
            selected_dispatch_unchanged=True, official=False,
            finite_difference_diagnostics='not_run_by_rebuilt_adapter'), role='pricing_audit')
        return StageResult(dict(conditional_price_count=len(rows),
            min_raw_yuan_per_mwh=min(r['raw_conditional_yuan_per_mwh'] for r in rows),
            max_raw_yuan_per_mwh=max(r['raw_conditional_yuan_per_mwh'] for r in rows),
            raw_price_only=True, official_mapping_verified=False, cash_eligible=False,
            source='same selected DA; independent pricing LP, not SCED balance dual',
            finite_difference_diagnostics_verified=False),
            selected_dispatch_id=ctx.selected['dispatch_id'], native_verified=True)

    def rt_research_price(self, ctx):
        raise StageUnavailable('RT independent pricing inventory/slack/emergency mapping unresolved; no price or cash generated')

    def actual_hindsight(self, ctx):
        from .gd_hindsight import (run_hindsight_reference, dispatch_rows, resource_rows,
                                   compare_dispatch_reports)
        if ctx.config.execution_count != 96:
            raise StageUnavailable('full-day hindsight comparison requires this run completed RT96; short-run results are retained')
        result = self._run_workflow(ctx, 'actual_hindsight', run_hindsight_reference,
                                   workflow_inputs=self.workflow, actual=self.actual)
        out = ctx.directory('actual_hindsight')
        model = result['selected_dispatch'].model
        rows = dispatch_rows(model, self.hindsight_kwargs['services'])
        _write_rows(out / 'dispatch_by_period.csv', rows)
        _write_rows(out / 'resource_dispatch.csv', resource_rows(model))
        comparison = compare_dispatch_reports(hindsight=result['comparison_summary'],
            market_rt=self.market_summary, hindsight_rows=rows, market_rows=self.market_rows)
        ctx.archive.write('actual_hindsight', 'comparison.json', self.da.plain(comparison), role='same_run_comparison')
        ctx.archive.write('actual_hindsight', 'hindsight_audit.json', self.da.plain(result['hindsight_audit']), role='input_audit')
        return StageResult(dict(**self.da.plain(result['comparison_summary']), synthetic_actual=True,
            market_DA_anchors_used=False, same_run_market_comparison=True, exact_optimum_claim=False,
            strict_lower_bound_claim=False, quoted_difference_is_savings=False,
            physical_rows='dispatch_by_period.csv', resource_rows='resource_dispatch.csv'),
            selected_dispatch_id=ctx.selected['dispatch_id'], native_verified=True)
