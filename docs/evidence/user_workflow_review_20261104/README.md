# 用户完整操作复核证据

配合[操作复核报告](../../用户完整操作复核_20261104.md)阅读。原始价格、出力、亏损、备用缺额和求解状态未改写。没有新增清零策略或全天求解。

- `integrated_result_journey_fixed.log`：集成后的63项结果页、实际运行卡跨页跳转和历史质量告警测试全部通过。初次失败日志另留，原因是AppTest保留已过期的显式导航请求，不是丢失产品结果选择。
- `recompute_display_prices.py`、`recomputed_display_prices.json`：独立读取四个完整96段和新增机组8段前缀的DA/RT保存价格与负荷重算均价、价差，四位小数序列化误差内一致；源文件哈希不变。重跑时指定`--evidence`为完整结果根，`--generator-prefix`为可选的新增机组调试结果目录。
- `probe_reboot_fix.py/json/log`、`recovery-final-tests.log`：最终恢复代码的独立多记录AppTest及61项复测。旧/外来/存活进程保留占用，原非meta文件不变。未实际重启操作系统。
- `workflow_repro.py`、`workflow.log`：新增五类机组、修改实际参数报价、提交并完成DA96+RT8的真实运行。运行用了独立测试场景30秒DA预算，不能与正式默认600秒全文验收混用。
- `results_cancel_repro.py`、`results_cancel.log`：真实已保存结果利润逐值核对、重复启动拒绝、实际SCUC中请求取消并等边界停止。两份脚本包含本次临时目录，仅作证据；迁移复现需明确替换源/结果路径，禁止指向已有正式输出目录覆盖。
- `eight_types_and_cross_scene_tests.log`：八类十条报价曲线及A→B→A持久化、冻结、全新会话恢复14项通过。
- `model_code_equivalence.json`：本次最终代码与实际全日求解源的18个模型和配置文件逐字一致。UI、进程身份和恢复机制的变化不构成重新求解或更强物理认证。

日志保留真实阶段和缺失/失败；debug_prefix不是完整日结算。Python测试warning或AppTest缺ScriptRunContext提示不作为用户网页错误。Windows/macOS没有原生实测，Chromium因环境权限未完成像素验收。

`final_user_workflow_suite_ca7eefe.log`：最终整树1533 passed、0 failed、0 skipped，741.33秒，退出0。以IEEE39_SAVED_RUN_FOR_UI、IEEE39_SAVED_GAP1_RUN、IEEE39_OPERATOR_EVIDENCE提供本轮和历史存档；源码ZIP不包含原始结果，未另提供档案时相应回放会按设计跳过。
