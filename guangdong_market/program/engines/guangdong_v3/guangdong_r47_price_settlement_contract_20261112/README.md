# R47 pure price and VRE settlement-input contract

Two new standalone pure modules. No optimizer, network, publication, payment, native dispatch, file mutation API, R36 calls, or old-tree modifications. API values use finite Decimal and timezone-aware datetime.

## Price boundary

PriceLimitSpec retains region, stage, unit, Decimal limits, effective interval, source URL/ID/status, search timestamp, no-expiry-known, override status, and dynamic-current-verification flag. Public parameter baseline −50/1800 CNY/MWh, effective2025-11-01 with no explicit expiry, can support a published_policy_basis candidate in2026. dynamic_current_verified=False does not block this calculation and does not certify current official applicability. A known replacement/conflict requires a new resolved spec; this implementation does not select one automatically. Dates are effective_from inclusive/effective_to exclusive.

Source provided for review: Guangdong263 announcement https://drc.gd.gov.cn/gkmlpt/content/4/4775/post_4775501.html, publication2025-09-19, effective2025-11-01. “2025年暂按上述参数执行，后续视情况调整” is not encoded as an invented December31 expiry. Latest-search claims and official source interpretation remain externally reviewed input metadata, not facts discovered by these modules.

Retained regional rule PDF SHA978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf: §2.2.11.5 supplies per-node/per-period limit substitution; §3.6.1.2 supplies arithmetic hourly mean of four quarter prices; §3.6.2 requires verification/publication, not automatic promotion of solver output. Four quarters are individually limited before averaging. Unknown native/invalid data are rejected, not classified as missing. Confirmed missing routes to pending only; no invented DA/cross-run fallback. Same-run fallback itself is not implemented.

Success requires raw evidence identity and64-hex SHA; this pure interface validates syntax/association fields, not external file authenticity. It preserves raw_price, outputs bounded_candidate_price, and ALWAYS leaves official_market_price and settlement_price None. Publication requires external verified official release. Synthetic specs yield illustrative candidates only. Hourly aggregation expects candidate dictionaries produced by this trusted module, not an authentication boundary for untrusted serialized output.

## VRE readiness / synthetic arithmetic

Explicit participates/does_not_participate/unknown. Real unit/resource/node mapping is required for formal readiness; bus aggregates remain synthetic. Metered quantities cannot be replaced by simulated executed quantities. Missing contracts are not zero; even a known-empty contract list requires a source. Raw-only prices, unknown financial DA, missing meter or participation retain amount=None, formal_bill=None, payable=False. Nonparticipants require a separate non-DA rule mapping; no automatic QDA=0 shortcut. Monthly mechanism, green certificates and AGC mileage remain unresolved, never invented zeros.

Independent explicitly synthetic participating-DA arithmetic follows supplied source: 南方监能市场〔2025〕146号第十一条, PDF pages7–8, https://nfj.nea.gov.cn/xwzx/tzgg/202601/P020260104519707491129.pdf:
LT=sum Q_L*(P_c+P_DA-P_ref); DA=(Q_DA-sum Q_L)*P_DA; RT=(Q_meter-Q_DA)*P_RT.
Each scalar must carry matching node/interval/unit/source. The synthetic fixture produces22500+20000+5000=47500; no formal amount/payment claim. It is not a coal/gas proxy call toR36, not a full monthly renewable settlement engine, and not a bill builder.

Run pure tests:
PYTHONDONTWRITEBYTECODE=1 ../ieee39-cloud/repo/.venv/bin/python -B -m pytest -p no:cacheprovider -q

## Actual R45 binding
bind_r45_vre reads a hash-pinned already-verified post_verification file, checks four consecutive quarters, same run and exact three command/receipt identifiers, A=P+C within1e-8MWh, and absent actual meter/official prices. actual_binding.json retains20 actual simulated bus-quarter records (5buses×4quarters), quantity/source distinctions and readiness missing reasons. It does not rerun R45 KKT. This is a pinned binding, not an independent authentication of upstream receipts. Contract reference spot price Pref remains separately explicit; it is never defaulted to DA node price.

baseline_registry.json distinguishes clearing cap1800 from declaration cap unknown. Price functions apply clearing price limits only, never bid declaration clipping. declaration cap requires the separate trading-rule reference.

## Final interface corrections
Contracts require unique contract_id and explicit reference_node; Pref is bound to that reference node, which may differ from the generator node. Pending R45 raw price is permitted only with unpriced_pending and None; unknown status rejects. Hourly calculation reconstructs each quarter from raw and its policy spec and rejects a modified bounded value. Monthly mechanism/green certificates/AGC mileage are separate_unclosed_components, not prerequisites for the limited three-component energy calculation; their values remain None and no complete bill is claimed. Initial12-test log is preserved; final16 tests include these four controls.
Final audit controls additionally require confirmed_missing.raw_price=None, exact quarter-hour start boundaries, explicit matching contract/Pref reference_market DA or RT, and nonnegative VRE QDA/Qmeter. Final test count17.

## Independent final check and actual price application

The final independent test run is `independent_tests.log` (17 passed). `INDEPENDENT_RULE_NOTES.json` distinguishes independently opened regional/146 sources from the 263 official text reviewed by the source researcher; this review's direct 263 re-fetch timed out. No current operator parameter feed was accessed.

`apply_verified_r45_prices.py` binds the already completed R45 independent review and all four original PRICE response identities. It does not rerun KKT or an optimizer. `R45_PRICE_CANDIDATES.json` contains 156 quarter-node candidates and 39 hourly-node candidates: raw −1000 is preserved; published-baseline bounded candidate −50 is separately calculated before the hourly mean. `stage=RT` means realtime market price from the independent PRICE LP, never the dispatch LP's dual. None of these values is an official market or settlement price. No upstream R44/R45 publication flag is changed.

The helper's `MANIFEST.json` is a prior handoff snapshot. `FINAL_MANIFEST.json` is the final whole-directory inventory, including the independent review and actual application.
