# 可移交的 IEEE39 实时重新组合研究程序

此目录可复制到任意位置，从任意 cwd 调用。vendor 保留 145 个必要源码相对布局；没有复制历史原始解。Python 3.11+，依赖见 requirements.txt。也可 `--dependency-root` 指向具有相同字节和相对布局的依赖目录，逐文件校验。

## 新研究运行
```sh
python program.py verify
python program.py seed --da-run /绝对路径/已接受DA运行 --run /绝对路径/新RT运行
python program.py build --run /绝对路径/新RT运行 --window 0
python program.py run-one-window --run /绝对路径/新RT运行 --window 0
# 上条只是预检；下面才明确启动真实求解
python program.py run-one-window --run /绝对路径/新RT运行 --window 0 --execute-native
python program.py handoff --run /绝对路径/新RT运行 --window 0 --simulate-no-deviation
python program.py build --run /绝对路径/新RT运行 --window 1
python program.py run-one-window --run /绝对路径/新RT运行 --window 1 --execute-native
```
DA 必须符合既有 ARTIFACTS 接口，文件 hash 正确且 accepted，具备完整 48h case、policy、AGC、DA-SCED names/expanded response。首窗 build 使用其已验物理解作为**待检查的可行启动点**，不是冒充新求解结果。第一次 build 后完整启动向量已在新 template 内。不是任意数据自动转换器；未知输入失败，不填资格或实测数据。

每窗 SCUC 默认 HiGHS 1.15.1，300 秒墙钟预算、threads=1、gap=.001、abs_gap=0；可 build 时显式 --time-limit 120/180/300。随后固定本次整数做 SCED（120 秒、dual_tol=1e-10、scale=0），全行/整数/gap/完整 KKT 门禁不降低。SCUC 非达标、缺响应或未知启动即停止，不自动重试。已完成 occurrence 仅复核，不再次求解。已有输出拒绝重建；旧版本、其他运行、修改后的 request/source 不能混用。

## 严格边界
- handoff 是**未经 AGC/AC 安全放行的反事实研究模拟执行**，不是表计、调度回执或正式可执行计划。默认不推进；显式参数才假设跟踪误差为零。输出 safety_release=false。`--mode formal` 拒绝执行。
- 每窗 SCUC+SCED 接受仍待独立 PRICE、AGC/联合安全、AC及正式结算。不能用 SCED dual 当独立价格；不自动全日扩展。
- 原 400 MW 两项备用不变。沿用统一启动费用/曲线、原研究预测、绝对 48h 事件轴与已下发前缀；不是正式三态或真实运营输入。当前原案例近端热机稳定，不能声称演示新热机启停。
- 新运行 seed 把 DA 前三格明确假设已下发，0 已执行；每次 handoff 推进15分钟，已下发不等于已执行。原始两窗证据未改写。本入口不直接接管旧固定DA链，也不静默迁移旧通用两窗的 reviewer/source pins。
- 控制层不提供强制中止/不明结果自动恢复。进程重启仅对完成且哈希一致的同一 occurrence 做核验。

## 验收
8 项纯测试见 PURE_TESTS.log。W0 纯构造为 264048 列、248256 行、18845 整数；与原实算 warm300 模板及 names **完全相同**，见 PURE_MATH_COMPARISON.json。本新程序未执行 native；原两窗实际结果属于原冻结实现，不能当成此新 CLI 已实际运行。
初次 pure_user_flow_v1 保留纯构造证据，后加 handoff 哈希闭环后其源码身份已过期，故不能执行；最终可用纯准备为 user_ready_run_v1。不把此测试目录包含入轻量源码发行包，用户应自行 seed。
