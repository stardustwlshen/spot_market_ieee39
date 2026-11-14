# R24 自由 SCUC → 新固定 SCED → 独立 DA98 定价纯接口

## 当前结果
- `r24_bridge.build_price_dto(case, physical_report, expanded_values, source_receipt, pricing_spec)` 已实现。19 项纯测试通过，包含构建独立定价模型；0 native、0 RT、0 命令、0 支付。
- 不修改 R21/R23；仅复用 R14 的纯公式/DTO校验和物理投影合约生成函数，不复用旧原生认证、旧哈希、旧 R15 capability 或策略身份。
- 接口测试是明确 synthetic fixture，不表示真实 576 格 SCED 已适配/验收。真实接线等待新的实际 SCED primal、physical_report 和独审证书。

## 最小输入
1. 新 R23 原 case（含 tuple-key 原序列，EnergyHorizon 支持 dataclass）。
2. 新 SCED `physical_report` 全 576 格；不得传非最优 SCUC source incumbent 冒充 SCED。
3. `expanded_values` 使用完整原模型符号名：`stable[G,j]`、`off[G,j]`、`power[G,j]`、`battery.charge_mode[B,j]`、`battery.discharge_mode[B,j]`、`battery.charge[B,j]`、`battery.discharge[B,j]`、`battery.energy[B,j]`。idle Expression 严格由 1−charge_mode−discharge_mode 推导，不按零功率猜。
4. 新 `gd.research.r24.free_da_bridge.v1.source_receipt`：见测试 fixture。实际来源还必须给新 SCUC/SCED request/response、expanded witness 原件 SHA256，以及新 SCED primal/KKT 和同一固定整数谱独审确认。此纯函数验证内容绑定，不自行证明调用者填写的证书为真；原件 SHA 与审计结论由受控接线读取验证，不是安全签名/授权票据。
5. 显式 research pricing_spec：delta 与来源、两条 chronological representative（kind/physical_index/weight/source）、每热机价格资格映射来源、每线路/断面的 PTDF、惩罚及来源、终态研究解释。不得用旧 accepted 身份填空，也不得称真实资格/官方申报。

## 数学与因果边界
- 576 格连续物理约束和完整 SOC/每日循环预算不截成 98 格重启；原 D96 目标固定 market index95/物理 boundary288。
- 前 96 点为三个真实 5 分钟均值；代表点取显式三格映射，不把瞬时点当区间均值。代表点不进入发布/结算 96 点数量。
- 保留原正备用/事故备用各 400 MW；非热机无虚构备用 credit。固定 BESS permission 与实际方向分列，零功率不推断执行充电。整数谱/hour_direction 的原模型一致性归新 SCED 独审，本适配器不制造第二份 SCUC 验收。
- 跨季度相位混合、充放方向混合、未支持 group、季度内报价/线路限额变化等未定义映射 fail closed，不默默采样。
- continuous48h 研究目标不是广东正式 DA98 目标。`formal_strict=True` 因真实 declared-target/代表点映射及经济等价性不足仍拒绝。
- 只产生新独立定价 DTO，不拿固定 SCED 对偶冒充 DA 价格。后续完整价格导数应遵循既有物理提升定价数学，包含相应 market/physical 双重 RHS 对偶，不只读冗余 market balance 对偶。

## 非最优 SCUC 诊断支路
`SCUC_accepted=false, diagnostic_only=true, publication_allowed=false` 可建研究 DTO；固定 SCED Optimal 不升级 SCUC accepted。`publish_candidate` 对该支路一律拒绝。普通研究发布候选还须 accepted SCUC/SCED、计划时点之前逻辑可用；新独立 PRICE 原件绑定/独审证书可另补。无价格时明确 pending，绝不零填。逻辑发布时间只是模拟，不是将实际晚到结果伪装为过去已发布。

## 验证
从工作根执行：
```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/guangdong_r24_free_da_bridge_20261112:$PWD/guangdong_r21_day_verified_final_v1_20261112/source/repo/src ieee39-cloud/repo/.venv/bin/python -B -m pytest -q -p no:cacheprovider guangdong_r24_free_da_bridge_20261112/test_r24.py
```
最近结果：19 passed in 3.34s。未做额外归档/备份。

## 新研究参数已经就位
`r24_spec.py` 从 core95 原 SCED/LMP 输入提取数值，逐文件和字段 hash 绑定为新 `RESEARCH_PRICING_SPEC.json`。原件认证/permit/strategy 不进入新授权链。PTDF 独立由新 R23 同一原网络 susceptance/端点重算，最大误差 `6.050715484207103e-15`；全 576 格线限一致。δ、稳定热机/BESS资格、代表点权重、网络惩罚均仍是清楚标注的原研究假设。不是请用户补一个本可复用的数据参数。

`prepare_actual.py` 只读新 diagnostic SCED 独审 receipt、原响应/名称/physical_solution，核对 raw SHA 与全量 KKT 摘要阈值，生成新的 PRICE input/template/names/pins。原始非最优 SCUC 的 gap 和禁止发布标签随 DTO 保存。该脚本不运行 native；后续单次 PRICE 由父线程控制。

## 实际新 SCED 已接入（更新）
`prepared_actual_v1` 已由新 diagnostic SCED 原始 expanded_response/names、physical_solution 与独审 receipt 实际生成，不是 synthetic fixture。独立 PRICE 模板 49,832 列、108,686 行；threads=1、mip_rel_gap=.001、mip_abs_gap=0、time_limit=60s。模板 SHA256 `98b670bb0d9952cf3aa407ec72a6539bf50e092d793fd816d38b763bfc977295`，全部产物见 PINS.json。尚未由本线程执行任何 PRICE。源 SCUC gap=1.3292820644797074、SCUC_accepted=false/publication_allowed=false/diagnostic_only=true 保持不变。

## 来源关联更正
v1 receipt 曾把原 full math request 与后来的 compressed SCUC response 配为同一调用。v1 原件保留但不得用于启动。v2 将实际 compressed request/response 与同目录 IDENTITY、launch/confirmed PID、preparation、compression_plan、postreview 逐一绑定；原 full math request 只作为 `original_full_math_request_sha256` 表达等价原模型来源。20 项纯测试通过（含错配 request/response 拒绝）；0新增 native。

新 PRICE 价格 oracle：每 node/point 的 market balance dual，加 market line upper−lower dual×PTDF，再加该点映射的三个 physical balance dual 与各 physical line upper−lower dual×PTDF，整体除该点 objective_weight_h。代表点亦使用其显式权重；不使用 SCED dual。
