# 枯水备用诊断：证据与复现

主报告：`枯水低新能源高负荷备用诊断.md`。

- `counterfactual_comparison.json/.csv`：原计划与DA额外100/250MW完整96段对照；执行段与前瞻分别统计，保留gap未达标。
- `baseline_shortage_details.csv`、`baseline_shortage_resources.csv`：原计划40个缺额段及容量/库存机制。
- `da_buffer100/`、`da_buffer250/`：完整运行；`counterfactual_spec.json`明确内存中的DA-only需求增量，原config不暗示已启用额外预留。两者仍有备用不足，非“修复成功”。
- `rt_probes/`：08:30首段清零但后段缺额增加；21:45明确infeasible。条件仅限原实际初态、原DA锁定计划与原硬锚。
- `parallel_on_DA/`：原模型parallel=on单项DA策略试验；`trial_status.json`区分仅准备、运行、完成和失败。
- `parallel_on_DA_preparation_attempt1/`：首次准备元数据不能序列化HiGHS默认无限时限而中止；未调用求解。修正元数据显示后重新准备，旧状态不删除。

## 环境

本次代码根目录为`../ieee39-dry-reserve-diagnosis`，c1f829a独立树；求解核心case/scuc/rolling/full/da/pipeline相对原归档50223f4未变。Python为`../ieee39-cloud/repo/.venv/bin/python`。HiGHS/Pyomo使用既有环境，不安装或升级依赖。

以下脚本已放`./scripts/`，均支持指定输入/输出；复制到别处时必须给依赖模型源码的脚本显式`--code-root`。`bench_frozen_parallel.py`还保留本机原归档和准备目录作为缺省值，移机务必显式指定`--source-run`和`--out`。脚本中的96时段以及默认探查34/87段对应本研究日，不是通用多日工具。`analyze_frozen_reserve.py`的400MW电池上界是本次固定三组电池，不能用于改容量场景而不更新公式。

所有输出使用新目录。不要重跑覆盖已有档案或已核验的结果包。

```bash
PY=/path/to/python
CODE=/path/to/ieee39-source
BASE=/path/to/pv2152_dry_lowre_highload_full96
OUT=/path/to/new-diagnostic-evidence
export MPLCONFIGDIR=/tmp/mplcache XDG_CACHE_HOME=/tmp/xdgcache

# 仅读冻结证据，不求解。
$PY scripts/analyze_frozen_reserve.py --source-run "$BASE" --out "$OUT"

# 两个全日敏感性。每次保留DA1%/600s、RT1%/180s及全部硬锚。
$PY scripts/diagnose_frozen_reserve.py --code-root "$CODE" --source "$BASE" --buffer 100 --out "$OUT/da_buffer100"
$PY scripts/diagnose_frozen_reserve.py --code-root "$CODE" --source "$BASE" --buffer 250 --out "$OUT/da_buffer250"
$PY scripts/summarize_reserve_counterfactuals.py --source-run "$BASE" --evidence-dir "$OUT"

# 两个条件RT窗口，不是全日方案，不放松原物理约束。
$PY scripts/probe_frozen_rt.py --code-root "$CODE" --source-run "$BASE" --out "$OUT/rt_probes" --periods 34,87

# 单项求解策略：先验证相同输入、相同LP模型、选项与规模，不求解。
$PY scripts/bench_frozen_parallel.py --mode prepare --code-root "$CODE" --source-run "$BASE" --out "$OUT/parallel_on_DA"
# 确认无其他重CPU求解/测试竞争后，才运行一次。不会自动重试。
$PY scripts/bench_frozen_parallel.py --mode run --code-root "$CODE" --source-run "$BASE" --out "$OUT/parallel_on_DA" --runtime-load-note "Other CPU-heavy solvers and test suites completed before start" > "$OUT/parallel_on_DA.log" 2>&1
```

策略试验保留原1%/600秒/4线程、目标/报价/物理约束，无免费已有解热启动，只改`parallel=on`并打开日志。准备阶段原/新LP字节一致，本次模型27840变量、2112未固定整数、33080约束。不要把更改启停备用能力的`tighten=true`当作等价优化。

读取原归档profiles.csv的100/250诊断经过标准DataFrame读写，最终CSV会重序列化。本次数值差最大5.684e-14MW，config/data/bids冻结快照和报价字节完全一致，DA各段只增指定MW、RT备用需求差为0。策略对照使用原prepare路径重新构建，且要求生成的profiles CSV与原归档字节完全一致。

`summarize_reserve_counterfactuals.py`当前将补充核对写入证据根目录`input_difference_audits/`，不再改写运行目录。完整归档独立校核使用主审查的`ieee39-final-evidence/verify_frozen_archive.py`，不把“归档一致性通过”误作“备用无缺额/最优性目标通过”。

## 复用完成的DA，恢复正式计划后继续RT

以下3个恢复脚本避免重复DA SCUC。`export_recorded_formal.py`只取正式阶段及明确标为dispatch的参考量；不能把auxiliary值当执行量。恢复先检验相同整数的LP重解，再固定全部已记录正式量补齐内部变量，并核对目标、库存、整数、约束、所有正式CSV和价格。任何核对不一致都会停止；不要绕过断言或静默改用新计划。

```bash
$PY scripts/export_recorded_formal.py --trial "$OUT/parallel_on_DA" --out "$OUT/parallel_recorded_formal_variables.json"
$PY scripts/replay_formal_da_sced.py --code-root "$CODE" --trial "$OUT/parallel_on_DA" --known-formal "$OUT/parallel_recorded_formal_variables.json" --out "$OUT/parallel_on_DA_sced_replay"
# 先阅读recovery_audit.json。若与原正式计划不同，应先决定恢复路线，不自动冒称原计划重现。
$PY scripts/continue_recovered_parallel_rt.py --code-root "$CODE" --trial "$OUT/parallel_on_DA" --replay "$OUT/parallel_on_DA_sced_replay" --known-formal "$OUT/parallel_recorded_formal_variables.json" --out "$OUT/parallel_on_DA_recovered_RT96" > "$OUT/parallel_on_DA_recovered_RT96.log" 2>&1
```

后一步只执行恢复LP及新96RT，不重求DA SCUC，原DA复制为只读参考、不覆盖。原DA策略为parallel=on，RT明确恢复原highs_extra={}、1%/180秒，不继承DA并行选项。原SCUC/SCED时间、恢复LP与新RT时间分开记录；注入已有DA的近零调用耗时不是新的日前求解时间。

本次15360个已记录正式量全部精确保持；12480个内部分段/辅助变量历史逐值身份不可证明。新增完整变量JSON和经过SHA256核验的本地可信pickle便于继续，只能载入自己产生的可信checkpoint，不能反序列化外来pickle。

## 本次完成结果

原模型parallel=on的SCUC在228.701103秒以0.6482498% gap结束；原正式SCED2.922846秒。恢复固定正式量LP3.247054秒，新执行96RT192.489228秒。RT最高gap0.9919995%，全部窗达1%。仍有37个执行段上备用不足，最大237.228332MW、积分986.219946MW·h，因此计算目标达标不等于备用物理问题解决。

恢复和续跑输出为`parallel_on_DA_recovered_RT96/`；`recovery_certificate.json`记录正式计划0差及未存历史内部变量的限制，`continuation_timing.json`分别列原DA、恢复及新RT耗时。证据根目录`parallel_recovery_all_integer_audit.json`独立检查全部2112个原模型整数，最大整数误差0。`parallel_RT96_comparison.json`及`parallel_RT96_shortage_periods.csv`给出新执行段完整指标。

可分发版本仅将parallel试验脚本的运行负载记录参数改名为`--runtime-load-note`，未改变任何模型、求解选项或执行流程；已有试验归档保留当时实际生成的记录键。原记录说明该次试验开始时其他重CPU求解和测试已结束，未使用CPU亲和隔离。

## 事前动态B包络及普通日控制

方法、限定和当前候选统计见`动态日前预留研究说明.md`。先只生成数据并审查，后运行一次B；A仅是离线分位对照，不运行优化。

```bash
$PY scripts/prepare_dynamic_reserve.py --code-root "$CODE" --source-run "$BASE" --out "$OUT/dynamic_DA_proposal" --renewable-multiplier .8
$PY scripts/audit_dynamic_information.py --proposal "$OUT/dynamic_DA_proposal" --out "$OUT/dynamic_DA_proposal/information_boundary_audit.json"
$PY scripts/run_dynamic_reserve.py --code-root "$CODE" --source-run "$BASE" --proposal "$OUT/dynamic_DA_proposal" --out "$OUT/dynamic_B_dry_lowre_highload" > "$OUT/dynamic_B_dry_lowre_highload.log" 2>&1
# 仅后处理、无求解；保留原状态文件，另写全精度审计和指标。
$PY scripts/finalize_dynamic_evidence.py --run "$OUT/dynamic_B_dry_lowre_highload"

# 普通干季控制，用自己的DA输入重建B，不复制上一案的MW数组。
NORMAL=/path/to/final_c1f829a_dry_nominal_full96
$PY scripts/prepare_dynamic_reserve.py --code-root "$CODE" --source-run "$NORMAL" --out "$OUT/dynamic_DA_nominal_proposal" --renewable-multiplier 1.0
$PY scripts/audit_dynamic_information.py --proposal "$OUT/dynamic_DA_nominal_proposal" --out "$OUT/dynamic_DA_nominal_proposal/information_boundary_audit.json"
$PY scripts/run_dynamic_reserve.py --code-root "$CODE" --source-run "$NORMAL" --proposal "$OUT/dynamic_DA_nominal_proposal" --out "$OUT/dynamic_B_dry_nominal" > "$OUT/dynamic_B_dry_nominal.log" 2>&1
$PY scripts/finalize_dynamic_evidence.py --run "$OUT/dynamic_B_dry_nominal"
```

运行脚本在DA结束后存完整正式变量、正式快照和可信本地checkpoint，再恢复原求解选项执行RT。该checkpoint写于选项恢复前，如需续跑必须按counterfactual_spec里的RT_base_solver_runtime恢复RT，不能继承DAparallel选项。

首个动态B进程使用的旧收尾审计将四位小数CSV与全精度需求以1e-8比较，会因正常四舍五入而误报失败。原执行脚本保留在`diagnostic_failure_reference/`，仅作失败历史，不运行。现可分发脚本严格核对全精度reserve_window_evidence，并单独检查CSV量化误差≤5e-5；`finalize_dynamic_evidence.py`可恢复原进程完整输出的审计与指标，不重求DA/RT、不覆写原失败状态、不放宽模型容差。

普通日准备阶段还保留一次未求解失败：毒值测试原先重新序列化DA浮点而引入末位差，后改为保留原CSV单元格字符串，只替换禁用列；准备失败目录为dynamic_B_dry_nominal_preparation_attempt1。该修复不改变任何B数组、种子、模型或预算。普通日只有一次实际DA/RT执行。

普通日原默认求解策略与本次parallel=on不同，返回方案的弃电/现金差不是单独归因于裕度公式的因果检验。未补跑第三组。两案原运行目录现已结束写入，后续说明与比较放在证据根目录。
