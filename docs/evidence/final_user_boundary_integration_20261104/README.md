# 最终用户操作边界集成证据

- `final_release_full_suite.log`：应用/测试提交39568d6，1615通过、无失败或跳过，928.02秒，退出码0。
- `dependencies.json`：Python3.12与14个锁定依赖匹配。
- `quote_recovery_freeze.log`：八类机组从文件恢复到持久报价、提交冻结和模型读取一致，8项通过。
- `whole_resource_loss_before.json` / `whole_resource_loss_after.json`：基于真实96段档案的整主体缺失故障注入，分别删RT-PV1、DA-PV1、RT-ES1状态，修复后均拒绝，原131文件不变。
- `protected_source_equivalence.json`：58个src/config/data/bids文件与90d3771一致。`test_profile_restoration.json`记录隔离测试生成样本曲线后恢复原字节，不发布测试产生的曲线。
- `historical_archive_hashes.json` / `historical_archive_preservation_result.json`：6个档案、727个文件前后SHA256一致。
- `archive_business_recheck.json` / `independent_price_recompute.json`：四个完整日及合法前缀的独立只读重核。备用缺额、实际gap和负利润保留，未新增全天求解。
- `final_code_distribution_smoke.log`：39568d6源码ZIP全新解压后只读准备、四页AppTest、96/96与8/96区分、整主体坏档恢复及跨范围对比。非新安装依赖、非真实浏览器或原生Windows/macOS验收。
- `intermediate_wrapper_status.json`：中间a37f1d3的pytest虽通过1606项，外围脚本收尾异常；该轮明确不代替最终39568d6的成功退出证据。

后续仅修改说明和规范新增日志行尾空格，应用、测试、模型和默认值均不再改变。正式交付ZIP/bundle哈希与解压复核结果另存交付清单。
