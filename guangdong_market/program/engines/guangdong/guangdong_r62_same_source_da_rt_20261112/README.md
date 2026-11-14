# R62: one new DA98 physical source feeding causal RT

Source is the **actual accepted R50v2 free 98-objective SCUC → R53 SCEDv3 → independent R53 DA PRICE** chain. `r62_entry.py` validates the accepted review hashes, actual PRICE event pins, SCED physical binding, new policy and raw prices, then assembles a 96-quarter physical schedule and 309 consecutive physical cells / 103 references. No old R42/R44 solved dispatch or old R60 RT result is substituted for the new chain. Original preparation facts, static bids, network/resource data and causal forecast priors remain reused as explicit inputs.

Physical publication availability is the declared **simulated** DA publication clock, not evidence that the historical operator issued the newly calculated solution at that time. All original startup notice checks and forecast information cutoffs remain enforced. Raw DA prices are neither clipped nor financial settlement prices. Source pricing/physical assumptions retain R50's 98 representative weighting, hidden physical nodes, synthetic minimum fee80 and connected-phase mapping; uniform event fees are not cold/warm/hot classification.

New noquote bid is -50 yuan/MWh and dispatch curtailment M2 is the explicit 1000 research parameter in both RT and PRICE input math. The source synthetic D-day AGC50 and positive/accident reserve400/400 remain unchanged. RT fixed-state energy objective does not represent a new free SCUC event/minimum-fee auction.

R60's necessary D96 reachability guard and removal **only of non-D96 rolling 2-hour endpoint equalities** are retained as an explicitly research mapping, not asserted Guangdong official free endpoints. Original D96 targets, budgets and physical modes remain unchanged.

## Pure verification

`prepared_v1` contains W0, W88 and W95 mathematical fixtures. W0 uses the verified original preparation prefix; W88/W95 use the new DA physical state only to test boundary shapes. They are NOT accepted rolling states or executable future tickets. Their matrices are 2166 columns and respectively 4013/4018/3813 rows; W88 retains D96 terminal equalities, W95 adds only 30 before-midnight reachability rows.

`pure_runtime_check.py` initializes a genuinely fresh in-memory source once and prepares live-provenance W0 with no solver. `fresh_runtime_v2` / `PURE_RUNTIME_RESULT.json` record 8.40s initialization and 1.99s W0 preparation in this measurement. Complete variable, row, objective and options match the W0 mathematical fixture. Double preparation, skipped window and foreign ticket were rejected. The first development attempt's missing provenance rejection is preserved in its log.

## FreshRun API and efficiency

`runtime.FreshRun(new_output)` loads and verifies the source once per new process. It retains its fresh contract/permit; each stage continues file bytes/hash checks and full mutable contract/data signatures. It does not regenerate the entire 309-cell source each window. No persisted permit is restored.

```
run = FreshRun(new_output)
ticket, prepared_path, metadata = run.prepare(0)
# Main launches exactly this standard HiGHS request and records five native files.
report = run.accept(ticket, event_paths)
# Repeat run.prepare(1) ... run.accept(...) in the SAME process.
# After all 96 accepted windows:
report = run.drain()
```

Preparation records new source/run/ticket identity. Actual acceptance requires the exact new request (only fresh deadline may differ), same in-memory unconsumed ticket, unmodified disk artifacts AND canonical in-memory input/request/names/record signatures, strict KKT and physical replay. Only the first three cells are issued; receipts advance only the prior issued prefix. Final drain explicitly executes the last three cells to D96 and checks targets/budgets/288 issued=288 executed, with preparation3 separate.

The external main-controlled driver is `guangdong_r62_actual_same_source_20261112/run_chain.py`. It has not been executed during this preparation. `--windows 1` is a standalone W0 diagnostic, not a resumable live token. `--windows 96` keeps the single process/permit and accepts each window before proceeding. Non-Optimal or failed audit stops immediately without retry. Every native attempt, including a failed one, is separately recorded by the driver; accepted-call counts are not total attempt counts.

R61 C pricing can consume this new branch's accepted input/ticket through a **new R62 adapter** using the generic provider/transform. Its current R60 adapter must not be reused. DA-prefix pricing state must never overwrite the running RT state. No R62 RT native result, independent RT PRICE, settlement or full-day feasibility is claimed by this pure package.
