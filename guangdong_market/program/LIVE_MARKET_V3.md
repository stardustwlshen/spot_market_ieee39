# Live market v3 增量：0 native 通用输入/界面验证

用户副本：../guangdong_market_user_app_v11_candidate_20261113。

## 实现
- source.TypedSource(host) 只接同模块 RollingHost/session 内存对象，不能用用户 JSON 选择代码/声称接受；验证全 portable 源、host源、接受pair及注册exact carry。Snapshot为内存能力，payload改动拒绝。
- host选择当前DA/seed/run/branch；生产API无固定四窗目录。source_kind/原来源由专门只读历史宿主显式区分。
- PriceSession.prepare/execute/hour 与 Pipeline 单进程持有权威；独立PRICE数学在固定子进程执行，价格与核算不串旧base。
- 默认0调用，完整新会话明确最多12次：seed PRICE，W0–W2每窗SCUC/SCED/PRICE，W3 SCUC/SCED，随后0native H0核算。
- portable v2已包括host_export依赖、SCED显式simplex、全源码门禁和数值/系统安全分层。不自动fallback，不读取JSON恢复live authority。
- UI新卡展示同一新分支的窗进度、四季度价和H0核算；更换DA/run/名称清确认，重复/失败/未知不重启，缺价不补0。

## 实际验证（没有额外求解）
- HISTORICAL_TEST.json：专门固定历史registry逐pair/full数学/carry重验真实四窗，再交通用TypedSource；W0纯PRICE与已验真实矩阵variables/rows/objective相同。只读原4份PRICE与新执行视图，H0重算54行、984919.7573504839122519145572元。
- TWO_INPUTS_TEST.json：两套不同合法DA（solar90/c1bid1与原cold-install）在新v11安装各自fresh准备run，独立branch/seedPRICE纯构模均过，0native。
- UI_TEST.json：AppTest默认0、显式12调用计划、simplex、切身份清确认；mock单次启动/重复拒绝、unknown不重启；拒绝JSON宿主；12调用顺序用mock检查，不冒称新真实12次。
- test_historical.log保留初次缺少source_kind的历史读取拒绝，v2历史factory修正后测试通过，未触发native。

## 真实边界
本增量没有从新v11按钮再次真实运行12次。真实4窗+4PRICE/H0证据来自上一冻结阶段；本次以清晰历史来源重验，不伪造新接受或改写code_pins。未验证第二场景实际求解、IEEE39非零启停、全天新流水线、AC/AGC系统安全，也不支持持久会话恢复。停机显式研究API另目录提供，尚未接本UI。固定五机各10MW/50MW资格和400/400MW保持；不支持任意新调频资格。首锚、δ、储能域等研究解释仍明示。研究核算不是实际账单/利润/支付。
