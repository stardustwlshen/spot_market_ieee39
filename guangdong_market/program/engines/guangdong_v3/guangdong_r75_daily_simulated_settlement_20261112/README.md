# R75 simulated daily energy accounting, R74 source adapter

Thin independent copy of frozen R71. `arithmetic.py` is byte-identical; `compute`, `assumptions`, `Sources`, and `load_execution` are AST-identical. Thermal/storage use retained R36, VRE uses R47, and all old restrictions remain: simulation-only, known-empty research contracts, no actual meter or bill, charge/discharge separate, quarter baseline clipping before four-point arithmetic mean. Fixed-injection/load financial accounts remain unknown, but enter physical energy conservation.

Only R74 full96 summaries, R74 PRICE-only plans and R74 quarter-domain preparations are accepted. Every input must still equal the associated R67 actual local24 input; metadata must pin its actual RT acceptance. This is the independently solved pure eight-quarter PRICE research model, including explicitly researched 15-minute ramp mapping. R69's original four infeasible results remain untouched and cannot be substituted into this source contract.

Wait for all96 real R74 results and summary; no partial or fictitious full-day amounts:
```
ieee39-cloud/repo/.venv/bin/python -B guangdong_r75_daily_simulated_settlement_20261112/assemble.py --price-summary guangdong_r74_price_summary_20261112/actual_day_v1/PRICE_SUMMARY.json --output guangdong_r75_daily_simulated_settlement_20261112/actual_day_v1
```
0 native / no repeated KKT. Three narrow source-delta tests verify unchanged arithmetic/execution functions, old-summary rejection, missing-price rejection. R71 remains frozen.

## Actual completion and numeric accounting boundary

Original `assemble.py` and `ACTUAL_CALCULATION.log` preserve the failed first arithmetic attempt: retained R36 correctly rejected ES3 23:00 charge quantity with the wrong sign, only +8.376924884750655e-13 MWh in its signed charge ledger. The actual raw charge was the negative of this. This was native zero-bound roundoff, not additional physical charging.

Successful entry is now **`assemble_v2.py`**. It normalizes only wrong-direction accounting quantities with absolute magnitude <=1e-9 MWh to zero, logs each raw/accounting/delta, and rejects larger violations. Correct-sign small values are never rounded. It leaves all actual physical commands/SOC untouched and computes physical balance with original raw quantities. Arithmetic formulas remain byte-identical in `arithmetic.py`. Six tests include the exact residual, over-threshold rejection and preserving a valid small quantity.

Successful results reside at `actual_day_v1/` (generated once by v2, directory renamed from its temporary `actual_day_v2` label without recalculation). There were two arithmetic invocations: one preserved rejection, one successful calculation, zero native calls or repeated KKT. Use `assemble_v2.py` for future explicit assembly, with a fresh output directory.

Actual result:480 rows, simulated energy subtotal17,860,524.976622447 CNY; LT0, DA17,860,526.404524691, RT deviation−1.427902244. This is research energy arithmetic only, not a payable bill. Original physical day balance residual−5.7578383643675e-13 MWh. R74's ERRATUM explicitly records a changed eight-quarter mathematical time domain, not a transport-only change; original R69 failures are preserved.
