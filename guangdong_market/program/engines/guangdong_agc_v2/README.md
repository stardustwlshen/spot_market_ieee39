# Parameterized AGC user workflow v2

Supported installation: `cli.py` plus `dependencies_v3/` (50 hash-pinned files). Run each stage in a separate process using the existing project Python environment. `--engine-root` aliases `--dependency-root`; it denotes the compatible packaged tree, not an arbitrary repo. No COPT initialized. Original v1, original frozen joint loop/proof and historical evidence were not changed.

```
python -B "$CLI" --dependency-root "$DEPS" context --parent-run "$RUN" --hour "$HOUR" --zone "$ZONE" --as-of "$ASOF" --output context.json
python -B "$CLI" --dependency-root "$DEPS" example --context context.json --output synthetic-offers.json
python -B "$CLI" --dependency-root "$DEPS" prepare --context context.json --thermal-input synthetic-offers.json --output candidate
python -B "$CLI" --dependency-root "$DEPS" native --candidate candidate --output native-attempt --execute-native
python -B "$CLI" --dependency-root "$DEPS" ac-check --candidate candidate --native native-attempt --output ac-check --execute-ac
python -B "$CLI" --dependency-root "$DEPS" review --candidate candidate --native native-attempt --ac-proof ac-check/NUMERIC_EVIDENCE.json --output review.json
```

`native` without its execute flag is dry. `ac-check` without its execute flag verifies SCED and writes only AC input/mapping, not an AC result. Every output directory is fresh-only; uncertain or existing attempts cannot auto-retry. AC check selects the maximum frozen candidate-hour forecast load, earliest tie, recorded at prepare. It uses candidate CONTEXT, not a hardcoded experiment context. Actual proof pins the new AC producer SHA, original worker, exact input/result and completed native occurrence. Review cannot promote old numerical results to this new candidate.

`example` explicitly creates synthetic declarations, not submitted bids or verified qualification. Supported scope remains the five original research thermals, AGC50, positive400 and accident400 separately; no new BESS/VRE eligibility. Thermal awards must remain compatible with this original 10MW-margin AC bridge. Full-tail physics, original voltage/Q/P/branch limits, modes and old results are not modified. A converged AC is not necessarily safe. Single snapshot cannot certify a whole hour or payment.

## Actual installation workflow test

Only `actual_user_flow_v1/` is this version's new experiment. Exactly one new HiGHS LP and one pandapower AC were executed. Context W67 is available at simulated16:30 for17:00–18:00, issued frontier201; all input times were supplied on the CLI. Selected cell212. New LP Optimal, full KKT accepted. New AC converged but returned `loss_allocation_pending` with voltage/P/AGC issues, no C1 exclusion and no payment. `FINAL_REVIEW.json` is the separate read-only CLI verification. No old SCUC or full-day run repeated.

Other top-level test/context/evidence files inherited from v1 during this new copy are historical diagnostics, not this actual run. Original incomplete `dependencies` is retained but unsupported. Only dependencies_v3 is the current installation. Import closure is static and byte-pinned; only its review_and_ac producer was revised to remove fixed context. For relocation, move installation and inputs before creating new contexts. Existing signed receipts retain original path identities and are never silently re-signed.
