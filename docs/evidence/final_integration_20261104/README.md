# 集成验收证据索引

本目录为2026-11-04的紧凑证据。它与[主报告](../../全程序集成验收_20261104.md)配合阅读，不替代原始完整结果包，也不将程序回归通过等同所有场景通过。

## 内容与口径

- `final_suite_complete.log`：前阶段2aec3d6代码的完整1434项测试，0失败、0跳过。旧告警回放使用了明确指定的历史存档；普通源码安装不包含该旧存档时会按测试设计跳过3项。
- `model_code_equivalence.json`：最终代码与实际全天求解代码的18个核心模型/配置文件逐字比较。之后仅新增本报告与证据，不改求解模型。
- `full96_comparison.csv`：正式执行首段累计的完整96段指标，不混入未执行前瞻量。
- `final_c1f829a_*`、`pv2152_*`：每个正式运行的输入说明、真实求解终止与间隙、保存文件清单、磁盘独立核验摘要。根层索引覆盖71个输出文件；本紧凑目录只复制摘要，不包含索引列出的所有原始文件。
- `scenario_54lp_*`、`evening_lowwind_lp_solve.json`：54种独立风光/负荷/水文组合与额外晚间低风事件的零松弛LP检查。它们不是MILP或RT可行证书。
- `forecast_128seeds_*`、`forecast_W1_before_after.png`：生成器对照。变化幅度降低不代表真实预测能力提高，也不保证所有误差指标改善。
- `network_reference_comparison.*`：无损、单位变比DC模型与参考网络的差异，不是AC/N−1校核。
- `dry_reserve_diagnosis/`：备用缺额机制、100/250MW日前预留反事实、条件窗口探查、parallel=on单项策略试验及事前冻结动态B的两个完整日。所有未满足项保留。
- `validation_archives*.json`：五个原始输出压缩包（基准、边界、恒定预留、并行策略、动态预留）的字节数、SHA256及逐索引文件哈希核验。压缩包另行交付，不随源码重复保存。

`initial_failure_contracts.csv`与`final_suite_c1f829a.log`逐项保存最初7个回归失败的契约和根因；`fresh_source_archive_smoke*`记录独立解压源码包后的只读准备和首页交互检查。

## 重跑正式全日与磁盘核验

在已安装项目依赖的环境中运行，`CODE`是本项目完整源码目录，`OUT`必须为不存在的新目录。不要指向原场景或旧输出目录。

```bash
PY=/path/to/python
CODE=/path/to/spot_market_ieee39
OUT=/path/to/new-acceptance-run
$PY "$CODE/docs/evidence/final_integration_20261104/run_frozen_acceptance.py" \
  --source "$CODE" --output "$OUT" --season wet --forecast-model multiscale_v2
$PY "$CODE/docs/evidence/final_integration_20261104/verify_frozen_archive.py" "$OUT"
```

干季高负荷低风光使用`--season dry --renewable 0.8 --load 1.1`；丰水高风光低负荷使用`--season wet --renewable 1.2 --load 0.9`。这些选项创建独立冻结输入，原输入不得变化。仍使用原1%目标和求解时限，不保证每次均在时限内达标。单个混合整数问题的求解路径和限时可行解可随平台、求解器和负载不同而变化。

`verify_frozen_archive.py`针对本项目96段/参考母线31的现有模型。它重载磁盘检查完整键格、冻结输入及索引哈希、DC平衡、TIE、存储动态和硬锚、求解状态、两级结算及成本恒等式。其通过表示归档一致性与所列核对通过，不表示零备用缺额、最优性达标或全部物理约束均获得独立认证。

原始计算中的临时目录、独立检验环境和绝对路径仅用于定位本次证据，移机时显式提供自己的路径。紧凑副本的执行负载说明已改为客观描述；原始求解数值、状态与哈希不变。

后续用户完整操作检查的代码、日志与最新全量回归见[用户操作复核证据](../user_workflow_review_20261104/README.md)。本目录保留当时的计算与求解策略证据，用户随后已明确接受不利组合备用缺额，未再为清零改变政策。
