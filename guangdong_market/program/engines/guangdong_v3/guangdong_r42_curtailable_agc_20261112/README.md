# R42: independent curtailable-renewable + synthetic D-day AGC scenario

Original case/availability/load/SOC/curves/network/positive400/accident400 are
unchanged. This is a NEW scenario, not a repair or relabelling of R35/R37/R39.
R39 remains valid for its original fixed-renewable assumptions.

## Rule basis and research boundaries
Original region202610_official.pdf SHA
978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf.
§1.4 printed2: noquote resources optimize at provincial declaration lower limit.
§2.2.9.6 printed12: forecast is upper bound.
§2.3.2.6 printed29–30: safety/balance permits optimized adjustment.
§2.5.3.20 printed51 and §2.5.4.17 printed65: noquote p+d=A;
quoted branch alpha*A−d<=p<=A is NOT implemented here.
SCUC/SCED objectives printed39–40/55–56 include M2*d.
Printed180's1000 is a price penalty M2-prime, NOT proof that scheduling M2=1000.

This new scenario uses explicitly research noquote bid proxy0 and research
curtailment penalty1000yuan/MWh; neither claims certified current official
operator settings. Pure coefficient sensitivity0/100/1000/10000 is included,
not four solved optima. Continuous48h cost is still NOT official DA98 costs.

Five original thermal units use independent SYNTHETIC plant/qualification/K/K1
and demand50 fixture from R38/R31, which forces all5stable and10MW each over
D-day cells0..287. These are not actual eligibility declarations. D+1 cells
288..575 retain the original research physical continuation with AGC0, not an
actual next-day service award. No wind/solar/BESS reserve or AGC credit added.
Symmetric +/-10 MW with residual400 reserve headroom is conservative research
coupling, not an assertion of a literal regulatory optimization formula.

## Exact implementation
The original serializer substituted fixed VRE. Merely unfixing p is wrong.
Five nonzero-source buses across all576 cells restore p,c in[0,A] and p+c=A,
including zero-availability cells. Nodal equations restore p and remove its
old RHS contribution; both aggregate reserve rows do the same. Thermal AGC
bounds/headroom include10*stable. All D-day stable1 conditions are explicit
new equalities, not old SCUC acceptance. All unrelated full rows/bounds/costs
are unchanged. New objective adds (1/12)*M2*c; original zero offer remains0.
The old R23 total-cost override omitted renewable_bid_cost, irrelevant while
oldbid0; this scoped proxy intentionally supports only zero bids and rejects
nonzero bids rather than silently omitting their cost.

Reuses original phase/BESS compression and proven thermal ramp cuts. Network
elimination is recomputed with20 injections/cell, including the5 VRE buses,
and original line/section constraints retained. 71280cols/152726rows/2160
integer columns before presolve. New full lift and new full KKT/primal checks
are required after solve; old fixed-VRE full feasibility is NOT a requirement.
No old primal/warmstart/bound/result/permit is reused. Preparation calls0native.

First suggested actual solve: HiGHS1.15.1, threads1, relativegap.001, abs0,
600s cap, no retry, new occurrence. Parent controls all native calls. Tiny
coefficient storage must be checked against this new matrix, not R34 counts.

Necessary curtailment in [89,169) is >=678.7558605769167MWh from exact R39 SOC
inequality. It is only a lower bound and is NOT imposed as a fixed curtailment
target. Actual curtailment may be higher for SOC/network/cycles/ramp reasons.
No official publication, grid execution or settlement approval is created.
