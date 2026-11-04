# 电网操作边界复核证据

主报告：[电网操作边界复核](../../电网操作边界复核_20261104.md)。所有真实档案只读，故障注入仅在内存或临时副本，未新增市场求解。

- `before_pinned.json` / `after_pinned.json`、对应日志及 `probe_archive_boundaries.py`：固定依赖下，90d3771与修复代码同档案对照；缺第41段、缺节点16、整数浮点时段、缺整个RT价格文件。每次核对原档131文件，哈希未变。
- `final_operator_regressions.log`：194项通过，3项历史gap1档案回放因本次首轮未指定路径跳过，无失败。涵盖新增损坏恢复、四个96段档案切换、合法DA96/RT8、对比、拓扑、导出、备用、弃电、取消/失败状态与恢复。
- `historical_gap_regressions.log`：为上述3项补上已有真实历史档案路径后单独复跑，3项全部通过（8.53秒）；不是新求解。
- `cache_final_regressions.log`：最后缓存失效补丁后17项通过，覆盖CSV删除/修复、DA子目录、冻结线路倍率/资源标签更新及既有网络/来源测试；和上述测试有重复，不相加作独立总数。
- `focused_regressions.log`：此前89项相关回归通过。`topology_export.log`：真实DA/RT首末段、节点3/L2-3以及原表筛选1项通过。
- `archive_checks.json`：四个已有完整日存档的业务量和哈希重核，与上轮证据完全相同。`environment_and_archive_equivalence.json` 记录固定依赖版本和该一致性。
- `initial_*`、`before.json/log`、`integrity_regressions.log`及`comparison_regressions.log`保留早期开发过程。最初误用共享目录的pandas3.1.0，不是本项目固定2.3.3；另有新校验把逐时结算多主体误认重复及新测试未使用中文显示列名的失败，均已修正并回归。这些日志不作为最终通过证据。

没有浏览器像素、浏览器下载落盘或Windows/macOS真机结果。没有因不利日的真实备用缺额改变模型或美化输出。源码包不含完整市场输出，真实回放须明确提供环境变量，未提供时会按设计跳过。

集成追加的整主体丢失检查：`whole_resource_checks.json`记录四个真实96档案正常通过及三种整主体删除被拒绝；`whole_resource_regressions.log`是本补丁AppTest与相关回归，94项通过、无失败或跳过（160.44秒）。集成侧独立修改前证据另保留于其交付证据目录 `whole_resource_loss_before.json`，确认三例旧检查均放行且131源文件哈希不变。本补丁仍允许旧RT完全缺TIE的警告路径、合法禁用/转换固定负荷资源及未保存的可选历史状态表。
