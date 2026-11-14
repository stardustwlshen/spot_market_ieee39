# 广东省内研究市场程序：完整源码交付

这是当前新输入研究pipeline的源码与明确输入闭包，不是旧结果回放包。旧 portable 程序保留在同名子目录，原相对布局保留以支持模块导入。没有复制完整历史native大解，也不把原acceptance作为新run许可。`SOURCE_MAP.json`记录原文件相对路径/hash、实际阶段manifest来源与补充依赖用途。

## 一处入口

使用安装了 `requirements.txt` 所列依赖的 Python（本工作区已存在解释器）。不自动安装，不自动求解；默认命令dry-run。以下 `ROOT` 指本目录绝对路径，可以从任意工作目录调用：

```sh
python "$ROOT/market.py" verify
python "$ROOT/market.py" energy run --run /绝对路径/新run --rt-windows 4
python "$ROOT/market.py" energy run --run /绝对路径/新run --rt-windows 96 --simulation-settlement --execute-native
python "$ROOT/market.py" energy resume --run /绝对路径/已有run --rt-windows 96 --simulation-settlement --execute-native
```

fresh run默认明确使用包内原IEEE39研究case、98点grid/费用policy、AGC合成申报、PRICE几何及RT初始准备/forecast资产；可用原pipeline参数显式替换，但当前只支持已声明的同构资源与参数域。报价−50、dispatch M2=1000、δ=.1、原SOC与正400/事故400不偷偷改变。MIP相对gap .001、绝对0、单线程。SCED严格数值版本、导入warning精确math profile及PRICE依赖清单均保留；不是blanket忽略warning。不同数学不匹配现profile须另作明确验证，不直接放行。

入口按DA、RT prepare、RT、独立PRICE、结算分开进程，避免不同smm版本相互污染。原始结果未知时停止，不静默重试；只有验证过的同run接受记录可以恢复，已下发前缀不重发。RT为固定新DA组合+309格可行尾部研究模型，不是R80滚动重新选机。96完整执行和96独立PRICE未齐，不能写完整结算。

`init --run ...`仅复制并验证输入，不建大模型、不调用优化器。`portable prepare-small --output ...`只构造1623列/3073行三态小模型。小例不等于全天官方模型。

## 辅助服务与金额接口

```sh
python "$ROOT/market.py" service reserve "$ROOT/examples/reserve.synthetic.json"
python "$ROOT/market.py" service energy "$ROOT/guangdong_r63_settlement_adapter_20261112/synthetic_coal_hour.json"
python "$ROOT/market.py" service agc-safety "$ROOT/guangdong_r77_agc_safety_gate_20261112/example.synthetic.json"
python "$ROOT/market.py" service storage-agc "$ROOT/guangdong_r78_storage_agc_competition_20261112/example.synthetic.json"
python "$ROOT/market.py" service mixed-agc "$ROOT/guangdong_r79_mixed_agc_projection_20261112/example.synthetic.json"
python "$ROOT/market.py" service agc-payment "$ROOT/examples/agc-payment.missing.json"
```

- R54为有偿旋转备用完整同价池核算，实际调度下限/完整池/资格/当期参数未知返回missing；模型400或dual不是补偿量/价。R63是电能量输入适配，不冒称备用模块。
- R27仅复用调频里程×价格×性能毛额及调整项算术，容量中标不等于里程；缺实测里程、性能或支付依据不填零、不造账单。其早期合成排序LP不能升级成正式市场。
- R77安全门禁、R78储能竞争、R79混合候选是独立研究接口；不凭空赋予风光/储能资格，也不声称与新能量链自动联合安全出清。
- 金额Decimal序列化为精确字符串。公开限价基线逐季度裁界再四点均价只形成研究候选；正式动态限价、参与资格、中长期合同、实测计量、税费及正式发布等缺项继续显式未知。所有示例均非付款指令。

## 验证与成熟度

本次只做从 `/tmp` 任意cwd的原case fresh init、RT/PRICE依赖加载、一次小模型构造，以及纯备用/混合AGC接口加载。0新native、0大模型构建、0AC。`VALIDATION.json`记录结果。运行环境需Python与依赖，不声称裸机无需安装。初始化后run文件绑定该运行绝对路径；移动已有run不能假装原许可证仍有效，迁移应显式重核。

初次打包时，新 pipeline 全天 run 尚在运行；目前已完成，见下方“最新实际研究运行完成摘要”及 FINAL_ACTUAL_RESEARCH_SUMMARY.json。R81三格AC均收敛但unsafe，不将DC/KKT/研究价格成功称作AC安全；本包未做OPF/N−1/动态稳定证明。R80两窗RT-SCUC另为小研究模块，未接全天。官方数据与解释缺口不得由研究参数填成“完全正式合规”。

## 跨平台矩阵 profile（显式准备，不自动求解）
完整大算例尚未在其他 BLAS/平台重新验证。约 1e-16 的浮点系数变化也可能触发默认 checked launcher 的精确数学摘要拒绝；不能忽略该拒绝或放宽阈值。

先用本包现有 DA prepare 接口生成该新 run 的 `da_scuc/{template,names,network_plan}.json`，然后：

```
python market.py prepare-matrix-profile --run /absolute/new_run
```

默认写入全新 `new_run/matrix_profile_candidate/`（已存在则拒绝）。也可显式 `--output` 指定不存在的目录。此步骤只读当前 run 的三个文件，计算精确 Fraction 系数箱界，保留原 1e-9/1e-12 cutoff 与 1e-6 活动误差预算，生成新的 `STORAGE_POLICY.json`、绑定其 SHA 的 `checked_mip_worker.py`、`validate_actual_import.py` 及来源清单。不构造大 Pyomo 模型，不导入 HiGHS，不调用优化器，不更改原模板/目标/限值/运行中源码。

下一步是独立、显式的存储导入检查（本次移交没有执行该步骤）：

```
python /absolute/new_run/matrix_profile_candidate/validate_actual_import.py
```

它会调用 HiGHS `passModel/getLp`，不调用 `run`/优化，按该新模板实际导入结果检验 tiny 项和 warning，成功写 `ACTUAL_STORAGE_IMPORT_VERIFICATION.json`。出现未知 warning/超预算必须停止，不能 blanket ignore。

**这不是自动激活接口。** 默认 checked launcher 仍锁定原已验 profile。新平台要实际求解，须先保存上述导入证明，再在独立 transport revision 中绑定新 worker/profile/guard/证明的完整哈希、该 run 的不变模板与新的 attempt 目录，更新该 revision 的实际 worker 选择和 transport manifest，然后显式启动。不得覆盖本包原 transport 文件，也不得把新 profile 偷换进原 acceptance/source identity。若没有实现并核对这一新 revision，运行保持拒绝；仅生成 profile 不代表跨平台整链已通过。新增接口避免旧 helper 的硬编码 run 路径，但不会自动绕过原审查边界。

本次仅对现存真实 template 做一次纯 profile 生成比对；验证输出中的绝对输入路径是此次测试证据，不是新运行的默认路径。早期纯生成路径检查误报记录保留在 `validation_matrix_profile`，修正后的完整候选在 `validation_matrix_profile_v2`；均 0 导入、0 优化。

## 最新实际研究运行完成摘要
本次 fresh run `gd-760dddd43b9a4f24af0b58f1af0c4e8a` 已实际完成新 DA 三阶段、96/96 RT、96/96 独立 PRICE 和 288 个模拟执行格。480 条电能量模拟核算行经独立算术/来源检查，价格先逐季度裁剪再取四点小时均值；2 条充电账户方向不符的微量正残差归零，原值 +8.376924884750655e-13 / +2.991758887410948333333333333e-13 MWh 及调整记录均保留。详见 `FINAL_ACTUAL_RESEARCH_SUMMARY.json` 与独立核查报告。此前 VALIDATION 中 running 字段是打包时点记录，由本摘要补充，原记录不覆盖。

这不是正式市场完成：费用基于明确合成参与/空合同假设，无实际计量或付款；R80 滚动重新组合未接入该 96 窗，AC 测试仍 unsafe，正式调频资格/里程/财务输入和当期限价认证未齐。没有复制巨大原始解；独立核查报告保留原件 SHA。核查脚本是审查原工作目录的证据，不作为便携包 run 默认入口。

独立回执/物理核查已通过，见 `FRESH_DAY_INDEPENDENT_RECEIPT_PHYSICS.json`：41 项既有前缀哈希未变；288 格 SOC 递推、热机/储能爬坡和自然小时方向已核；正备用与事故备用分别维持 400 MW。该核查未重算 KKT、未调用优化器，模拟执行不冒充计量。

## 用户交付 v2：新运行安全诊断与真实结果状态

本目录是独立源码副本，原 full 包和历史 accepted 结果未改。新 `energy run` 的 RT 使用 safe_workers/rt_precision.py（原1e-9/unscaled0），独立 PRICE 使用 safe_workers/price_standard.py（保留原标准 profile）。两者实际 launch、source identity、原 KKT review 已接通，不只是旁置方案。DA-SCED 的1e-10及 SCUC matrix-warning guard 不替换。默认 HiGHS，gap .001，threads1。非有限诊断输出 null 和原符号；Infeasible 保持不可行，TRANSPORT_RECEIPT 明确未数学接受。Optimal 仍须原全行/KKT 验收。失败/未知不自动重跑。

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python market.py help
.venv/bin/python market.py verify
.venv/bin/python market.py energy run --run /absolute/new_run --rt-windows 4
# 上行仅命令预览，不建 run、不验证所有实际输入；运行须明确增加 --execute-native。
# 新 run 已接受4窗后，用 energy resume --run ... --rt-windows 96 --simulation-settlement --execute-native
.venv/bin/python market.py status --run /absolute/run
.venv/bin/python market.py status --run /absolute/run --ac-plan /path/prepared_v1/PLAN.json --ac-result /path/actual_v1/RESULT.json
```

status 是只读证据视图，schema gd.delivery.user_status.v1。旧结果无需新 source pin 替换即可读取，不能用本版本恢复旧正在运行的 native。AC 输入必须是同 run 的 fresh executed bridge，核验源文件、DA/执行收据、时格及 AC 输入 hash。缺 AC 为 not_checked；三个抽样快照不论通过与否都不是全日/N-1/动态安全认证。研究能源链完成≠安全通过≠正式账单，formal_bill 永远 null。blocking_unknown_attempt 仅提示发现不完整 native 响应，不表示 false 时可以自动重跑。

本版本未实现跨全部阶段的安全取消，UI 应不提供取消按钮；不要强杀当前求解进程。历史源码及当前代码的普通中断证据仍保留。

本次6项纯测试通过（含真实 launch 路径的 mock Infeasible 完整 JSON/未接受），0新增 native、0全日重跑。本环境依赖 import/版本及外部 cwd CLI 已实测；没有声称从网络全新 pip 安装已测试。已有真实 run 的只读结果显示288执行、96 PRICE、480模拟行，三个AC时格 unsafe，见 STATUS_READ_ACTUAL.json。历史完整数值验收仍属于原profile，本新 transport 集成尚未重新实际求解全天。

状态补充：尚未96窗时自动选同 run 最大 PARTIAL_Wn，验证 DA/RT source identity、最新窗口 COMPLETE 全部文件 hash、实际执行 receipt 与5分钟时钟。不能把12个已下发格误称12个已执行格：历史4窗实际为9执行/4独立PRICE。逐窗口PRICE须已接受并验证独立KKT/响应/preparation/raw hash；尚无最终 PRICE_MANIFEST 也可如实显示。只有真实完整288执行、96价格及模拟结算才标研究能源链完成。新增历史4窗及完整96窗只读测试通过，本次共7纯测试，无native。
