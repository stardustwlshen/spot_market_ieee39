# IEEE39 电力现货市场出清模型（M1 + 日前）

参考四川、甘肃现货规则，在改造的 IEEE 39 节点系统上实现**日前现货 SCUC/SCED 节点电价出清**，支持报价版本管理和改报价。
本阶段完成 **M1（数据与报价）+ 日前出清**。日内滚动、实时出清和偏差结算放在下一阶段。

> 带“假设值”标记的参数均不是规则原文中的数值，只用于让算例可解、有代表性。规则来源和条款见方案页第 2、9 节。

## 快速开始

```bash
cd /workspace/spot_market_ieee39
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt   # 已建好可跳过
. .venv/bin/activate

python scripts/main_da.py                      # 四川口径、丰水期、最新报价版本日前出清（约 90 s）
python scripts/main_da.py --bid-version 1      # 指定报价版本
python scripts/main_da.py --rule-set GS        # 甘肃口径
python scripts/main_da.py --season dry --time-limit 120 --no-plots
python scripts/demo_rebid.py                   # 改报价演示：违规报价被拒 → 修正提交 v(n+1) → 改前/改后对比（约 3 min）
python scripts/make_default_bids.py            # 重新生成默认报价 v1
python -m pytest -q tests/                     # 12 项测试（约 90 s）
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
scripts/  main_da.py  demo_rebid.py  make_default_bids.py  export_case39.py
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
- 储能：充放分段报价、SOC 动态、效率、末 SOC 约束；充放互斥默认松弛，求解后检查，出现同时充放会自动改用 0-1 重解。
- 区外联络线：固定计划（价格接受者）。
- 备用（工程化处理预测误差）：正备用 = 最大单机/N-1 + ρ_L·负荷 + ρ_RE·新能源预测，负备用同理；提供者和系数按规则集（假设值见 modes.yaml）。
- 罚：失负荷/弃负荷、线路越限、备用不足。

**SCED 与节点电价**：固定 SCUC 的整数解后把模型改成 LP 重解，读取对偶：
- 节点电价 π = 节点平衡约束对偶 / dt；能量价 λ = 系统平衡对偶 / dt；
- 阻塞分量：μ⁺ = −dual(上限)/dt，μ⁻ = −dual(下限)/dt；
- 分解 π = λ − PTDFᵀ(μ⁺ − μ⁻)，每次运行都会核对（误差约 1e-12）；
- 出现松弛时，会改用节点电价计算罚因子（`pricing_penalties`）重算价格，最后按出清限价截断。

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

- `test_bids.py`：默认报价合规；四川（单调、限价、储能放电价>充电价、启动费上限）与甘肃（10 元步长）违规能被拦截；PTDF 参考列为 0。
- `test_da.py`：功率平衡、注入和为 0、线路不越限；LMP 分解一致、参考母线 LMP = λ、阻塞对偶非负且满足互补松弛；出现阻塞和节点价差；**有限差分检验**（固定整数的 SCED 中把节点负荷改动 ±0.5 MW，目标函数增量 / dt 与 LMP 一致）；储能 SOC/互斥、水电水量/电量/振动区、备用充足、火电出力/爬坡。

## 主要假设与简化（假设值）

1. `load_scale = 0.68`：0.8 时四川口径正备用缺约 495 MW，因此调低。
2. N-1 备用 = max(配置的 N-1 损失, 最大单机) = 865 MW（C3）；ρ_L、ρ_RE 等系数为假设值。
3. 四川段长 A1/A2 校验对所有火电（含燃气）的首段（最小技术出力段）豁免，这是参考实践的假设。
4. 水电按电站聚合（每个出力区的开机台数为整数），不做单机组合；储能互斥默认松弛并做事后校验。
5. 联络线为固定计划；负荷不报价；未考虑网损。
6. 罚因子：四川附件未给数值，按甘肃工作方案稿的量级自定。
7. MIP gap 0.2%：启停解为近优，不同 gap 下价格可能略有差异（非凸）。改报价对比同样受此影响。

## 下一阶段

日内滚动出清（以日前启停为基础，滚动更新预测，储能 SOC 衔接）、实时 SCED（15/5 min）、日前/实时两部制偏差结算（含储能、新能源偏差考核口径）、甘肃用户侧报价、随机/鲁棒备用（可选）。
