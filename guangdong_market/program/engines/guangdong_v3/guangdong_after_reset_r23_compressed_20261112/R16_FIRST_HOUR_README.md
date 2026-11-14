# R16 first-hour candidate

Read `docs/117_R16_FIRST_HOUR_RELEASE.md` first. This source preserves every
accepted R15 file unchanged and adds isolated R16 modules. It contains no
approved native release and does not claim actual first-hour results.

The only intended future native entry is
`experiments/run_r16_first_hour_priced.py`, under the parent-reviewed source,
runtime and exact eight-call release. The pure inspection entry is
`experiments/inspect_r16_first_hour_pure.py`. New tests are
`repo/tests/test_gd_r16_first_hour.py` and
`repo/tests/test_gd_r16_energy_ledger.py`.

All Python checks must use PYTHONDONTWRITEBYTECODE=1 and python -B. Disable
pytest cacheprovider, place logs and all outputs outside this source, and do
not run solver-backed historical tests. The existing environment's interpreter
is `ieee39-cloud/repo/.venv/bin/python`; no installation is needed.

Independent review should rerun the focused R16 tests and pure RT/PRICE matrix
inspection, then the exact retained pure regression list recorded in the
external builder gate. Nine historical R13 checkpoint tests require a missing
explicit V3 archive/source and remain skipped; that is not R16 acceptance.
The six R14 static inputs are in the accepted core's `input/static_original`
and must match `gd_r14_da_inputs.STATIC_SHA256`. An external test-only ROOT
relocation may expose the unchanged six-file `inputs/ieee39_v6` layout. Never
rewrite fixtures or weaken source production paths.

Before new native work: independently review the full source manifest,
complete pure gate, all serialized stage bounds, source input manifests,
original official-reference pricing policy and runtime identity; verify the
source backup by actual download; then let the parent issue a distinct release.
No builder test result is an approved release or accepted native response.
