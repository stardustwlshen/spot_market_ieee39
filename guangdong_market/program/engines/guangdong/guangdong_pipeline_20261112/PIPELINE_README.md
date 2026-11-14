# Explicit-input, one-run research pipeline

This entry coordinates the existing verified mathematics. It does not claim the new end-to-end entry has been actually completed until its same-run receipts exist. Fixed-DA R67 controller only; R80 real-time commitment demo is not silently inserted. Formal AGC/AC and real contracts/meters remain outside the completed research scope.

## Continue the current actual DA run

After the main-owned DA process finishes, first run four RT windows and their independent PRICE calculations:
```
ieee39-cloud/repo/.venv/bin/python -B guangdong_pipeline_20261112/pipeline_cli.py resume --run guangdong_pipeline_20261112/pure_fresh_run_v1 --rt-windows 4 --execute-native
```
Then resume the same run to96 and explicitly opt into synthetic participation/known-empty-contract energy accounting:
```
ieee39-cloud/repo/.venv/bin/python -B guangdong_pipeline_20261112/pipeline_cli.py resume --run guangdong_pipeline_20261112/pure_fresh_run_v1 --rt-windows 96 --execute-native --simulation-settlement
```
The accepted DA stages and accepted RT/PRICE prefix are verified and reused, never automatically solved again. Four issued windows do not mean a fully executed hour; RT report retains the causal executed/issued counts. Only96 plus final D96 drain supports288 execution quantities, and only96 accepted independent PRICE windows supports a full-day amount.

Omitting `--execute-native` returns a no-action dryrun; it does not initialize a directory or import an optimizer. Existing run requires `resume`; fresh `run` requires all explicit inputs.

## Fresh repeat of the declared case
```
ieee39-cloud/repo/.venv/bin/python -B guangdong_pipeline_20261112/pipeline_cli.py run --run /path/to/fresh_run --bundle guangdong_market_delivery_20261112/examples/ieee39_research/bundle.json --pricing-spec guangdong_pipeline_20261112/examples/research_pricing_spec.json --rt-assets guangdong_pipeline_20261112/explicit_rt_assets/ASSETS.json --rt-windows 4 --execute-native
```
Fresh source matrices must exactly match the mathematics digest covered by the independently verified current small-coefficient storage profile before this checked worker can be used. Different supported inputs need a new exact import/profile verification; no permissive `kWarning` acceptance is introduced. The original generic attempt had0 optimizations and is retained. Checked transport keeps this run's default HiGHS heuristic settings; it does **not** claim the older R50 explicitly-disabled heuristic profile. HiGHS,gap0.001/abs0,threads1 remain explicit.

## Files and scope

DA `ARTIFACTS.json` → `rt_prepared/` → `rt/` → `rt_price/PRICE_MANIFEST.json` → `settlement/RESULT.json`.

`ORCHESTRATOR.json` binds this top-level entry independently of DA mathematical source identity. DA transport revision and each RT/PRICE stage have their own dependency manifests. Stage changes do not silently invalidate and rerun unchanged upstream native solutions. Exact Decimal amounts serialize as strings.

`PIPELINE_STATUS.json` reports the last known phase and pending/blocker without fabricating completed results. A native directory with uncertain completion, time limit, infeasible solve or failed acceptance is pending. There is no automatic retry or old-result substitution. Pure partial artifacts are not overwritten. Original requests/responses and failure records remain intact for explicit diagnosis/reconciliation.

Only the CLI's three small pure tests and no-action dryrun were performed here; no additional native call was made by the entry's author.

### Default numerical and dependency profile
New same-case runs use the exact-import-verified tiny-coefficient guard directly,
SCED dual feasibility tolerance `1e-10` with simplex scaling `0`, and the
complete PRICE dependency closure in `price_dependencies_v4`. They do not
intentionally reproduce the preserved import or `1e-9` KKT failures. The strict
KKT threshold remains `1e-8`. Different mathematics needs a new exact storage
profile, not broad warning acceptance. Current accepted SCUC/SCED receipts are
reused; the v4 recovery command executes only the still-unexecuted DA PRICE.
Every downstream consumer must resolve receipt references, not assume the
accepted response lives under a directory named `native`.
