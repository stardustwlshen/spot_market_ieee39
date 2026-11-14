# Independent real-time PRICE slack mechanisms

This phase adds mechanisms, not an official parameter set and not a new accepted full-day market. It does not modify or reinterpret the preceding four independently solved prices. Those86 archived files were hash-checked unchanged; no corresponding LP was rerun.

## Rule/source scope

Retained official `region202610_official.pdf`, SHA978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf:

- §3.4.4, printed pages123–124: objective names ML′ for system load± slack, MR′ for accident-reserve shortfall, and M1′ for line/section± slack. These penalties are separate from renewable M2′/water M3′/tie-plan M4′, which this change does not invent.
- §3.4.4.1: generation equals demand + SLload+ − SLload−. Thus positive-load slack is surplus; negative-load slack is shortage. The implementation follows these signs, not ambiguous colloquial labels.
- §3.4.4.9–10, pages128–129: lower≤flow−SL+ +SL−≤upper for both line and section. The serialized transform uses the same ranged expression. It does not soften angle/flow equations or resource capability.
- The objective defines accident slack. Its implementation here is the conventional shortfall form Σeligible reserve+SLpr≥400, retaining the exact original demand and eligible-column inventory. This explicit adapter mapping is not evidence that all missing regional parameter/constraint detail has been resolved.
- §3.4.4.11 still explicitly refers to DA-SCED battery powers and bounds. Prior ambiguity around mode/SOC mapping is unchanged. Neither the current-SCED research domain nor the DA/R61 research domain becomes official by adding penalties.

The older `gd_r14_da_lmp.py` already had source-required line penalties and line slacks but retained hard balance/reserve. The four preceding new RT PRICE LPs were explicitly hard-network research matrices. This phase's new PRICE-only candidates are separate.

## Parameter admission

`validate_parameters(parameters, as_of)` requires all three ML′/MR′/M1′ values, positive finite numeric values, yuan/MWh units, exact factor meanings, effective dates, availability cutoff, an actual source file/hash and locator. No default numeric penalty exists. `formal` parameter mode rejects research sources and requires official-document provenance, official URL and an independent verification-receipt reference; missing factors, future sources, wrong units, nonfinite/zero/negative values fail. This is a supplied-parameter contract, **not** a cryptographic attestation of an authority or a complete formal-market certification. Downstream formal publication remains disabled.

The reviewed rule section/old module did not establish independently verified currently effective official numbers for these three factors. The sole test declaration is explicitly synthetic: ML′1000, MR′200, M1′100yuan/MWh. They are not presented as Guangdong values. Before an operational/formal configuration is admitted, its actual parameter documents and numeric provenance must be checked independently.

The node-angle representation also needs an explicit allocation of the single system load-slack pair across nodal balances. A reference-node39 allocation is used only in the pure IEEE39 example and is marked uncertified research. The transformer requires all nodal rows be inventoried and nonnegative weights sum to1; it does not claim the official rules prescribe this allocation.

## Interfaces

`soften(base_payload, names, mapping, parameters, as_of=...) -> payload, names, trace`

- Exact base hashes and complete named balance/accident/line inventories prevent silently operating on a different model.
- One load surplus/shortage pair per15min; one accident-shortfall variable per15min; two per line/section.
- Original thermal/storage/VRE columns and bounds remain byte-identical. Only explicitly inventoried line-column bounds become equivalent soft ranged rows. Existing section rows can be softened in place; unknown/omitted inventory is rejected.
- Original positive-reserve rows stay hard and unchanged. Accident eligibility/capability constraints stay unchanged. A400MW demand served by390MW eligible reserve plus10MW mathematical shortfall is reported as a10MW deficit, not400MW supplied reserve and not extra qualification.
- Penalty objective coefficient is factor×duration_h. For15min, row dual/0.25 yields yuan/MWh. The selected storage PRICE boundary remains inherited unchanged.
- PRICE slack permits a priced infeasibility/constraint violation. Its result is **not executable dispatch**, not an AC-safe schedule, and not a substitute for rerunning physical SCUC/SCED.

`adapt_existing_pure.py` demonstrates the interface against the frozen W0 DA/R61 price domain without a solve:1830→2590columns,1333→1701rows,760slacks. It keeps source context, parameters, mapping and trace in `W0_DA_domain_mechanisms_pure`. This was initially a pure candidate. After separate authorization, its NEW mechanism-enhanced occurrence was solved as described below; it is still not part of the prior four-LP conclusions.

## Actual verification

Exactly one small independent LP combines five disconnected mechanism checks. All penalties are the explicit test assumptions above. HiGHS1.15.1, threads1, gap0.001/abs0,60second upper budget, one native call, Optimal and strict full KKT passed:

| Check | Actual slack | Expected/observed marginal value |
|---|---:|---:|
| shortage |10MW|+1000yuan/MWh|
| surplus |10MW|−1000yuan/MWh|
| accident shortfall |10MW|200yuan/MWh of requirement|
| positive line overrun |10MW|105yuan/MWh (5energy+100network)|
| negative section overrun |10MW|105yuan/MWh (5energy+100network)|

This verifies sign/unit conventions, not an unconditional cap on all nodal prices in a congested multi-node network. Source allocation and network shadow prices matter. Raw request/response, worker/launch/completion receipts and full KKT are retained. Eight pure/read-only tests pass, including formal/research parameter separation, missing/future/nonfinite rejection, inventory protection and exact preservation of positive reserve/resource bounds. Tests do not repeat the optimizer.

## Authorized real IEEE39 W0 mechanism occurrence

After the tiny test, one separately authorized60s/threads1 HiGHS solve of the2590-column/1701-row W0 DA-reference/R61 price domain plus sourced mechanisms completed Optimal. Full KKT: primal5.1728e-12, dual sign1.5626e-12, absolute duality gap9.0804e-8. All760 slack values were actually returned and are0; not missing values filled with0. The39 first-quarter prices remain330.75yuan/MWh, exactly matching the earlier W0 hard-domain node prices in this particular solution. First-quarter positive headroom831.554462468MW; accident physical supply405MW, separate price shortfall0MW, demand400MW. Complete eight-quarter supply/shortfall columns and all slack values are retained. Zero use here does not make the mechanism unnecessary; the single tiny LP independently activated each mechanism and checked signs/prices.

Total new optimizations in this phase:2 (one tiny mechanism LP + one NEW full W0 penalty-domain LP). No old LP was repeated. Nine final pure/read-only tests pass. Both use explicit research factors only; no current official numeric parameter set, complete formal price, payment authority or executable physical dispatch is claimed.
