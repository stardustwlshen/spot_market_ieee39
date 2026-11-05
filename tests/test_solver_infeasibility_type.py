"""Only solver-confirmed infeasibility may enable physical-band softening."""
from types import SimpleNamespace

import pyomo.environ as pyo
import pytest

from smm import scuc


class FakeSolver:
    def __init__(self, *, returned=None, native=None, error=None):
        self.returned, self.error, self.native_status = returned, error, native
        self.highs_options = {"time_limit": 10.}
        self._solver_model = None
    def solve(self, *args, **kwargs):
        if self._solver_model is None and self.native_status is not None:
            self._solver_model = SimpleNamespace(getModelStatus=lambda: self.native_status)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(solver=SimpleNamespace(termination_condition=self.returned))


@pytest.mark.parametrize("phase", ["scuc", "formal_sced", "auxiliary_pricing"])
def test_explicit_returned_infeasibility_retains_phase(phase):
    with pytest.raises(scuc.InfeasibleModelError) as error:
        scuc._solve_with_budget(SimpleNamespace(), FakeSolver(returned=pyo.TerminationCondition.infeasible), phase=phase)
    assert error.value.phase == phase
    assert error.value.solver_status == "infeasible"
    assert error.value.primal_solution_available is None
    assert "primal_solution_available=None" in str(error.value)


@pytest.mark.parametrize("status", ["HighsModelStatus.kTimeLimit", "HighsModelStatus.kUnboundedOrInfeasible", "HighsModelStatus.kUnbounded", "unavailable", None])
def test_solver_errors_or_no_solution_are_not_infeasibility(status):
    original = RuntimeError("no solution can be loaded")
    with pytest.raises(RuntimeError) as error:
        scuc._solve_with_budget(SimpleNamespace(), FakeSolver(native=status, error=original), phase="scuc")
    assert error.value is original and not isinstance(error.value, scuc.InfeasibleModelError)


@pytest.mark.parametrize("status", [pyo.TerminationCondition.maxTimeLimit, pyo.TerminationCondition.infeasibleOrUnbounded,
                                    pyo.TerminationCondition.unbounded, pyo.TerminationCondition.solverFailure])
def test_noninfeasible_returned_status_not_classified(status):
    result = scuc._solve_with_budget(SimpleNamespace(), FakeSolver(returned=status))
    assert result.solver.termination_condition == status


def test_explicit_native_infeasibility_is_classified():
    with pytest.raises(scuc.InfeasibleModelError) as error:
        scuc._solve_with_budget(SimpleNamespace(), FakeSolver(native="HighsModelStatus.kInfeasible", error=RuntimeError("no primal")), phase="formal_sced")
    assert error.value.phase == "formal_sced"


def test_budget_exhaustion_is_not_infeasible_even_with_stale_native_status():
    m = SimpleNamespace(_solver_budget=scuc.SolverBudget(0.))
    with pytest.raises(scuc.SolverBudgetExceededError):
        scuc._solve_with_budget(m, FakeSolver(native="HighsModelStatus.kInfeasible", error=RuntimeError("no primal")))


def test_failed_infeasible_call_charges_budget_and_overrun_takes_priority(monkeypatch):
    ticks = iter([0., 2.])
    monkeypatch.setattr(scuc.time, "perf_counter", lambda: next(ticks))
    m = SimpleNamespace(_solver_budget=scuc.SolverBudget(1., reserve_lp_s=0.))
    with pytest.raises(scuc.SolverBudgetExceededError):
        scuc._solve_with_budget(m, FakeSolver(returned="infeasible"))
    assert m._solver_budget.used_s == 2.
    assert len(m._solver_budget.calls) == 1


@pytest.mark.parametrize("phase", ["scuc", "formal_sced"])
def test_real_highs_infeasible_model_is_typed(phase):
    m = pyo.ConcreteModel()
    m.x = pyo.Var(bounds=(0., 1.))
    m.c = pyo.Constraint(expr=m.x >= 2.)
    m.obj = pyo.Objective(expr=m.x)
    m._case = SimpleNamespace(cfg=SimpleNamespace(base={"solver": {"threads": 4, "time_limit_s": 10.}}))
    with pytest.raises(scuc.InfeasibleModelError) as error:
        (scuc.solve_scuc if phase == "scuc" else scuc.solve_sced)(m)
    assert error.value.phase == phase
    assert error.value.primal_solution_available is False
    assert "primal_solution_available=False" in str(error.value)


def test_stale_native_infeasibility_does_not_relabel_a_new_failure():
    original = RuntimeError("invalid solver option before a new solve")
    opt = FakeSolver(native="HighsModelStatus.kInfeasible", error=original)
    opt._solver_model = SimpleNamespace(getModelStatus=lambda: "HighsModelStatus.kInfeasible")
    with pytest.raises(RuntimeError) as error:
        scuc._solve_with_budget(SimpleNamespace(), opt)
    assert error.value is original and not isinstance(error.value, scuc.InfeasibleModelError)
