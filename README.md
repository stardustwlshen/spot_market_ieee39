# IEEE39 电力现货市场出清模型（M1 + 日前）

参考四川、甘肃现货规则，在改造的 IEEE 39 节点系统上实现**日前现货 SCUC/SCED 节点电价出清**，支持报价版本管理和改报价。
本阶段完成 **M1（数据与报价）+ 日前出清**。日内滚动、实时出清和偏差结算放在下一阶段。

> 带“假设值”标记的参数均不是规则原文中的数值，只用于让算例可解、有代表性。规则来源和条款见方案页第 2、9 节。

## 快速开始

```bash
cd /workspace/spot_market_ieee39
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt   # 已建好可跳过
. .venv/bin/activate

python scripts/main_da.py                      # 四川口径、丰水期、最新报价版本日前出清（约 2 min）
python scripts/main_da.py --bid-version 1      # 指定报价版本
python scripts/main_da.py --rule-set GS        # 甘肃口径
python scripts/main_da.py --season dry --time-limit 120 --no-plots
python scripts/demo_rebid.py                   # 改报价演示：违规报价被拒 → 修正提交 v(n+1) → 改前/改后对比（约 3 min）
python scripts/make_default_bids.py            # 重新生成默认报价 v1
python -m pytest -q tests/                     # 14 项测试（约 100 s）
python scripts/bench_scuc.py SC plain tight ws hourly   # SCUC 求解参数/提速措施对比（逐个顺序运行）
```

## 目录结构

```
config/base.yaml     运行参数：日期、季节、规则集、负荷缩放、线路限额压缩、求解器
config/units.yaml    机组参数：燃煤×3、燃气 CCGT/GT、风×2、光伏、水电站（4 台，G5@34）、储能×3、区外联络线
config/modes.yaml    省规则集：报价/出清限价、分段规则、启动状态划分、备用口径、罚因子
data/case39/         IEEE39 母线/支路（scripts/export_case39.py 由 pandapower 导出）
data/profiles/       96 点负荷/风/光预测与“实际”曲线（固定随机种子）
bids/{date}/{SC|GS}/bids_v{n}.json|csv   版本化报价（JSON 为准，CSV 便于查看）
src/smm/
  config.py   network.py(PTDF)   forecast.py   bids.py(报价生成/校验/存取)
  case.py(日前算例、备用需求)   scuc.py(Pyomo MILP/LP、对偶取价)   da.py(日前流程)
  results.py   plots.py   pipeline.py
scripts/  main_da.py  demo_rebid.py  make_default_bids.py  export_case39.py  bench_scuc.py
tests/    test_bids.py（快速）  test_da.py（一次日前出清共享）
outputs/  每次运行的结果（不入库）
```

## 模型要点

**网络**：直流潮流 PTDF，参考母线 31，46 条支路（变压器视为支路）。部分线路限额按 `line_scale` 压缩以制造阻塞（假设值）。
节点注入 `inj[n,t]` 由节点平衡等式定义，系统平衡为 `Σ inj = 0`，线路为 `−F̄ ≤ PTDF·inj ≤ F̄`（带罚松弛）。

**SCUC（MILP，HiGHS）**
- 火电（燃煤、燃气）：分段报价、启停 0-1 变量、最小开停机时间（含初始状态）、爬坡（启停时为 Pmin）、热/温/冷启动费（四川 8/72 h，甘肃 1/10/72 h）。燃气同样参与报价。
- 新能源：报量报价，出力 ≤ 预测，可弃电。
- 水电：电站级分段报价、聚合机组（每个出力区的开机台数为整数变量，避开振动区）、水量平衡、生态流量、末库容、日电量上限、弃水罚。
- 储能：充放分段报价、SOC 动态、效率、末 SOC 约束；充放状态始终用 0-1 变量 `u_ch`、`u_dis`：`u_ch + u_dis ≤ 1`，`Pc ≤ M_c·u_ch`、`Pd ≤ M_d·u_dis`，且每个报价段 `pc[k] ≤ Lc[k]·u_ch`、`pd[k] ≤ Ld[k]·u_dis`。big-M 取紧值：M = min(额定功率, 各段之和, 一个时段内 SOC 可用区间对应的功率)。`solver.storage_binary` 默认 true，false 只用于与旧松弛做法对比。
- 区外联络线：固定计划（价格接受者）。
- 备用（工程化处理预测误差）：正备用 = 最大单机/N-1 + ρ_L·负荷 + ρ_RE·新能源预测，负备用同理；提供者和系数按规则集（假设值见 modes.yaml）。
- 罚：失负荷/弃负荷、线路越限、备用不足。

**SCED 与节点电价**：固定 SCUC 的整数解（火电启停 u/v/w、水电出力区、储能充放状态 u_ch/u_dis）后把模型改成 LP 重解，读取对偶：
- 节点电价 π = 节点平衡约束对偶 / dt；能量价 λ = 系统平衡对偶 / dt；
- 阻塞分量：μ⁺ = −dual(上限)/dt，μ⁻ = −dual(下限)/dt；
- 分解 π = λ − PTDFᵀ(μ⁺ − μ⁻)，每次运行都会核对（误差约 1e-12）；
- 出现松弛时，会改用节点电价计算罚因子（`pricing_penalties`）重算价格，最后按出清限价截断。

## 求解提速（SCUC，四川口径 v1，HiGHS 1.15.1，逐个顺序运行）

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

甘肃口径默认配置 SCUC 31 s（gap 0.185%）。需要更快时，可在 `config/base.yaml` 调 `time_limit_s`、`mip_rel_gap`，或打开 `commit_step_periods: 4`；其他 HiGHS 参数可通过 `solver.highs_extra` 透传。

## 输出（outputs/da_{date}_{规则}_{季节}_v{n}/）

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

- `test_bids.py`：默认报价合规；四川（单调、限价、储能放电价>充电价、启动费上限）与甘肃（10 元步长）违规能被拦截；PTDF 参考列为 0；`storage_binary` 默认开启。
- `test_da.py`：功率平衡、注入和为 0、线路不越限；LMP 分解一致、参考母线 LMP = λ、阻塞对偶非负且满足互补松弛；出现阻塞和节点价差；**有限差分检验**（固定整数的 SCED 中把节点负荷改动 ±0.5 MW，目标函数增量 / dt 与 LMP 一致）；储能 SOC、充放 0-1 状态（SCED 中已固定、取值 0/1、u_ch+u_dis≤1、P≤M·u），水电水量/电量/振动区、备用充足、火电出力/爬坡。

## 主要假设与简化（假设值）

1. `load_scale = 0.68`：0.8 时四川口径正备用缺约 495 MW，因此调低。
2. N-1 备用 = max(配置的 N-1 损失, 最大单机) = 865 MW（C3）；ρ_L、ρ_RE 等系数为假设值。
3. 四川段长 A1/A2 校验对所有火电（含燃气）的首段（最小技术出力段）豁免，这是参考实践的假设。
4. 水电按电站聚合（每个出力区的开机台数为整数），不做单机组合。
5. 联络线为固定计划；负荷不报价；未考虑网损。
6. 罚因子：四川附件未给数值，按甘肃工作方案稿的量级自定。
7. MIP gap 0.2%（`mip_rel_gap: 0.002`，`time_limit_s: 300`）：启停解为近优，不同 gap 下价格可能略有差异（非凸）。改报价对比同样受此影响。

## 下一阶段

日内滚动出清（以日前启停为基础，滚动更新预测，储能 SOC 衔接）、实时 SCED（15/5 min）、日前/实时两部制偏差结算（含储能、新能源偏差考核口径）、甘肃用户侧报价、随机/鲁棒备用（可选）。
