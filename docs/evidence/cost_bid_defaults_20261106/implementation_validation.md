# 分类型与容量默认成本报价实施验收

日期：2026-11-06。隔离实现；未提交、未推送。先行方案已同步项目 Page 序列17后实施。

## 本次结果

- 聚焦主回归：349 passed / 193.28s。包括全类型资源编辑、成本与报价分离、利润、旧场景复制、17资源报价与UI；此次后仅补了随机报价旧成本依据转历史的修复。
- 修复后补充：151 passed / 75.11s，覆盖容量新算法、报价随机、成本依据、价格上下限、显式报价、场景组合及默认补填历史来源。
- 修复后随机报价UI：20 passed / 135.91s；输入语义、恢复、连续编辑、场景复制UI：60 passed / 415.59s。
- 最终代码再次执行：15 passed / 9.80s，含全部14项容量/保护/完整构模测试，以及全类型新增资源8段DA+RT实算。
- 另行检查：机组编辑UI 19 passed，输入fail-closed 29 passed；compileall src app scripts通过。
- 测试集合有重合，不把以上计数相加声称唯一测试总数。日志随本目录交付。

## 实算与模型核对范围

- 新默认v5完整17资源、96段构模；10176项目标系数核对通过。30336个变量、36922个约束为此隔离实现合并水量修改前的构模计数，不是最终模型大小。
- 最终15项中的实算为 `tests/test_unit_editor.py::test_saved_all_types_complete_small_market_pipeline`：两节点8段DA+RT小系统，九种编辑入口（煤、CCGT、GT、风、光、水库水电、径流式水电、电池、抽蓄）逐项保存后真实求解、导出与说明性前缀结算，余额残差低于1e-6；不宣称IEEE39全日或正式结算验收。
- 最终集成须在水电改动全部合并后统一跑IEEE39全天DA+RT96，受控丰枯对比保持相同冻结v5，仅变物理水情。

## 历史与手工保护

- 原始config/fixed_costs.yaml未改；原有逐机组成本优先。容量代理只用于新生成/明确重建研究成本。
- 旧报价v1—v4 JSON/CSV字节与SHA256不变；旧场景27个文件（包括当前手工草稿）逐文件字节不变。哈希证据见old_bid_sha256.json、complete_default_model_check.json。
- 缺default_bid_model的旧场景继续legacy_v1。显式旧生成器v1档案原值、哈希与版本来源保留。新资源/明确生成使用v2。
- 回归中历史模板或旧golden夹具显式选择legacy_v1；最新版测试独立验证v5。UI采用成本测试先制造实际差异，避免用新默认本已对齐的无变化预览假测“已采用”。场景报价手工修改末段以保持合法单调，不改变价格校验规则。
- 当前报价随机预览会把已有_cost_basis移入_cost_basis_history，不将历史成本依据误标为新随机报价的公式来源。

## 复现

依赖环境：PYTHONPATH=/workspace/shared/ieee39_locked_deps_20261106:src；STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false；MPLCONFIGDIR=/tmp/ieee39-final-mpl。

主回归命令：python -m pytest -q tests/test_capacity_cost_defaults.py tests/test_research_defaults.py tests/test_bid_cost_basis.py tests/test_operating_profit.py tests/test_research_cost_setup.py tests/test_bids.py tests/test_scenario_cost_copy.py tests/test_unit_editor.py tests/test_bid_simulation.py tests/test_high_pv_default.py tests/test_storage_expansion_default.py tests/test_unit_input_view.py tests/test_cost_archive_recovery.py tests/test_input_recovery_ui.py tests/test_new_default_scenario_ui.py

补充命令：python -m pytest -q tests/test_capacity_cost_defaults.py tests/test_bid_simulation.py tests/test_bid_cost_basis.py tests/test_bid_limits.py tests/test_explicit_bid_input.py tests/test_typical_scenarios.py tests/test_default_fill_provenance.py

随机报价UI：python -m pytest -q tests/test_quote_simple_ui.py

其他报价UI：python -m pytest -q tests/test_quote_input_semantics.py tests/test_quote_backup_recovery.py tests/test_quote_continuous_edits.py tests/test_scenario_quote_copy.py

最终实算/构模：python -m pytest -v tests/test_capacity_cost_defaults.py tests/test_unit_editor.py::test_saved_all_types_complete_small_market_pipeline
