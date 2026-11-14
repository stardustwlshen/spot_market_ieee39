# SettlementSession v1: component arithmetic, not a bill

Copy `settlement_session.py` plus `vendor/` as `app/gd_settlement_runtime/`. Load using a unique importlib module name. Standard-library only, zero optimization. Retained vendor files unchanged. R36 and R27 function bodies loaded by AST to avoid workspace-only import/bootstrap dependencies; no formula rewritten.

## Trusted integration boundary
Call only after the installed UI's existing accepted-hour/export source verification. Construct `SettlementSession(hour_path, expected_run_id=selected_pipeline_run_id, expected_hour_sha256=verified_export_digest)`. This module checks digest, source pins, run and period, **does not redo full model mathematics or authenticate a live host**. A self-consistent uploaded hour is not a trusted live result. Standalone imported exports are labelled research exports only. Never expose an upload switch claiming `formal_data`/official acceptance. Installed app must pin this module and vendor files in its normal source manifest.

All results carry run/hour binding, `formal_bill=None`, `payable=False`, `safety_release=False`, `native_calls=0`. User-provided measured/external-publication labels describe claims, not verified authenticity; outputs remain research component arithmetic. No JSON can enable payment or formal status.

## Tabs
1. Existing verified hour: `hour_summary()`. Label exactly **模型列示账户电能量分项净合计（研究）**. Not turnover/profit; missing demand counterparties etc. `storage[resource]` contains signed `charge`, `discharge`, `net` amounts. Negative discharge amount is possible and not forcibly relabelled an expense/error.
2. Energy components: `energy(fields)`. `typed_inputs.value` uses retained R36 keyword API; category plus explicit contracts/base/agency/DA/RT/meter/applicability required. Each retained R14 row reports independent missing reasons; unsupported total remains None. No empty contracts->zero. Explicit source-backed zero contract required. API intentionally does not fill imported meter/contract values from simulation. Research fixture may explicitly choose them. R14/R36 only supported classes; VRE original hour remains visible, expanded VRE input editor not yet implemented.
3. AGC: `agc(fields)` names `measured_mileage_mw`, `uniform_price_yuan_mw`, `settlement_performance`, `designated_test`, optional `adjustments` list of `{statement_id,signed_yuan}`. No capacity conversion to mileage. Missing adjustments leaves reconciled net None. Quality/exemption formulas not invented.
4. Reserve: `reserve(fields)` names `scope`, `units`. Scope fields follow vendor `Scope`; supply units follow `Unit`. Explicit R6 mandatory. Complete compensation pool, service market state/current rule and parameter assertions, actual compensation lower requirement, qualification flags required for fee. Partial pool still reports computable individual service quantities but total fee None. All supplied claims treated as research. A complete five-unit research pool is never a verified provincial pool. 400+400 never inferred as compensation requirement.

## Classified field envelope (every supplied field)
`{classification: measured|external_publication|research|missing, source: string, run_id: selected run, start: ISO hour start, end: ISO hour end, value: ...}`. Missing fields omit value and stay missing. Each nonmissing field requires exact run and interval. For tagged R36 JSON: `{$type: 'Key'|'Award'|'Value'|'Evidence'|'Attestation'|'Contract'|'SettlementBasis'|'StatementAmount', ...fields}`, decimals `{$decimal:'1.25'}`, datetime `{$datetime:'...+08:00'}`; arrays become tuples. Values/Evidence are the retained API, simulation channel only. Input classifications must be shown in UI and not collapsed into official badges.

Review input errors as component-only validation failures; don't crash other tabs or silently substitute zero. Review-generated sample is research, never prechecked formal. `test_session.py` demonstrates complete retained original fixture.

## Not implemented / never publish
Monthly compensation cost integration, deviation ambiguity/exclusion logic, AGC quality/exemption/fee sharing, reserve monthly payer allocation and heterogeneous pricing, monthly renewable mechanism/green certificates, taxes/invoice and final bill. No operational safety release. User-data audit is not performed here.

## Verification
`python test_session.py` → PASS 0native. Existing HOUR0 source pins checked; 54-row signed sum preserved. Original R36 fixture 47900; contract missing; cross-run refusal; formal JSON refusal; mileage gross300/netmissing; incomplete pool no revenue; explicit complete research pool fee3000. Existing native acceptance is not rerun or claimed new.

# v2 added APIs (same constructor/envelopes, no solver)

`session.non_da_renewable(fields)`:
- `account`: `{resource,node,settlement_unit,identity_kind:'model_aggregate'|'supplied_subject',registry_source?}`. Aggregates must match VRE account in selected hour. Supplied subjects require mapping source, are not authenticated as registered subjects.
- `participation`: explicit `does_not_participate_DA`; `node_mapping`: source declaration.
- `actual_export_mwh`, `rt_price_yuan_per_mwh`: explicit separate classified inputs. Does NOT silently reuse participating DA export quantities/prices or replace its original ledger.
- Reuses unchanged October §7.4 actual MWh×RT price function. Other contracts/base/mechanism/green-certificate/monthly accounts remain separately missing, not zero or rolled into this cash.

`session.operating_compensation(fields)`:
- `account`: resource and node in selected thermal hour.
- `offer_cells`: complete contiguous hour list. Each `{start,end,power_basis:'explicit_piecewise_constant_gross_MW',gross_power_mw,minimum_adjustable_mw,minimum_price_yuan_per_mwh,include_minimum_cost:boolean,minimum_cost_decision_source,curve_source,segments:[{width_mw,price_yuan_per_mwh}]}` in declared physical curve order. Positive segment widths, above-minimum supported only, no startup/offline/transition extrapolation. Explicit constant-within-cell representation is a research assumption when only schedule averages exist. Variable power within a cell requires finer data, not endpoint fitting.
- `auxiliary_offer_deduction_yuan`: signed currency deduction with mandatory envelope `calculation_basis`. No default plant-use fraction. Negative energy bids may imply signed deductions; user must supply correct accounting evidence. Net offer C报价=gross bid integral minus this amount.
- `net_export_mwh`, `approved_cost_yuan_per_mwh`, `minimum_cost_deduction_yuan`, `other_variable_compensation_yuan`: needed for C运行成本=net MWh×approved cost minus stated deductions. Zero deductions must be explicit.
- `spot_market_revenue_yuan`: that unit's eligible DA+RT energy revenue, not 54-account sum/LT-inclusive subtotal/profit.
- `coefficient`: explicit current provincial m, no default.
- `applicability`: `{included:true|false|null,provincial_rule_source:...}`. If included=true also require `hour_exclusion_review`: `{DA:[bool,bool,bool,bool],RT:[bool,bool,bool,bool]}`, each boolean indicates ANY §4.2.2.4/provincial exclusion at that quarter; all must false. This remains research input, not system-inferred eligibility.
- C报价 can be available independently; C核定/other costs/m/revenue/eligibility missing means compensation unknown. Unchanged original scalar compensation function reused. Explicit excluded returns original rule's zero with exclusion status, not qualification inferred from label.

`extensions.declared_curve_cells(thermal, ordered_segments=..., samples=..., minimum_price_yuan_per_mwh=..., include_minimum_cost=..., minimum_cost_decision_source=..., curve_source=...)` consumes existing `energy_inputs.thermal[g]` fields `bid_base_mw` and `segments[k].{width_mw,price_yuan_per_mwh[t]}`. Samples require tick,start,end,gross_power_mw,power_basis. No no-load/startup cost added. Return suitable `offer_cells` value. Bid data alone never supplies auxiliary deduction, meter net energy, qualification or approved cost.

Units per cell: MW×CNY/MWh×h=CNY; integrate curve at EACH actual constant cell before adding time, not integral at hourly mean. Example 100/200 MW halfhours yields 38500 CNY versus 150MW constant37000 for same piecewise curve. No claim hourly mean is sufficient.

Local retained source: regional2026V1 §4.2.2.3–.4 pp149–150; minimum-cost treatment and applicability deferred to provinces. No new official-document certification. §4.3.2.3 negative×negative prose/printed-expression conflict remains untouched/unavailable.

`python test_extensions.py` includes v1 regressions, §7.4 10MWh×300=3000 with contracts still missing; gross offer37000, net offer36000, approved operating cost36250 and explicit research m=.5/revenue30000→3000; missing m/cost/net blocks. Full hour segmentation check38500 versus mean37000; invalid mean-only basis and exclusion conflict rejected. No native, no fabricated acceptance.
