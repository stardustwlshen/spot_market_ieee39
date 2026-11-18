# Editor draft integration verification

Base: b8f8907698c39630c5f62ddd6914331f5ea566c1.

Changed only presentation, session draft lifecycle, and unsaved-input launch gates. Thermal, storage/pumped-storage and renewable edits require save or discard before launching DA or RT. Switching resources preserves drafts; empty optional resource lists no longer crash. Existing validators and snapshot writer remain unchanged.

Installed the repository-pinned Python dependencies into a clean Python 3.12 environment. Ran the actual default Streamlit entry and real validators/snapshot writer, without backend doubles. Confirmed thermal capacity, storage power and renewable price modifications reach the serialized snapshot and original scenario remains unchanged.

Affected regression group: 93 passed in 58.81 s:
- test_provincial_entry_smoke.py
- test_editor_drafts_integration.py (new)
- test_resource_inputs.py
- test_resource_add.py
- test_thermal_capacity_editor.py
- test_thermal_minimum_editor.py
- test_forecast_consumption_binding.py

The aggregate `pytest tests` cannot be called passing: 26 collection errors from historical audit scripts with global module replacement and legacy module paths. An untouched worktree at the same base reproduced 26 collection errors (319 tests collected). These were not suppressed or reclassified as passing. No full 96-period solver rerun, real browser or Windows acceptance is claimed by this change.

Other local worktrees and unpublished patches were left untouched. This commit does not claim to include those independent changes.

Runtime source and application inventories updated for changed files and new draft modules. The actual live source gate and inventory consistency tests also passed (3 tests).
