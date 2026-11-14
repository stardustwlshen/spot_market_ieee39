# 统一短域 v6 研究入口（从v5继承）
默认稳定机组路径承接v3循环尾预算约束；加入不改变模型数学的3格位移部分MIP提示，以及仅对1e-10 MWh内硬边界浮差、同时通过原生SOC及独立fsum校验的SOC规范化。原始解和循环账不改，实质越界拒绝。起始DA seed无上一RT解时不伪造提示。

仍保留稳定/保守DA保护guard，不声称一般火电重新组合。HiGHS gap0.001、400MW、原SOC目标与循环限额不变。不自动重试未知求解。Callback checkpoint尚未接入。

`python user_entry.py rt|price ...` 是原用户程序接口。新增 `experimental info` 查看隔离接口限制；其他实验子命令必须给 `--explicit-research-laboratory --input JSON --output 新文件`：
- thermal-plan / thermal-publish / thermal-boundary：已验三态小算例适配与研究模拟通知/状态传递。只支持命名R68零最小时间源适配；停机许可未接通。未来pending可改变，issued不可改。不会绕过默认RT guard。
- agc-produce / agc-project：显式资格/报价/性能/研究参数下的单小时算术及未来段替换。输入键对应hourly.py函数；expected_plan_sha256须来自独立可信宿主导出，不是从不可信计划自取的认证。不同滚动窗口可复用同小时receipt，禁止同一消费者重复叠加。

实验输出不会自动用于当前RT构模，也不授予风光/储能资格。还必须新SCED、同固定UC调频0弃新能源对照及AC安全。研究结算模型已实现；实际账单与支付不在本仿真任务范围。此处不是正式调度或安全发布能力。原独立模块的实测结果摘要位于experiments，不表示在v6再次求解或完成全天。

V6：PRICE网络默认1000，来源区域表项18、保留显式override；96季度完整新成本KKT为解析采用，不是新native，旧raw不改。原CLI11全天在只读历史面板展示。默认AGC安全gate未放行，不能自动逐机剔除。详见主项目docs/统一短域v6研究入口.md。
