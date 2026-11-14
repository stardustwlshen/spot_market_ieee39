# Thermal dispatch-boundary contract v1 (independent research candidate)

## Delivered and tested

`contract.py` compiles explicit, time/version/source-labelled records into per-unit/per-5-minute fixes and a separate publication-protection gate. Kinds: commercial DA award, reliability reference, must-on/off, issued instruction, actual curve, forecast curve, protection, permission. Actual curves cannot extend into the future. New versions unavailable at cutoff do not affect selection. Issued/actual records cannot silently be superseded; conflicting hard requirements reject. No ordinary DA-online cell expands into a whole-day must-run rule.

Commercial DA award is a reference plus a bounded stop/off publication guard. It is not automatically a whole-day hard online constraint. Reliability/forecast references do not confer immutable status. Must-on/off records explicitly bind their valid interval. Only explicit research permissions targeting the affected record, field and interval with a declared safety exception permit a changed protected proposal. Permission does not remove must-on/off or issued/actual hard constraints. This gate is conservative research treatment of the protection principle, NOT a legal interpretation establishing all permitted operational changes. Formal mode always returns FORMAL_BLOCKED: an authenticated operator-input/authority adapter is not implemented.

`issued_records_from_ledger` maps only issued, checksum-consistent research notices, not pending forecasts, to per-cell connection/start/stop/mean power locks. `bind_notice_carry` binds ledger/run/clock hashes to the existing exact carry and exports its remaining begin/mean/end curve and age fields. The existing free-SCUC boundary adapter then enforces active curve geometry. Neither function creates publication or safety authorization. Source IDs/checksums are provenance declarations, not proof of real operator authority; caller must use verified notice_bridge.load_plan/publish_due/connect_boundary, not fabricate their outputs.

`apply_to_model` adds concrete Pyomo equalities to the existing free-SCUC model; it does not remove prior conservative restrictions. Apply before serialization and require `check_publication` before simulated publication. Do not automatically install it over the frozen run.

## Evidence

- 14 pure tests passed (`TEST.log`), including missing formal authority, future versions, immutability, conflicts, time/source validity, bounded permissions, and preserved hard constraints.
- Existing R68 accepted SCUC/SCED and next-window SCUC reused without new solves; emitted `COMPILED_R68.json` and `BUILDER_BOUNDARY_R68.json`.
- `test_real_builder.py` executes only the reviewed construction prefix of the existing connector, before native serialization/run statements; real free-SCUC constructed and serialized with 12 added contract rows. Existing accepted native primal satisfies all 12 with residual0. Existing conservative protections retained (`REAL_BUILDER_RESULT.json`). This is not a newly optimized model and not a second-window SCED.
- First test failure was only the existing compressed response filename; original failure log preserved.
- Zero new native calls. No original400MW inputs, frozen v6/source/day results or prior evidence changed. No installation/push.

## Explicit remaining work

1. Independent review and production-grade schema evolution; define approved replacement/cancellation workflows rather than relaxing immutable records.
2. Bind full IEEE39 unit identities, source bytes and versions, actual DA/reliability/protection inputs to the user-facing scenario loader. At present input source digests are declared research metadata, not externally authenticated dispatch authority.
3. Invoke compiler/apply/publication gate from the large-model driver and supervisor; enforce transactionally at every rolling issue. Current adapter is executable and tested on the real builder, but NOT installed or automatically called by the large-model/UI path.
4. Replace the conservative all-day research protection only in a new explicitly selected research branch with complete boundary records and carry coverage for every unit. This candidate intentionally does not remove it.
5. Multi-unit typed hot/warm/cold model parameter binding, nonzero minimum-times, network/reserve/AGC safety and actual shutdown permissions still need full-model integration and limited live tests. Existing R68 source is single-unit, zero-minimum-time, synthetic research.
6. Formal data authentication and operator authority verification remain blocked; research declarations must never unlock formal mode.

Rule basis is the bounded review `guangdong_rt_da_protection_scope_review_v1_20261112/REVIEW.md`, notably §3.4.1, §3.4.2.5/.8, §3.5.1.1/.8, not an invented all-day DA rule.
