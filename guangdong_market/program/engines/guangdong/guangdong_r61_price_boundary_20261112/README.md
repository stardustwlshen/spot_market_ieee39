# R61 独立 C 定价边界与跨午夜

0 native。只从新 R60 已实际接受的 RT input、票据、同次 raw/acceptance 和源码 pins 生成独立 PRICE；不重建完整309格contract，不读取旧 live permit，不把 DA 状态传回 RT。每窗仍校验源码 bytes/hash、实际 checkpoint/ticket/input/acceptance 身份和纯 PRICE 输入签名。

`provider.boundaries(inp, PricingStateBasis())` 与 `transform.transform(q,n,inp)` 是独立通用接口，不绑定 R42 路径或报价；可由 R62 新 source 传入其同源 input/matrix。当前 `prepare.py` 仅是严格 R60 schema、R42 bid0实际适配器，不能用来混入 R53 −50 分支。R62 应用其独立实际来源适配器，不变通本适配器门禁。

## C 含义
1. PRICE 初始 SOC 替换为同源 DA 首格 `energy_begin_mwh`。
2. 每个自然日 U 从该日期首个DA格的末累计吞吐减本格内部吞吐得出。当前日是窗开始的 DA-prefix U；下一日必须从00:00首格开始，并交叉原下一日 cycle_budget.used_internal_mwh，不把当前日累计转给次日，也不无依据清零。缺日期/来源拒绝。
3. 每3条SOC递推精确聚合为季度递推，移除非季度边界80个SOC变量及其5分钟中间硬界；保留24个功率lift及ramp、日期throughput系数，只按U差移动各日期行RHS。此为研究定价域，不是原物理执行模型。
4. 原 D96 等式在 k=24（W88）、21（W89）、3（W95）等原索引原数值保留，不变成尾窗终点。最后窗延伸次日物理参考仅为同一48h研究来源，不称已发布次日正式DA。

所有非battery-SOC递推/指定日期U边界数学、DA±.1、网络、VRE、AGC、400及报价保持原同源PRICE构造。PRICE只有季度电气网络；无正式映射/金融价，market/settlement=None；成功也不回填旧R46/新R60 PRICE或运行状态。

## 当前可启动单窗
有效批次 `batch_W46_v2/BATCH_PLAN.json`，SHA f81042d8959f5d02415467758899de14a5a80f854ada8d09e54bfe6fce81ae0b；真实已接受RT来源，PRICE1821行1206列。早期pure输出仅调试保全，不可启动，源码pins已过期。
```
PYTHONDONTWRITEBYTECODE=1 ieee39-cloud/repo/.venv/bin/python -B guangdong_r61_price_boundary_20261112/launch_batch_once.py --plan guangdong_r61_price_boundary_20261112/batch_W46_v2/BATCH_PLAN.json --plan-sha f81042d8959f5d02415467758899de14a5a80f854ada8d09e54bfe6fce81ae0b --output guangdong_r61_actual_price_20261112/native_W46_v1
```

后续只在请求的全部真实RT输入/acceptance齐备时运行 `prepare_batch.py --suffix <actual suffix root> --windows 47,48,... --output <new dir>`。不会等待、伪造未来输入或阻塞RTsuffix；缺任一来源显式拒绝。其结果BATCH_PLAN哈希传相同launcher。每窗一次60s/threads1/gap.001/abs0、无重试；输出目录必须新建。完成自动全KKT及首季39节点raw LMP后审，非Optimal/证据不足保留pending。未再审W8，也没有新求解。

5项小纯测试：D96 k24/k21/k3、跨日U重置来源、RT/formal拒绝、缺日期与次日账不一致拒绝。初次pure适配字段名bid_proxy校正为真实bid_proxy_yuan_mwh；未产生native/原件修改。
