# Fresh-input DA chain, limited research case

No old native solution or serialized model supplies current mathematics. Inputs start with portable `examples/ieee39_research/bundle.json`; additionally explicit retained research PRICE geometry/representatives and `explicit_rt_assets/ASSETS.json` are copied into the new run and hashed. Static input provenance is distinct from actual accepted output. Only the stated −50 noquote/M2=1000 policy and current uniform-event case are supported; one peak + one valley and finite penalties are checked before construction. No silent new assumptions guarantee feasibility.

`da_pipeline.init(bundle,run,pricing_spec,rt_assets)` creates a fresh run. `prepare_scuc(run)` constructs R56 from decoded data, serializes, derives a new compression plan and DC elimination map. `da_native.run_da(run,execute_native=True)` is the sole explicit three-native action. It calls fresh SCUC, fixed-new-integer SCED, independent DA PRICE with acceptance between stages. No solver call during import/prepare. `da_cli.py prepare/resume` supplies the small command interface.

DA worker settings: HiGHS1.15.1,threads1,gap0.001/abs0,SCUC600s,SCED/PRICE120s. LP uses already verified unscaled dual1e−9 worker. Backend/version/options/readback, original full-row feasibility, new integer agreement, and LP KKT remain gates. Reserved AGC is the explicit synthetic declaration, not a claim of formal market auction or physical telemetry. Three-state startup/formal AGC/AC are not silently enabled. R80 is not inserted into the fixed-DA RT controller.

## Accepted output contract

`ARTIFACTS.json` exists only after all three current-run stages are actually accepted:

- schema `gd.pipeline.da_artifacts.v1`; status `accepted`; run_id,input_identity,source_identity.
- inputs: case,grid,policy,agc,pricing_spec,rt_assets.
- stages.da_scuc: full_template,full_names,full_primal,template,names,compression_plan,network_plan,request,response,acceptance.
- stages.da_sced: physical_solution,expanded_response,full_names,full_template,template,names,compression_plan,COST_SCOPE,AGC_DECLARATION,policy,request,response,acceptance.
- stages.da_price: input,base_bridge,raw_prices,template,names,request,response,acceptance.

Every file ref is `{path: run-relative, sha256: byte hash}`. `resolve(run,ref)` rejects escape paths/hash changes. `load_case(run)` returns codec-decoded case with dictionary horizon; only the constructor converts a temporary copy to EnergyHorizon. The PRICE input retains the tested R53 wrapper shape; source_receipt physical_report_sha256 is **canonical content digest**, separate from byte-ref hashes. raw_prices uses `raw_unclipped_LMP[node][0..97]`. Financial publication/entitlement remains unknown.

## Recovery without recomputation

A prepared stage is reused only after all its input/source/artifact hashes match. An accepted stage is reused only after current run/input/source/stage and all artifact refs validate. Existing native directory without known completed response is pending, never automatically re-launched. Completed response without acceptance can be reviewed again; pure derived files are reused if content-identical rather than failing merely because they exist. Differing derived output is retained and requires an explicit revised audit output.

If an audit-code defect is repaired after a real native solve, do **not** rerun the optimizer. Preserve its prepared payload, worker, request, response and old audit. The recovery route is a separate revision receipt identifying new audit-code hash and proving unchanged mathematical/worker/native identities. Automatic adoption of changed reviewer code into an old source seal is deliberately not implemented. Stage-specific DA dependency identity means RT/PRICE/settlement-wrapper changes do not invalidate an unchanged DA solve; downstream stages must carry their own source manifests.

Pure status at handoff: fresh `pure_fresh_run_v1` constructed reduced SCUC71280columns/146966rows; seven narrow tests and standard worker protocol validation passed. Three-stage actual and one-key end-to-end validation are not claimed by this pure-preparation document.
