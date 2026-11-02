# IEEE39 电力现货市场出清仿真

在改造的 IEEE 39 节点系统上模拟一个完整的电力现货市场：**日前 SCUC/SCED 出清节点电价 → 实时 15 分钟滚动 SCED → 日前/实时两级偏差结算**。可以改报价、改边界条件，在网页里运行和看结果。

- 完整的建模方案、公式和规则出处见 [docs/仿真方案.md](docs/仿真方案.md)
- 开发过程、参数依据、求解提速试验和对比算例数据见 [docs/开发记录.md](docs/开发记录.md)

> 规则参数参考四川（川V4.0）、甘肃（甘V3.2）现货规则，统一成一套自有规则 `MKT`，逐项在 `config/modes.yaml` 注明出处。标“假设值”的数值不是规则原文给出的，只用于让算例可解、有代表性。

## 功能

| 功能 | 说明 |
|---|---|
| 日前 SCUC / SCED | 96 点（15 min）MILP 机组组合：火电启停、储能充放 0-1、水电开机台数、抽蓄工况；固定整数后 LP 重解，取对偶得到节点电价 |
| 节点电价 LMP | 直流潮流 PTDF，LMP = 能量价 λ + 阻塞分量，每次运行自动核对分解；限价 [0, 800] 元/MWh |
| 实时滚动 | 每 15 min 出清未来 2 h、只执行首时段（甘肃模式）；燃煤/CCGT 启停锁定日前，燃气 GT 可启停；默认不设独立日内市场（`--intraday` 可开） |
| 多类资源 | 燃煤×3、燃气（CCGT、GT）、风×2、光伏、水电（库容型/径流式）、储能×3、抽水蓄能 PS1、区外联络线 |
| 储能 / 抽蓄 | 分段报价、SOC/库容动态、期末申报；实时三种模式：B2 整点锚定（默认）/ B1 窗口末 ±x / A 执行日前；抽蓄规则与储能相同 |
| 预测误差 | 日前、超短期预测按 AR(1) 误差生成，随提前量收敛；实时首时段用实际值 |
| 两级结算 | 日前量 × 日前价 + 偏差量 × 实时价，按主体、类型输出；对账（阻塞盈余）残差约 1e-9 元 |
| 报价管理 | 报价按版本保存（v1、v2…），提交前按规则校验（段数、递增、步长、限价等），违规不能保存 |
| 网页应用 | Streamlit 中文界面：场景管理、边界条件编辑、后台运行、结果图表、场景对比 |

## 目录结构

```
config/            参数配置
  base.yaml          运行参数：日期、季节、负荷缩放、线路压缩、求解器
  units.yaml         机组参数：火电、联络线、风光、水电、抽蓄、储能
  modes.yaml         市场规则 MKT：限价、报价分段、备用、罚因子、日内/实时、抽蓄规则
data/
  case39/            IEEE39 母线、支路（由 scripts/export_case39.py 从 pandapower 导出）
  profiles/          96 点负荷、风、光的日前预测与“实际”曲线
bids/{date}/MKT/   报价版本 bids_v{n}.json（以 JSON 为准）与 bids_v{n}.csv（便于查看）
src/smm/           模型代码
  network.py         PTDF                  forecast.py    预测与误差
  bids.py            报价生成/校验/存取     case.py        日前算例、备用需求
  scuc.py            Pyomo MILP/LP、对偶取价 da.py          日前流程
  rolling.py         实时滚动 SCED、储能锚点 settlement.py 两级结算、对账
  full.py            全流程、校核、输出      scenario.py    场景与运行目录管理
scripts/           命令行入口：main_full.py、main_da.py、run_scenario.py、run_app.sh 等
app/               网页应用：主页.py、pages/1_边界条件.py、2_运行.py、3_结果.py、4_场景对比.py
tests/             pytest 测试
docs/              仿真方案.md、开发记录.md、images/
outputs/  runs/  scenarios/   运行结果与本地场景（自动生成，不入库）
```

## 环境与安装

- Python **3.11 及以上**（开发和测试环境为 Python 3.13.5，Linux）
- 求解器 HiGHS 通过 `highspy` 包安装，不需要另装求解器
- 建议 4 核以上 CPU、4 GB 以上内存；一次全天全流程约 10–12 分钟（主要是含抽蓄的日前 SCUC，按 600 s 时限求解）

```bash
git clone https://github.com/stardustwlshen/spot_market_ieee39.git
cd spot_market_ieee39
python3 -m venv .venv
. .venv/bin/activate              # Windows：.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` 中的版本是开发环境的实测版本（pyomo 6.10.1、highspy 1.15.1、streamlit 1.66.1、plotly 7.1.0 等）。

## 快速开始

### 方式一：网页应用

```bash
bash scripts/run_app.sh            # 后台启动，默认只监听 127.0.0.1:8501
```

然后在浏览器打开 <http://localhost:8501>。

```bash
bash scripts/run_app.sh restart    # 改代码后重启
bash scripts/run_app.sh stop       # 停止
PORT=8600 bash scripts/run_app.sh  # 换端口
```

- 进程号在 `outputs/app.pid`，日志在 `outputs/app.log`；健康检查 `curl http://127.0.0.1:8501/_stcore/health`。
- `run_app.sh` 需要先建好 `.venv`（见上节），并使用 `curl` 等待启动完成。
- 脚本默认设置 `TZ=America/Los_Angeles`，运行目录名和日志时间按该时区；可在启动前设置 `TZ` 覆盖。
- 应用**没有登录鉴权**，默认只监听本机。在远程服务器上运行时，请用 SSH 端口转发访问（`ssh -L 8501:127.0.0.1:8501 <服务器>`），不要直接暴露到公网。
- 不想后台运行时，也可以直接 `streamlit run app/主页.py`。

### 方式二：命令行

```bash
. .venv/bin/activate

# 全流程：日前 → 实时 96 个时段滚动 → 结算（最新报价版本，约 10–12 分钟）
python scripts/main_full.py

# 快速检查：日前 + 只滚动前 8 个时段，不出图（约 10 分钟，几乎全是日前 SCUC）
python scripts/main_full.py --periods 8 --no-plots

# 指定报价版本；切换储能实时模式（结果目录加后缀以免覆盖）
python scripts/main_full.py --bid-version 1
python scripts/main_full.py --anchor window_end_pct --anchor-x 0.05 --tag _b1   # B1：窗口末 ±x
python scripts/main_full.py --storage-mode fixed_da --tag _fixed_da            # A：执行日前曲线
python scripts/main_full.py --rt-lookahead-h 1 --intraday                       # 改前瞻时长、打开独立日内

# 只做日前出清（约 10 分钟）；--time-limit 缩短 MIP 时限可更快拿到可行解（gap 会变大）
python scripts/main_da.py
python scripts/main_da.py --season dry --time-limit 120 --no-plots

# 按网页里保存的场景运行
python scripts/run_scenario.py --scenario 默认 --mode full --periods 8
```

| 参数 | 脚本 | 说明 |
|---|---|---|
| `--bid-version N` | main_full / main_da / run_scenario | 报价版本，缺省用最新版本 |
| `--season wet\|dry` | main_full / main_da | 丰水期 / 枯水期，缺省见 `base.yaml` |
| `--periods N` | main_full / run_scenario | 实时只滚动前 N 个时段（调试用，缺省 96） |
| `--time-limit S` | main_da | 日前 MIP 时限（秒） |
| `--storage-mode sced_anchor\|fixed_da` | main_full | 储能、抽蓄实时模式 |
| `--anchor hourly_eq\|window_end_pct`、`--anchor-x` | main_full | 锚点方式（B2 / B1）和 B1 的浮动比例 |
| `--rt-lookahead-h H` | main_full | 实时前瞻时长，缺省 2 h |
| `--intraday`、`--id-interval-h H` | main_full | 打开独立日内滚动及其周期 |
| `--no-plots` | main_full / main_da | 不输出 PNG 图 |
| `--tag _xxx` | main_full | 输出目录后缀 |

参考用时（4 线程，2026-07-15 丰水期）：含抽蓄 PS1 后日前 MIP 较难，通常跑满 `config/base.yaml` 中 `solver.time_limit_s`（默认 600 s），取时限内最优可行解（gap 约 1%）；实时 96 个时段约 1–1.5 分钟；全流程约 10–12 分钟（2026-11-02 实测：报价 v2、`--periods 4` 合计 609 s，其中日前 606 s）。机器繁忙时会更慢。想更快试跑，可把 `solver.time_limit_s` 调小（如 120），代价是 gap 变大；不含抽蓄（`config/units.yaml` 中 PS1 `enabled: false`）时日前约 2 分钟、全流程约 3 分钟。

其他脚本：`demo_rebid.py`（改报价演示：违规被拒 → 修正提交新版本 → 改前改后对比）、`make_default_bids.py`（重新生成默认报价 v1）、`compare_pumped.py`（有/无抽蓄对比）、`bench_scuc.py`（求解参数对比）。

## 网页使用指南

左侧导航有 5 个页面，右上角的“当前场景”决定编辑和运行哪一套边界条件。

![主页](docs/images/web_home.png)

1. **主页**：项目说明、出清流程、算例资源、最新结果速览。
2. **边界条件**：一个“场景”就是一套完整的边界条件，修改后自动保存在 `scenarios/<场景名>/`。共 9 个标签页：
   - **场景管理**：新建、复制、删除场景，写备注（“默认”场景不能删除）；
   - **负荷曲线**：负荷缩放、季节、随机种子；96 点表格编辑或上传 CSV；
   - **风/光预测**：各阶段误差 σ、自相关 φ；96 点实际或日前预测覆盖；
   - **报价**：选择基于哪个版本、哪个主体，编辑段价段量、储能充放电段与期末 SOC、启动/空载费；页面实时校验，**有错误时不能提交**；提交后另存为新版本 vN+1，旧版本保留；
   - **机组/储能/水电**：火电、风光、联络线参数，储能容量/效率/SOC，水电来水与库容、库容型/径流式切换；
   - **线路限额**、**市场限价与备用**、**实时与滚动**（储能模式 B2/B1/A、x、前瞻时长、日内开关、求解器参数）、**抽水蓄能**（物理参数；市场规则同储能）。

   ![边界条件-报价](docs/images/web_bids.png)

3. **运行**：选择模式（仅日前 / 全流程）、报价版本、调试时段数，点“开始运行”。运行在后台进行，页面每 2 秒刷新进度和日志，可以随时停止。结果保存在 `runs/<场景名>/<时间戳>_<da|full>/`。
4. **结果**：选择一次运行（也会列出命令行生成的 `outputs/` 结果），8 个标签页：系统概览、节点电价（热力图、节点曲线、日前/实时/差值）、出力堆叠、储能（SOC 与锚点）、抽蓄/水电、线路负载率、网络拓扑着色、结算（按类型/主体/对账/明细，可下载 CSV）。

   ![结果-系统概览](docs/images/web_result_overview.png)
   ![结果-抽蓄/水电](docs/images/web_result_ps_hydro.png)

5. **场景对比**：任选两个结果，对比指标（A / B / B−A）、负荷加权价曲线、节点均价、分类型电量；两者都是全流程时再对比结算。

## 配置说明

所有参数都在 `config/` 下（网页修改的是场景目录里的副本，不影响仓库里的默认配置）。

**`config/base.yaml`（运行参数）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `date` / `season` | 2026-07-15 / wet | 运行日；丰水期 wet、枯水期 dry |
| `load_scale` | 0.68 | 负荷缩放（假设值） |
| `line_scale` | 5 条线 0.5–0.7 | 线路限额压缩系数，用于制造阻塞 |
| `solver.mip_rel_gap` / `time_limit_s` / `threads` | 0.002 / 600 / 4 | 日前 MIP 参数 |
| `solver.commit_step_periods` | 1 | 设 4 表示火电按小时启停，约快一半、成本 +0.4% |
| `solver.highs_extra` | {} | 透传其他 HiGHS 参数 |

**`config/units.yaml`（资源参数，均为假设值）**

- `thermal`：`pmax/pmin`、爬坡 `ramp`（MW/min）、最小开停机 `ut/dt`、启动费 `startup`、初始状态 `init`。
- `renewable`：装机 `cap`、曲线 `profile`；`tie`：联络线固定计划。
- `hydro`：`regulation`（`reservoir` 库容型 / `run_of_river` 径流式）、台数与振动区 `zones`、耗水率、`seasons`（来水、库容上下限、期末库容、生态流量、日电量）。
- `pumped`：`enabled`、`bus`、台数与单机功率、转换系数 `e_gen/e_pump`、上下库容、`vup_end_bid`、启动次数上限。
- `storage`：`pmax`、`ecap`、效率、SOC 上下限、初值 `soc0`、申报期末 `soc_end_bid`。

**`config/modes.yaml`（市场规则 `MKT`，逐项注明出处）**

- `price_limits`：申报、出清上下限；`segments`、`price_step`、`min_seg_frac`、`max_adjacent_step`：报价校验规则。
- `reserve`：备用系数 `rho_*`、N-1、`providers`、`storage_reserve`。
- `penalties` / `pricing_penalties`：调度罚因子和定价罚因子。
- `intraday`：独立日内（默认关闭）；`realtime`：周期、前瞻、锁定类型、水电和储能实时处理。
- `pumped`：抽蓄规则（`rules: storage`）。

**报价文件 `bids/{date}/MKT/bids_v{n}.json`**

```json
{
  "date": "2026-07-15", "rule_set": "MKT", "version": 1, "note": "缺省报价（假设值）",
  "units": {
    "C1":  {"mode": "price_quantity",
            "segments": [{"p_start": 0, "p_end": 290, "price": 0}, {"p_start": 290, "p_end": 361.2, "price": 300}, "..."],
            "startup_cost": {"cold": 600000, "warm": 400000, "hot": 250000}},
    "W1":  {"mode": "price_quantity", "segments": ["..."], "avail_pmax": null},
    "ES1": {"mode": "price_quantity", "dis_segments": ["..."], "ch_segments": ["..."],
            "soc_min": 0.1, "soc_max": 0.9, "soc_end": 0.5}
  }
}
```

- 段为 `[p_start, p_end]`（MW）和 `price`（元/MWh），首尾相接、价格递增（储能充电段递减）。
- 储能 `soc_end` 为申报期末 SOC（硬约束），`null` 表示不申报。报价中没有的主体（如 PS1）按 `src/smm/bids.py` 的缺省报价补齐。
- 建议通过网页“报价”页或 `scripts/demo_rebid.py` 的方式生成新版本，这样会先经过 `validate_bids` 校验；手工新建 `bids_v{n+1}.json` 时，同目录的 CSV 仅供查看。

## 输出文件

命令行结果在 `outputs/`，网页运行在 `runs/<场景名>/<时间戳>_<da|full>/`（另含 `meta.json`、`run.log`、`scenario_snapshot/`）。

| 目录 / 文件 | 内容 |
|---|---|
| `outputs/da_{date}_MKT_{season}_v{n}/` | 仅日前结果 |
| `outputs/full_{date}_MKT_{season}_v{n}{tag}/` | 全流程结果（含主要日前文件 `da_unit_awards/da_nodal_prices/da_storage.csv`；网页运行另有 `da/` 子目录保存完整日前结果） |
| `da_unit_awards.csv` | 各机组 96 点中标出力、启停、备用、节点电价、收入 |
| `da_nodal_prices.csv` / `da_lmp_matrix.csv` | 节点电价（含能量、阻塞分量）；母线×时段矩阵 |
| `da_line_flows.csv` / `da_storage.csv` / `da_hydro.csv` / `da_system.csv` | 潮流与阻塞、储能、水电、系统指标（仅日前目录完整输出） |
| `id_*.csv` | 独立日内结果（默认关闭，仅表头） |
| `rt_units.csv` / `rt_prices.csv` / `rt_lines.csv` / `rt_storage.csv` / `rt_system.csv` / `rt_lmp_matrix.csv` | 实时执行出力、LMP、潮流、SOC、切负荷/弃电、锚点松弛 |
| `settlement_detail.csv` / `settlement_by_entity.csv` / `settlement_by_type.csv` | 结算明细、按主体、按类型 |
| `settlement_reconciliation.csv` / `settlement_line_rent.csv` | 对账、各线路阻塞盈余 |
| `summary.json` | 关键指标、校核结果、用时 |
| `*.png` | 不加 `--no-plots` 时输出的图 |

各文件字段的详细说明见 [docs/开发记录.md](docs/开发记录.md)。

## 规则开关

| 开关 | 位置 | 取值 |
|---|---|---|
| 储能/抽蓄实时模式 | `modes.yaml` → `MKT.realtime.storage` | `mode: sced_anchor` + `anchor: hourly_eq`（**B2，默认**：窗口内每个整点 SOC = 日前）<br>`anchor: window_end_pct` + `x: 0.05`（B1：窗口末 SOC 在日前值 ±x 内，x 为假设值）<br>`mode: fixed_da`（A：执行日前充放电曲线） |
| 抽蓄规则 | `MKT.pumped.rules` | `storage`：报价、定价、日末库容申报、实时模式、备用、结算都与储能相同 |
| 储能/抽蓄是否提供备用 | `MKT.reserve.storage_reserve` | `false`（默认）/ `true` |
| 水电实时处理 | `MKT.realtime.hydro` | `fixed_da`（默认，执行日前曲线）/ `sced`（实时窗口内优化） |
| 水电类型 | `units.yaml` → `hydro[].regulation` | `reservoir` 库容型 / `run_of_river` 径流式 |
| 抽蓄是否参与 | `units.yaml` → `pumped[].enabled` | `true` / `false` |
| 独立日内市场 | `MKT.intraday.enabled` | `false`（默认，甘肃模式）/ `true` |
| 实时前瞻 | `MKT.realtime.lookahead_h` | 默认 2 |

命令行参数 `--storage-mode`、`--anchor`、`--anchor-x`、`--rt-lookahead-h`、`--intraday` 会临时覆盖上述配置，不修改文件。

## 测试

```bash
. .venv/bin/activate
python -m pytest -q tests/                # 全部测试，数分钟以上（含多次日前求解）
python -m pytest -q tests/test_bids.py    # 只测报价校验，几秒
```

| 文件 | 内容 |
|---|---|
| `test_bids.py` | 默认报价合规；违规报价（不递增、超限价、步长、段长、价差、储能放电价 ≤ 充电价）能被拦截 |
| `test_da.py` | 功率平衡、线路限额、LMP 分解、对偶互补松弛、有限差分检验 LMP、储能 0-1、水电约束、备用 |
| `test_rolling.py` | 实时滚动（缩短到 6–8 个时段）：预测收敛、SOC 连续、锁定启停、B2/B1/A 锚点、结算对账 |
| `test_pumped.py` | 抽蓄：约束、规则同储能、日末库容申报、实时 B2/B1/A、结算主体；水电库容型 vs 径流式 |

## 已知限制与主要假设

- 直流潮流，不计网损和无功；联络线为固定计划；负荷不报价。
- 机组技术和成本参数、备用系数、部分罚因子、B1 的 x = 0.05 等为**假设值**；规则依据和“摘要来源”“假设”的标注见 [docs/仿真方案.md](docs/仿真方案.md) 第 14、16 节。
- 确定性优化：预测误差通过备用和滚动出清处理，不做随机/鲁棒优化。
- 日前 MIP 按 gap 0.2% 或时限内最优可行解出清，启停为近优解，不同 gap 下价格可能略有差异。
- 水电按电站聚合、固定耗水率、以库容代替水位；实时默认执行日前曲线。
- 只模拟单日，不做多日衔接；未包含偏差考核、辅助服务市场、容量电费结算（抽蓄容量电费只作信息项）。
- 完整清单见 [docs/仿真方案.md](docs/仿真方案.md)“14 假设值与待核实清单”和“15 后续扩展”。
