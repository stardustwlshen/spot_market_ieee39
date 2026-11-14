# R52 最小已验证 source 视图候选

仅做纯性能/等价性候选，不提供 native 启动或运行许可。活 R46、冻结 R49、R50 求解文件均不修改。

## 差分

只覆盖 source_contract 及一个小视图函数。时钟、ticket、命令/回执重积分、RT/PRICE 数学、阶段文件校验全部从冻结 R46 只读依赖加载；未复制数据树。

原全 case 仍按原流程加载、哈希校验、初态核对、物理源重积分、网络/SOC/AGC 核验。仅在长期 registry 中，不再保存实际运行不读取的整套 576 格 case：

- 保留四族 prior 的完整原 bytes、SHA、available_at 及其余原证据字段。
- 保留 buses、reference_buses、sections，以及每条线实际消费的 from_bus/to_bus/susceptance。
- 记录原全 case 与原完整 line 数据的 codec SHA。
- 原全部 stage 文件哈希清单不变。
- 每次 validate 仍完整计算小视图 memory digest，并核 prior 原 bytes/hash/vintage 必需字段；不是 mtime 缓存或盲信 dict 身份。

## 一次成对纯基准

`compare_once.py` 在同一进程分别加载旧/新源、初始化，构造同一 W4 RT 和 PRICE 并序列化。只比较数学 payload 的 variables/rows/objective/options 和名字，及实际输入/预测/资格/资源等；新契约 metadata/schema 可不同。

记录 cProfile 会有额外开销，单次测量且工作区可能同时执行其他任务。结果只能说明该纯阶段的测量变化，不能外推96窗总耗时或原生求解速度。没有重复完整KKT、全天仿真或调用优化器。

负控包括小视图拓扑篡改、prior bytes与配套hash一起篡改、缺必需字段、同大小且恢复原mtime的stage文件篡改。均须拒绝。

依赖当前工作区与冻结R46路径；不是可独立安装包。以后若接真实运行，须另建R52运行入口/代码manifest/身份，不能借R46 worker许可启动本候选。
