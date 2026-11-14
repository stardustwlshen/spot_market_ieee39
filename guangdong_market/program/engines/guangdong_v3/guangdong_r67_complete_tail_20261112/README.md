# R67: cached complete physical extension controller

## Implemented scope

A directly constructed continuous Pyomo model covers309 chronological5-minute cells: D's288 cells plus21 next-day cells required by the last rolling window. It uses the same actual accepted R50v2 SCUC / R53 SCEDv3 physical schedule and independent DA PRICE source as R62. All original network, thermal/AGC headroom, qualified positive/accident400 MW requirements, battery mode chain, efficiency, SOC limits, ramps, dated throughput budgets and original D96 energy equalities remain. There is no arbitrary non-D962-hour SOC equality, no new capacity/qualification assumption and no sequence of special-case necessary cuts.

The full template is built/serialized once, with27816 columns and48528 rows. Every run loads this pinned cache and creates one fresh source contract/permit. Every subsequent window rechecks source bytes and mutable signatures but does not reconstruct/serialize Pyomo or regenerate the day's source.

At window i, global cells0..3i-1 are fixed to the genuine previously issued solution, including the three already-issued cells between cutoff and the new window start. The objective contains only cells3i..3i+23; all other coefficients are zero. Quotes retain the existing thermal/storage energy curves, VRE bid-50 and research curtailment M2=1000. Fixed event/minimum fees are not silently re-auctioned or multiplied into the future tail. The full309 objective retained in the base cache is only the coefficient source; it is never the actual rolling request objective.

## Conditional recursive feasibility

The previous complete physical primal is a feasible extension of the newly fixed first-three-cell prefix. Changing the current24-cell objective cannot change feasibility. This statement depends on frozen D-1 prior forecasts, fixed UC/modes/qualifications/network inputs, zero tracking error, and consistent prefix commitment. Updates/outages/measurement deviations invalidate the premise and require a new check; no unconditional real-world guarantee is made.

The source prior covers the entire309-cell horizon and was already available before the first cutoff. Future RT dispatch, measurements or future information are not used. Each local24 input is separately assembled from the actual causal receipt plus issued prefix and checked against the cached future arrays/modes/network.

Every prepare re-evaluates the previous primal against all48528 rows, all current column domains and the original column domains. `FEASIBLE_EXTENSION.json` records this evidence. It is a primal-feasibility statement, not the next objective's optimality certificate. Each actual native solve must still return Optimal and pass unchanged strict KKT and full309-cell physical replay before issuing only three cells.

## Numerical policy

The verified original R53 worker (SHA5517dc6d922428d3a2160c9fa07a355c71b7cbb7b91bfe998814019b01e3d2e8) applies and reads back dual_feasibility_tolerance1e-9 and simplex_scale_strategy0. Threads1, MIP gap.001/absolute0,120-second limit remain. These transport options do not alter the cache-v1 variables, rows or objective; no KKT threshold is relaxed.

Raw native issued values are not silently normalized. A raw value outside an original column bound by no more than1e-7 may be fixed unchanged under the explicit numerical-boundary policy; every such occurrence records original bounds, raw value, deviation and tolerance. Larger deviations are rejected. Updated-domain and original-domain checks accompany the complete-row feasibility check. The original cache remains immutable and no physical capacity is silently changed.

## Verification artifacts

- `prepared_v1/LOCAL_MAPPING.json`: every original W0 local physical row except its window-only dated-budget row matches an identical full-model row; named column domains and current24 energy cost match exactly. The local budget is implied by the complete same-date budget with nonnegative throughput and the fixed actual prefix; the original D96 target is hard at global energy288.
- Independent `guangdong_r67_independent_source_witness_20261112/witness_v1/PRIMAL_REVIEW.json`: all27816 bounds and48528 rows checked. Maximum row residual1.55e-11, bound violation1.31e-12. Source P/E/VRE are unchanged actual R53v3 values; reserve/segment auxiliary mapping is explicit. This is not a new optimized result and no old optimality certificate is reused.
- `PURE_RUNTIME_RESULT_v3.json`: new runtime loads/cache, full309 and local24 physical replay, shifted objective, committed prefix, and three lifecycle negative controls passed. `NUMERIC_NEGATIVE_CONTROLS.json`: tiny explicit raw-bound deviation recorded, larger deviation rejected.
- Earlier pure development fixtures/logs remain. Only the latest source identity and a genuinely fresh runtime create current in-memory tickets; stored fixtures cannot restore a permit.

## Actual run and PRICE interface

Main's external entry is `guangdong_r67_actual_complete_tail_20261112/run_chain.py --output NEW_OUTPUT --end-index95 --execute-native` (space-separated `--end-index 95` in the actual CLI). Preparation made0native calls. The driver records exact launch attempts, stops on any failed/uncertain result or audit, and never retries automatically.

Each actual window exports:
- `prepared/input.json` and `local_RT_input.json`: original validated24-cell shape with actual projected state, not DA state substituted for operation.
- `TICKET.json`: global_offset=3i,309/24 shape, current objective support, source bindings and numerical policy.
- `accepted/ACCEPTANCE.json`: full-matrix strict KKT aggregates/worst items and native request-response hashes. Full KKT is performed; repetitive per-row/per-column detail is not duplicated on disk. Raw complete request/response remain available for independent recomputation.
- `issued_commands.json`: only three commands with global indices.
- `GLOBAL_EXTENSION_FACTS.json`: acceptance hash, local-input hash and non-executable global-lookahead facts.

The complete-tail LP duals include future feasibility effects and are **not independent market PRICE**. R69/R64 C pricing must use the bound actual local24 input in a separate8-quarter pricing model. PRICE, financial eligibility, metering, contract settlement and official publication remain separate.

W95 acceptance is not completion by itself: the explicit final drain executes its last three already-issued cells, checks original D96 targets and dated budgets, and closes288 issued=288 executed, with preparation3 separate. No full-day result is claimed before that actual process completes.

This is a research feasibility-extension strategy, not a claim that Guangdong's formal rules specify this controller. Synthetic qualifications/AGC,98 representative weights, connected minimum-fee mapping and uniform rather than cold/warm/hot event fees remain documented source limitations. Keep runtime source frozen after launch; external launchers stay outside this directory.
