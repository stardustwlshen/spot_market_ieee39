# IEEE39 电力现货市场出清模型（日前 → 日内滚动 → 实时 SCED → 偏差结算）

在改造的 IEEE 39 节点系统上实现完整的现货流程：**日前 SCUC/SCED 节点电价出清 → 日内滚动优化 → 实时 15 min SCED → 日前/实时两部制偏差结算**，支持报价版本管理和改报价。

**规则**：只有一套自有统一规则 `MKT`（“自有统一现货市场规则（参考川、甘）”，`config/modes.yaml`）。每个参数取四川 V4.0 / 甘肃 V3.2 中合理的一边，并在行内注明出处；规则原文没有给出数值的标为“假设值”。所有参数都可配置，流水线只运行这一套规则。

> 带“假设值”标记的参数均不是规则原文中的数值，只用于让算例可解、有代表性。规则来源和条款见方案页。

## 快速开始

```bash
cd /workspace/spot_market_ieee39
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt   # 已建好可跳过
. .venv/bin/activate

python scripts/main_full.py                    # 一键全流程：日前→日内(每1h)→实时(15min,前瞻2h)→结算，最新报价版本（约 6 min）
python scripts/main_full.py --bid-version 1    # 复现下文“一次全流程结果”（v2 为 demo_rebid 生成的改报价版本）
python scripts/main_full.py --periods 8 --id-interval-h 2   # 只滚动前 8 个时段（快速检查，约 2.5 min）
python scripts/main_full.py --rt-lookahead-h 1 --storage-state fixed --no-plots
python scripts/main_da.py                      # 只做日前出清（丰水期、最新报价版本，约 2 min）
python scripts/main_da.py --bid-version 1      # 指定报价版本
python scripts/main_da.py --season dry --time-limit 120 --no-plots
python scripts/demo_rebid.py                   # 改报价演示：违规报价被拒 → 修正提交 v(n+1) → 改前/改后对比（约 3 min）
python scripts/make_default_bids.py            # 重新生成默认报价 v1
python -m pytest -q tests/                     # 全部测试（约 3 min）
python scripts/bench_scuc.py plain tight ws hourly   # SCUC 求解参数/提速措施对比（逐个顺序运行）
```

## 目录结构

```
config/base.yaml     运行参数：日期、季节、规则集、负荷缩放、线路限额压缩、求解器
config/units.yaml    机组参数：燃煤×3、燃气 CCGT/GT、风×2、光伏、水电站（4 台，G5@34）、储能×3、区外联络线
config/modes.yaml    统一规则 MKT：限价、分段、启动状态、备用、罚因子、日内/实时/结算参数（逐项注明出处）
data/case39/         IEEE39 母线/支路（scripts/export_case39.py 由 pandapower 导出）
data/profiles/       96 点负荷/风/光预测与“实际”曲线（固定随机种子）
bids/{date}/MKT/bids_v{n}.json|csv   版本化报价（JSON 为准，CSV 便于查看）
src/smm/
  config.py   network.py(PTDF)   forecast.py   bids.py(报价生成/校验/存取)
  case.py(日前算例、备用需求)   scuc.py(Pyomo MILP/LP、对偶取价)   da.py(日前流程)
  results.py   plots.py   pipeline.py
  rolling.py(日内滚动 + 实时 SCED，系统状态推进)   settlement.py(两部制偏差结算、对账)   full.py(全流程、校核、输出)
scripts/  main_full.py  main_da.py  demo_rebid.py  make_default_bids.py  export_case39.py  bench_scuc.py
tests/    test_bids.py（快速）  test_da.py（一次日前出清共享）  test_rolling.py（缩短滚动 8 时段）
outputs/  每次运行的结果（不入库）
```

## 模型要点

**网络**：直流潮流 PTDF，参考母线 31，46 条支路（变压器视为支路）。部分线路限额按 `line_scale` 压缩以制造阻塞（假设值）。
节点注入 `inj[n,t]` 由节点平衡等式定义，系统平衡为 `Σ inj = 0`，线路为 `−F̄ ≤ PTDF·inj ≤ F̄`（带罚松弛）。

**SCUC（MILP，HiGHS）**
- 火电（燃煤、燃气）：分段报价、启停 0-1 变量、最小开停机时间（含初始状态）、爬坡（启停时为 Pmin）、热/温/冷启动费（8/72 h 三态，川V4.0）。燃气同样参与报价。
- 新能源：报量报价，出力 ≤ 预测，可弃电。
- 水电：电站级分段报价、聚合机组（每个出力区的开机台数为整数变量，避开振动区）、水量平衡、生态流量、末库容、日电量上限、弃水罚。
- 储能：充放分段报价、SOC 动态、效率、末 SOC 约束；充放状态始终用 0-1 变量 `u_ch`、`u_dis`：`u_ch + u_dis ≤ 1`，`Pc ≤ M_c·u_ch`、`Pd ≤ M_d·u_dis`，且每个报价段 `pc[k] ≤ Lc[k]·u_ch`、`pd[k] ≤ Ld[k]·u_dis`。big-M 取紧值：M = min(额定功率, 各段之和, 一个时段内 SOC 可用区间对应的功率)。`solver.storage_binary` 默认 true，false 只用于与旧松弛做法对比。
- 区外联络线：固定计划（价格接受者）。
- 备用（工程化处理预测误差）：正备用 = 最大单机/N-1 + ρ_L·负荷 + ρ_RE·新能源预测，负备用同理；提供者和系数见 modes.yaml（假设值）。
- 罚：失负荷/弃负荷、线路越限、备用不足。

**SCED 与节点电价**：固定 SCUC 的整数解（火电启停 u/v/w、水电出力区、储能充放状态 u_ch/u_dis）后把模型改成 LP 重解，读取对偶：
- 节点电价 π = 节点平衡约束对偶 / dt；能量价 λ = 系统平衡对偶 / dt；
- 阻塞分量：μ⁺ = −dual(上限)/dt，μ⁻ = −dual(下限)/dt；
- 分解 π = λ − PTDFᵀ(μ⁺ − μ⁻)，每次运行都会核对（误差约 1e-12）；
- 出现松弛时，会改用节点电价计算罚因子（`pricing_penalties`）重算价格，最后按出清限价截断。

## 求解提速（SCUC，HiGHS 1.15.1，逐个顺序运行）

> 下表是统一规则前（旧四川口径报价 v1）测得的；统一规则 MKT 报价 v1 的日前 SCUC 为 112 s、gap 0.1996%、目标 6,042,962 元（报价不同，目标不可直接比较），提速结论不变。

储能改为 0-1 后，整数变量 1728 个（其中 0-1 变量 1536 个）。LP 松弛下界约 4.60M，最优约 4.83M，差距主要来自燃气机组启停和储能状态的分数解；HiGHS 的大部分时间花在强分支上。

| 方案 | SCUC 用时 | 目标（元） | gap | 结论 |
|---|---|---|---|---|
| 旧：储能互斥松弛 + 事后校验 | 86.6 s | 4,829,379 | 0.19% | 已弃用 |
| **0-1（默认）** | **109–110 s** | **4,831,823** | **0.199%** | 采用 |
| + threads=8 | 109 s | 同上 | — | 无效：HiGHS MIP 实际只用 1 个 worker |
| + mip_heuristic_effort 0.02 / 0.3 | 129 / 152 s | — | — | 更慢；0.3 有一次未找到可行解 |
| + mip_pscost_minreliable 2 / 0 | 170 / 213 s | — | — | 更慢 |
| + 启停能力收紧（`tighten`） | 142 s | 4,837,955 | 0.135% | 下界提高但更慢，且启停当段不能留上调备用，改变了模型，默认关 |
| + 热启动（`warm_start`） | 116 s | — | — | 启发式可行解约 5.06M，质量差，无收益，默认关 |
| 收紧 + 热启动 + gap 0.5% | 133 s | 4,857,746 | 0.5% | 无明显收益 |
| 火电按小时启停（`commit_step_periods: 4`）| 53 s | 4,852,640 | — | 快一半但成本 +0.4%，属工程简化，仅可选 |

需要更快时，可在 `config/base.yaml` 调 `time_limit_s`、`mip_rel_gap`，或打开 `commit_step_periods: 4`；其他 HiGHS 参数可通过 `solver.highs_extra` 透传。

## 日内滚动、实时 SCED 与偏差结算

### 滚动时序（推荐默认值，均可在 `modes.yaml` 的 `intraday` / `realtime` 下修改）

| 环节 | 默认 | 依据 |
|---|---|---|
| 日内触发 | 每 1 h（00:00、01:00…，`interval_h`） | 假设值。川V4.0 8.1 为“边界变化时触发”，没有固定周期 |
| 日内窗口 | 触发时刻 → 当日 24:00（`window: day_end`） | 川V4.0 8.1、8.3.1 |
| 日内粒度 | 15 min | 川V4.0 8.2.5（96 点） |
| 日内锁定 | 燃煤、CCGT 启停 = 日前；GT、水电（开机台数）、储能（0-1 充放状态）重新决策 | 川V4.0 8.2.4；基本规则第五十四条 |
| 日内价格 | 只输出参考价，不结算 | 川V4.0 4.1、8.1 |
| 实时周期 | 15 min | 川V4.0 9.1；甘V3.2 第六十三条 |
| 实时前瞻 | 2 h（8 个时段），只执行首时段 | 甘V3.2 第六十三条（未来 15 min–2 h）；川写“未来 M 小时”，未给 M |
| 实时首时段 | 用实际负荷/风光；后续时段用超短期预测 | 假设做法 |

**预测**：每个时段的预测随提前量收敛（`forecast_view`）：提前量 ≥ 16 个时段用日前预测；1–16 个时段在日前预测和超短期预测之间线性过渡；当前时段可用实际值。

**状态衔接**：每次实时执行后推进系统状态：火电在线/离线时长、上一时段出力（爬坡）、水库库容和已用日电量、储能 SOC。下一轮日内或实时以**实际执行后的状态**为初值。日内/实时窗口不到日末时，末 SOC、末库容取最近计划值作**软目标**（偏离罚 `terminal_penalty`，假设值）；日前仍是硬约束。

**实时中储能的处理**（`realtime.storage_state`）：
- `redecide`（默认）：每个实时时段先解一个 2 h 窗口的小 MIP，重新决定储能充放 0-1 状态（其他整数沿用日内计划），再固定这些整数解 LP，用对偶求实时 LMP。储能因此可以随实时价格调整充放方向，价格仍来自 LP。
- `fixed`：直接沿用最近一轮日内计划的充放状态，只解 LP。
燃煤/CCGT 启停固定为日前（= 日内）；GT 启停、水电开机台数沿用最近一轮日内计划。

**实时价格**：带切负荷、弃电/过剩松弛。出现松弛时改用 `pricing_penalties` 重算价格；物理量取调度罚因子下的解。LMP 最后截断到 [clear_min, clear_max]。

### 偏差结算（两部制）

对每个主体、每个 15 min：`金额 = Q_DA × π_DA × dt + (Q_RT − Q_DA) × π_RT × dt`，其中 q > 0 为注入（收入），q < 0 为用电（支出）。
- 燃煤、燃气（CCGT/GT）、水电、风、光、储能放电、储能充电、负荷、联络线分别计算；储能充电和放电分开列示。
- 偏差损益 `dev_pnl = ΔQ × (π_RT − π_DA) × dt`，即偏差相对于全按日前价结算多赚或少赚的部分。
- 对账：市场盈余 = −Σ 各方金额 = 阻塞盈余（Σ μ·F）+ 限价截断修正，日前和实时偏差两部分分别对平（残差约 1e-9 元）。

### 全流程输出（outputs/full_{date}_MKT_{season}_v{n}/）

| 文件 | 内容 |
|---|---|
| da_unit_awards / da_nodal_prices / da_storage .csv | 日前结果 |
| id_rounds_summary.csv | 每轮日内：窗口、用时、gap、松弛、首小时参考价 |
| id_plans.csv / id_prices_indicative.csv / id_storage.csv | 每轮日内各机组计划、参考 LMP、储能计划 |
| rt_units / rt_prices / rt_lines / rt_storage / rt_system .csv、rt_lmp_matrix.csv | 实时执行出力、LMP（含分解）、潮流、SOC、切负荷/弃电 |
| settlement_detail / by_entity / by_type .csv | 结算明细、按主体、按类型（日前金额、实时偏差金额、偏差损益、偏差电量） |
| settlement_reconciliation.csv / settlement_line_rent.csv | 结算对账、各线路阻塞盈余 |
| summary.json | 价差、结算、预测误差影响、对账、校核、用时 |
| lmp_da_vs_rt.png / rolling_plans.png / settlement_bars.png | 日前 vs 实时 LMP；各轮日内计划与实时执行；结算柱状图 |

### 一次全流程结果（2026-07-15 丰水期，MKT v1，2026-11-01 运行）

- 用时 354 s：日前 115 s，日内 24 轮 187 s（早轮 12–19 s，后轮 < 3 s），实时 96 时段 52 s。
- 负荷加权均价：日前 291.6、实时 332.4 元/MWh，平均价差 +41.1（节点 |RT−DA| 最大 604.5）。
- 预测误差（负荷 + 风光）导致的偏差电量 4,557 MWh，实时偏差结算 −66.0 万元，偏差损益 −25.6 万元。风、光偏差损益分别为 −11.9 万和 −8.2 万元。
- 校核全部通过：SOC 连续、锁定启停 0 偏差、实时功率平衡误差 1e-11 MW、对账残差 1e-9 元。
- 已知现象：21:30 以后实时价格冲到 700–800 元（截断上限）。原因是实时窗口在日末收缩，储能末 SOC 软目标（罚 1000 元/MWh，假设值）要求储能在最后几个时段充电，而此时煤电、水电已接近上限，能量价 λ 被储能末 SOC 罚因子抬到 1,100–1,460 元，节点价被截断到 800 元。这是日末边界效应，可调低 `terminal_penalty` 或按次日计划给末 SOC 解决。

## 日前输出（outputs/da_{date}_MKT_{season}_v{n}/）

| 文件 | 内容 |
|---|---|
| da_unit_awards.csv | 各机组 96 点中标出力、启停、备用、可用/弃电、节点电价、收入 |
| da_nodal_prices.csv / da_lmp_matrix.csv | 节点电价（长表：LMP/能量/阻塞分量；矩阵：母线×时段） |
| da_line_flows.csv | 潮流、负载率、μ⁺/μ⁻、是否阻塞 |
| da_storage.csv / da_hydro.csv / da_system.csv | 储能充放与 SOC；水电出力、库容、开机台数；系统负荷、备用和价格 |
| forecast_accuracy.csv | 预测与实际偏差统计 |
| summary.json | 关键指标 |
| lmp_heatmap / lmp_curves / generation_stack / storage_soc / line_loading / forecast_vs_actual .png | 图 |

改报价对比位于 `outputs/rebid_v1_vs_v2/`，包括 lmp_compare.csv、award_compare(_daily).csv、rebid_compare.png 和 summary.json。

## 测试

- `test_bids.py`：只有统一规则 MKT；默认报价合规；违规（非严格递增、超限价、非 10 元步长、段长不足、相邻价差 >150、储能放电价 ≤ 充电价）能被拦截；PTDF 参考列为 0；`storage_binary` 默认开启。
- `test_da.py`：功率平衡、注入和为 0、线路不越限；LMP 分解一致、参考母线 LMP = λ、阻塞对偶非负且满足互补松弛；出现阻塞和节点价差；**有限差分检验**（固定整数的 SCED 中把节点负荷改动 ±0.5 MW，目标函数增量 / dt 与 LMP 一致）；储能 SOC、充放 0-1 状态（SCED 中已固定、取值 0/1、u_ch+u_dis≤1、P≤M·u），水电水量/电量/振动区、备用充足、火电出力/爬坡。
- `test_rolling.py`（日内每 1 h、滚动 8 个时段）：预测随提前量收敛；SOC 连续且满足方程、日内初值 = 实际 SOC；锁定启停与日前一致、实时启停 = 日内计划、最小开停机；实时功率平衡、注入和为 0、LMP 分解；结算每时段电量平衡、联络线无偏差、储能充放符号、对账残差 ≈ 0。

## 主要假设与简化（假设值）

1. `load_scale = 0.68`：0.8 时正备用缺约 495 MW，因此调低。
2. N-1 备用 = max(配置的 N-1 损失, 最大单机) = 865 MW（C3）；ρ_L、ρ_RE 等系数为假设值。
3. 段长下限（每段 ≥ 装机 10%）和相邻价差上限对火电首段（最小技术出力段）豁免，这是参考实践的假设。
4. 水电按电站聚合（每个出力区的开机台数为整数），不做单机组合。
5. 联络线为固定计划；负荷不报价；未考虑网损。
6. 罚因子：按甘肃工作方案稿的量级取值；备用罚、平衡定价罚为假设值。
7. MIP gap 0.2%（`mip_rel_gap: 0.002`，`time_limit_s: 300`）：启停解为近优，不同 gap 下价格可能略有差异（非凸）。改报价对比同样受此影响。

8. 日内每 1 h 定时触发、实时首时段用实际值、日内/实时末状态软目标罚 1000、结算统一 15 min：均为假设值。
9. 日内只输出参考价，不结算；实时按 15 min 结算（川规则中发电和用户按小时结算、独立储能按 15 min 结算，此处统一为 15 min）。
10. 储能、新能源偏差不另做考核；仅按实时价结算偏差。

## 下一阶段（可选）

偏差考核/免考核带、用户侧报量报价、5 min 实时、随机/鲁棒备用、多日滚动（末 SOC 衔接次日）。
