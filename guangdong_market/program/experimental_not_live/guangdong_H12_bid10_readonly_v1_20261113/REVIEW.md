# H12 C1 bid6→10 sensitivity: no thermal freedom released

Pure arithmetic executed through existing general_hourly_agc_v2.hourly.clear / vendored R73 adapter. No solver, source modification, publication, receipt production or original award change.

Inputs copied from short_hour_agc_receipt_v1/actual_hour01_v1/INPUT.json; target hour changed to2026-11-11 12:00–13:00 and hypothetical research as_of11:15. Only sensitivity is C1 bid6→10 yuan/MW-mileage. This new quote and its availability are explicit counterfactual research assumptions, not received actual declarations. Original50MW demand, local floor0, full-area kmax2, each ratio0.05, capacities, synthetic qualifications and separate-plant identities are unchanged. safety_excluded remains empty.

## Actual pure results

Both baseline and sensitivity award C1/C2/C3/CCGT/GT10MW each, unmet0. Baseline marginal price6; candidate10. C1 moves from first to last but is still selected.

Capacity ×0.05 is32.3,36.25,43.25,34.35,15MW respectively. Existing projection.py:51–53 separately applies per-plant cap0.2 × zone-demand / selected units in plant. With zone-demand50 and each unit declared a distinct plant, that cap is10MW. Thus the other four can supply only40MW; a higher C1 bid cannot make C1=0 while meeting50. Excluding C1 would leave10MW short and is not a permitted substitute for a successful auction. We did not do that exclusion. Raising only C1's bid anywhere in the allowed range cannot overcome this capacity arithmetic.

## Causality and binding times

- This output is an unbound pure auction calculation, not an accepted hourly receipt. A live research receipt requires a pinned accepted prior SCUC/SCED plan covering all H12, cutoff strictly earlier than auction as_of, available_at<=as_of, and full-hour stable/capacity checks via produce(). The local exporter currently enumerates legacy_short/cycle9; CLI10 identity needs an explicit registered adapter, not relabelling CLI10 as cycle9.
- Earliest proposed research calculation here is11:15, before H12 T−30 deadline11:30. CLI10 W44 has cutoff11:00 and declared simulated availability/delivery11:15, potentially usable after registering and checking that pair. W45 cutoff11:15 cannot be its own earlier reference at11:15. If auction instead occurs11:30, it cannot retroactively enter W45's11:15 cutoff; use a later consumer.
- Preserve original H11 awards up to12:00. H12 candidate would affect only12:00–13:00, but is numerically unchanged. No earned release interval exists and therefore no earliest permitted C1 shutdown can be inferred or notice issued from this experiment.
- Future H13+ original positive awards remain unchanged, also requiring stable online. Even a hypothetical zero H12 award alone would not permit coal shutdown and return by13:00: C1 shutdown curve1h, minimum down8h, startup curve3h. Those physical limits cannot be shortened. This is a further temporal restriction, not authorization to modify later awards.
- Existing fixed startup notice0.5h is NOT evidence of an authorized shutdown instruction or its operational lead time. Contract publication guard and safety review remain in force.

This is a useful negative result: there is no mathematical opportunity for C1 release under the exact allowed bid-only sensitivity. It does not justify changing50MW requirement, plant identities, capacity/ratio, eligibility,400MW reserve or physical data to force one. Continue full-model interface integration separately, without promising shutdown benefits from this auction.
