# IEEE39 电力现货市场出清模型（日前 → 实时滚动 SCED → 偏差结算）

在改造的 IEEE 39 节点系统上实现完整的现货流程：**日前 SCUC/SCED 节点电价出清 → 实时 15 min 滚动 SCED（前瞻 2 h，甘肃模式；独立日内可选）→ 日前/实时两部制偏差结算**，支持报价版本管理和改报价。

**规则**：只有一套自有统一规则 `MKT`（“自有统一现货市场规则（参考川、甘）”，`config/modes.yaml`）。每个参数取四川 V4.0 / 甘肃 V3.2 中合理的一边，并在行内注明出处；规则原文没有给出数值的标为“假设值”。所有参数都可配置，流水线只运行这一套规则。

> 带“假设值”标记的参数均不是规则原文中的数值，只用于让算例可解、有代表性。规则来源和条款见方案页。

## 快速开始

```bash
cd /workspace/spot_market_ieee39
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt   # 已建好可跳过
. .venv/bin/activate

python scripts/main_full.py                    # 一键全流程：日前→实时(15min 滚动,前瞻2h)→结算，最新报价版本（约 3 min）
python scripts/main_full.py --bid-version 1    # 复现下文“一次全流程结果”（v2 为 demo_rebid 生成的改报价版本）
python scripts/main_full.py --periods 8       # 只滚动前 8 个时段（快速检查，约 2 min）
python scripts/main_full.py --bid-version 1 --anchor window_end_pct --tag _b1        # 储能 B1（可选）：甘肃窗口末 ±x
python scripts/main_full.py --bid-version 1 --storage-mode fixed_da --tag _fixed_da  # 储能 A：执行日前曲线
python scripts/main_full.py --anchor window_end_pct --anchor-x 0.1 --rt-lookahead-h 1 --intraday --no-plots # B1 改 x、前瞻；打开独立日内做对比
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
  rolling.py(实时滚动 SCED、储能锚点、可选日内，系统状态推进)   settlement.py(两部制偏差结算、对账)   full.py(全流程、校核、输出)
scripts/  main_full.py  main_da.py  demo_rebid.py  make_default_bids.py  export_case39.py  bench_scuc.py
tests/    test_bids.py（快速）  test_da.py（一次日前出清共享）  test_rolling.py（缩短滚动 6–8 时段）
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

## 滚动出清（甘肃模式）、实时 SCED 与偏差结算

### 滚动时序（默认值，可在 `modes.yaml` 的 `intraday` / `realtime` 下修改）

| 环节 | 默认 | 依据 |
|---|---|---|
| 独立日内市场 | **关闭**（`intraday.enabled: false`，`--intraday` 可打开做对比，代码保留川式事件触发日内） | 甘肃模式：不设独立日内市场 |
| 实时周期 | 15 min | 川V4.0 9.1；甘V3.2 第六十三条 |
| 实时前瞻 | 2 h（T~T+120，8 个时段），只执行首时段 | 甘V3.2 第六十三条（未来 15 min–2 h） |
| 边界时序 | T−30 读取边界；模型中首时段直接用实际负荷/风光（T−30 到 T 的残差视为由 AGC 处理） | 时序参考甘V3.2 第六十三条；“首时段用实际值”为假设做法 |
| 启停 | 燃煤、CCGT 启停 = 日前（锁定）；燃气 GT 在每个 2 h 窗口 MIP 内自由启停 | 甘V3.2 第十八条（煤电启停日前确定）；GT 自由为假设做法（参考基本规则第五十四条“快速启停资源”） |
| 水电 | **执行日前出清曲线**（开机台数和出力都固定为日前值，`hydro: fixed_da`），实时不再优化，也不设软惩罚 | 甘V3.2 第六十条(四) |
| 储能 | 见下文模式 B2（默认，已确认）：整点 SOC = 日前 | 广东式做法（条款号查不到）；B1 甘肃式可选（甘V3.2 运营细则 附录5 第9项） |

**预测**：每个时段的预测随提前量收敛（`forecast_view`）：提前量 ≥ 16 个时段用日前预测；1–16 个时段在日前预测和超短期预测之间线性过渡；当前时段用实际值。

**状态衔接**：每个实时时段执行后推进系统状态：火电在线/离线时长、上一时段出力（爬坡）、水库库容和已用日电量、储能 SOC。下一时段以**实际执行后的状态**为初值。日前的日末 SOC 按申报值作硬约束；实时窗口不再设末状态软目标（旧的 `terminal_penalty` 已删除）。

**储能在实时中的三种模式**（`realtime.storage`）：

| 模式 | 配置 | 做法 | 依据 |
|---|---|---|---|
| **B2（默认，用户已确认）** | `mode: sced_anchor`，`anchor: hourly_eq` | 储能沿用日前封存报价进入实时 SCED，充放 0-1 状态在窗口 MIP 内决定，初值取实际 SOC。窗口内每个整点（第 4、8… 个 15 min 末）`E_RT_{4n} = E_DA_{4n}`，为硬约束；整点之间可自由偏离 | 参考广东整点 SOC 相等的做法（条款号查不到）。选用原因：B1 在日末出现 800 元尖峰（见下文对比），B2 无 |
| B1（可选） | `anchor: window_end_pct`，`x: 0.05`（`--anchor window_end_pct`） | 同 B2，但只约束窗口末时段：`max(Emin,(1−x)E_DA) ≤ E_RT ≤ min(Emax,(1+x)E_DA)`，为硬约束 | 甘V3.2 运营细则 附录5 第9项原文：“独立储能实时市场各优化周期末时段的荷电状态跟随日前市场优化结果，并在满足荷电状态上下限约束的前提下允许上下浮动一定比例”。比例原文未给，**x = 0.05 为假设值** |
| A（可选） | `mode: fixed_da`（`--storage-mode fixed_da`） | 储能执行日前充放电曲线，作为价格接受者 | 对比用 |

锚点带松弛 `sA_up/sA_dn`，罚 `anchor_penalty = 1e5 元/MWh`（假设值），只用于防止无解。一旦触发，就把锚点边界放宽到实际 E、松弛固定为 0 后重解 LP，因此该罚**不进入电价**。`summary.json` 的 `realtime.anchor_relaxed_periods` 记录触发次数。

**实时价格**：带切负荷、弃电/过剩松弛。出现调度松弛时，改用 `pricing_penalties` 重算价格（锚点松弛不算在内）；物理量取调度罚因子下的解。LMP 最后截断到 [clear_min, clear_max]。

### 偏差结算（两部制）

对每个主体、每个 15 min：`金额 = Q_DA × π_DA × dt + (Q_RT − Q_DA) × π_RT × dt`，其中 q > 0 为注入（收入），q < 0 为用电（支出）。
- 燃煤、燃气（CCGT/GT）、水电、风、光、储能放电、储能充电、负荷、联络线分别计算；储能充电和放电分开列示。
- 偏差损益 `dev_pnl = ΔQ × (π_RT − π_DA) × dt`，即偏差相对于全按日前价结算多赚或少赚的部分。
- 对账：市场盈余 = −Σ 各方金额 = 阻塞盈余（Σ μ·F）+ 限价截断修正，日前和实时偏差两部分分别对平（残差约 1e-9 元）。

### 全流程输出（outputs/full_{date}_MKT_{season}_v{n}/）

| 文件 | 内容 |
|---|---|
| da_unit_awards / da_nodal_prices / da_storage .csv | 日前结果 |
| id_rounds_summary.csv | 每轮日内：窗口、用时、gap、松弛、首小时参考价（默认无日内，仅表头） |
| id_plans.csv / id_prices_indicative.csv / id_storage.csv | 每轮日内各机组计划、参考 LMP、储能计划 |
| rt_units / rt_prices / rt_lines / rt_storage / rt_system .csv、rt_lmp_matrix.csv | 实时执行出力、LMP（含分解）、潮流、SOC、切负荷/弃电、锚点是否松弛（rt_system.anchor_relaxed） |
| settlement_detail / by_entity / by_type .csv | 结算明细、按主体、按类型（日前金额、实时偏差金额、偏差损益、偏差电量） |
| settlement_reconciliation.csv / settlement_line_rent.csv | 结算对账、各线路阻塞盈余 |
| summary.json | 价差、结算、预测误差影响、对账、校核、用时 |
| lmp_da_vs_rt.png / rolling_plans.png / settlement_bars.png | 日前 vs 实时 LMP；实时执行 vs 日前计划（B1 时 SOC 面板含 ±x 锚点带）；结算柱状图 |

### 一次全流程结果（2026-07-15 丰水期，MKT v1，`--bid-version 1`，2026-11-01 运行）

默认 B2（outputs/full_2026-07-15_MKT_wet_v1/，2026-11-01 02:33 PST 完成）：
- 用时 176 s：日前 119 s，日内 0（关闭），实时 96 时段 56 s。旧方案（川式每小时日内 + 软目标）为 354 s。
- 负荷加权均价：日前 291.6、实时 277.3 元/MWh。逐小时价差（RT−DA）的平均值为 −18.1，绝对值平均为 53.9，范围 −165.7 ~ +84.3；节点 |RT−DA| 最大 310.0，RMS 90.2。
- 实时节点价最大 519.6，**没有 ≥700 元的时段**。21–24 时负荷加权均价：日前 321.1、实时 277.9（逐小时 284.7 / 269.0 / 265.8 / 300.1）。
- 锚点松弛触发 0 次（最大 0 MWh）；调度松弛重算价格 0 次。校核全部通过：水电与日前偏差 0 MW，锁定启停 0 偏差，功率平衡误差 1e-12 MW，SOC 方程误差 3e-14，对账残差 1e-9 元。
- 预测误差（负荷 + 风光）导致的偏差电量 4,752 MWh，实时偏差结算 −56.8 万元，偏差损益 −15.8 万元（风 −8.36、光 −5.57、负荷 −1.87）；燃煤 +4.33、GT −4.33；储能充/放 +0.33 / +1.03。日前市场盈余 402.19 万元，全部为阻塞盈余。

B1（甘肃窗口末 ±5%）的日末问题：时段 86（21:30）和 93（23:15）节点价被截断到 800，能量价 λ 分别为 1,000 和 2,253。原因是硬锚点约束紧：实时中储能在 21:30–23:00 放电比日前更深，日末锚点进入窗口后，最后几个时段必须充回 ≥0.95·E_DA，而这时供给已经很紧。因此默认改为 B2。

三种储能模式对比（同一日前结果，同一份实际值）：

| 模式 | 实时均价 | 21–24 时实时均价（日前 321.1） | 实时 LMP 最大 | ≥700 的时段 | 储能偏差电量 充/放 (MWh) | 储能偏差损益 充/放（万元） | 预测误差偏差损益（万元） | 用时 |
|---|---|---|---|---|---|---|---|---|
| **B2 整点相等（默认）** | 277.3 | 277.9 | 519.6 | 无 | +1.3 / −1.2 | +0.33 / +1.03 | −15.8 | 176 s |
| B1 甘肃窗口末 ±5% | 291.6 | 407.9 | 800 | 86、93 | −34.8 / +40.9 | +0.86 / +2.16 | −20.9 | 169 s |
| A 执行日前曲线 | 278.6 | 260.9 | 800 | 40（10:00，λ=840） | 0 / 0 | 0 / 0 | −20.2 | 163 s |

> B1 的日末尖峰来自锚点约束本身（x、前瞻长度、日末 SOC 申报值），不是数值问题。B1 结果目录：outputs/full_2026-07-15_MKT_wet_v1_b1/；A：outputs/full_2026-07-15_MKT_wet_v1_fixed_da/。

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
- `test_rolling.py`（默认 B2 滚动 8 个时段；B1、A 各滚动 6 个时段）：预测随提前量收敛；SOC 连续且满足方程；燃煤/CCGT 启停与日前一致、GT 可自由启停、水电出力 = 日前；B2（默认）整点 SOC = 日前且锚点未松弛；B1 窗口末 SOC 在 (1±x)E_DA 内；A 储能出力 = 日前；从日前状态起算的日末时段（t=92）不出现末状态罚导致的尖峰；实时功率平衡、注入和为 0、LMP 分解；结算每时段电量平衡、联络线无偏差、储能充放符号、对账残差 ≈ 0。

## 主要假设与简化（假设值）

1. `load_scale = 0.68`：0.8 时正备用缺约 495 MW，因此调低。
2. N-1 备用 = max(配置的 N-1 损失, 最大单机) = 865 MW（C3）；ρ_L、ρ_RE 等系数为假设值。
3. 段长下限（每段 ≥ 装机 10%）和相邻价差上限对火电首段（最小技术出力段）豁免，这是参考实践的假设。
4. 水电按电站聚合（每个出力区的开机台数为整数），不做单机组合。
5. 联络线为固定计划；负荷不报价；未考虑网损。
6. 罚因子：按甘肃工作方案稿的量级取值；备用罚、平衡定价罚为假设值。
7. MIP gap 0.2%（`mip_rel_gap: 0.002`，`time_limit_s: 300`）：启停解为近优，不同 gap 下价格可能略有差异（非凸）。改报价对比同样受此影响。

8. 实时首时段用实际值、GT 实时自由启停、B1 的锚点浮动比例 x = 0.05（默认 B2 不用 x）、锚点松弛罚 1e5、结算统一 15 min：均为假设值。
9. 日内只输出参考价，不结算；实时按 15 min 结算（川规则中发电和用户按小时结算、独立储能按 15 min 结算，此处统一为 15 min）。
10. 储能、新能源偏差不另做考核；仅按实时价结算偏差。

## 下一阶段（可选）

偏差考核/免考核带、用户侧报量报价、5 min 实时、随机/鲁棒备用、多日滚动（末 SOC 衔接次日）。
