# 广东市场研究程序：可移交源码包

本目录可整体复制到任意工作目录，不需要原scratch路径或原cwd。含独立的调频纯接口、原样vendor数学源码、三态小模型构造和显式一次HiGHS MIP求解入口。**不是完整正式广东运营系统，也不自动将任意新案例跑成全天市场。** 原场景与验收结果未改，包内没有旧原始解或失败大产物。

## 快速使用

Python3.11+。纯接口仅标准库，无需安装依赖。小模型/核心需 `python -m pip install -r requirements-core.txt`（这里列的是既有环境已测版本；未重新下载安装）。

```sh
python market.py --help
python market.py readiness
python market.py validate --module mixed --input examples/mixed.json
python market.py run --module mixed --input examples/mixed.json --output mixed-result.json
python market.py run --module storage --input examples/storage.json
python market.py run --module safety --input examples/safety.json
```

`thermal/storage/mixed/safety`分别R73/R78/R79/R77严格契约，示例全部合成，绝不用于授予现有IEEE39储能资格。`validate`执行对应纯校验/算术，0native。缺参数、跨小时、未知资格、未闭合域返回非零，不补零。`--mode formal`一律fail-missing；正式数据未知与算法缺失分开见docs/SOURCE_CAPABILITY.md。

## 真正模型入口，不只是AGC

```sh
# 原样R68三态小集成：1623变量、3073行，无native
python market.py prepare-small --output ./prepared-small
# 默认仅检查/显示命令，不运行solver
python market.py solve-prepared --prepared ./prepared-small --output ./native-small --time-limit 30
# 只有明确此旗标才调用一次HiGHS；本次打包没有执行此命令
python market.py solve-prepared --prepared ./prepared-small --output ./native-small --time-limit 30 --execute-native
```

小例含不同三态曲线/通知/单次费与能量平衡，不是完整IEEE39网络。旧工作区同数学实际Optimal6140.75；**初次打包仅小模型构造与dryrun；随后主线程已在另一个复制目录从/tmp实际完成一次native，Optimal6140.75，见ACTUAL_PORTABLE_VERIFICATION.json。本线程此次R80增强未重复native。** 新结果逐行/变量/整数/objective audit、实际计数/版本/设置、有限bound/gap复核；未Optimal/超时/失败保留pending，消费一次许可不自动重试。只支持MIP；LP需独立KKT适配，当前拒绝。

`prepare-scuc --input examples/scuc.bundle.template.json --output ./prepared-full` 提供R56原样48h/DA98数学构造接口；模板故意缺四个输入文件，因此直接fail-missing，不默认重建26万变量。须由调用方提供case（既有job codec格式）、98grid、显式policy与AGC；路径相对bundle，不相对原工作区。内部恢复horizon/int-key语义，检查一峰一谷及finite penalty，之后原builder执行其他合同检查。原统一事件研究数学不被伪装完整三态大案例。此包装的完整大case构造本轮未跑。

## 边界与移交状态

- 52个核心源码文件967215字节，43个smm模块约909KB；保留原相对vendor目录、源码逐字hash映射。只复制源码和小示例，另附原6.1MiB研究案例输入与小型grid/policy/AGC；raw解未复制。
- 独立复制整个目录到临时路径，从另一个cwd实测help/readiness、四模块校验/纯算例、formal/missing负例、小模型build及solve dryrun，加codec输入horizon/整数键解码共3项组合测试通过；0native/0大模型。
- HiGHS默认gap0.001/abs0/threads1。R80独立固定研究fixture的两窗RT-SCUC→SCED已新增源码接口，见下方；不是任意新案例日内运营系统。完整96RT/96PRICE/480行结算是已有研究证据，`python market.py evidence`给路径及hash索引，**不是本包可单命令重现的完整链**。
- 可选`backend-readiness --backend-root 原恢复根目录`仅检查候选入口存在，不执行、不宣称依赖全部完备。核心小模型不需要此后端。
- 当前AGC50 no_increase_not_certified；新资格不自动应用；Fm不进里程补偿。日内变预测/再组合/重新发布完整运营链尚未验证。

新增纯wrapper见market.py/core.py；冻结数学源见SOURCE_MAP.json。requirements-dev仅用于pytest。完整移交清单见ACCEPTANCE.json。

完整性：每次入口运行逐个比对SOURCE_MAP内源文件hash；准备合约另绑定map和core源码hash。audit原函数强制primal/integer≤1e−6、目标误差≤1e−6+1e−9|obj|，不是只打印残差。解码测试在调用真正大builder前截停，未大build。

## 已齐全的IEEE39研究输入与概要
`examples/ieee39_research/bundle.json` 已带四个输入，直接可交给prepare-scuc；与故意缺参的scuc.bundle.template.json负控并存。已做加载及R56合同预检，未大build/native。原输入来源hash和假设在该目录。`evidence/R67_SUMMARY.json`、R74_SUMMARY.json、R75_SUMMARY.json为各约数KB的只读来源摘录，带完整原文件hash，不是本包独立重现全天的声明。

## 后续实际搬迁验证与R80两窗模块

主线程对原交付快照在 `guangdong_delivery_actual_verify_20261112/package` 从/tmp cwd实际运行小模型：1次HiGHS1.15.1、Optimal6140.75、bound6140.75、gap0，1623列3073行1273整数，primal1.42e−14/integer0。ACTUAL_PORTABLE_VERIFICATION.json绑定当时快照manifest、review及日志hash，不复制raw解。此前“打包0native”是作者准备阶段统计，不能覆盖随后这次实际验证；本轮R80接入仍0native。

```sh
python market.py rt-small-fixture
python market.py prepare-rt-small --output ./rt-w0-preview
python market.py run-rt-small --output ./rt-two-windows
# 仅此旗标真实运行，最多2 MIP+2 LP；不自动重试
python market.py run-rt-small --output ./rt-two-windows --execute-native
```

R80仅支持内嵌已声明固定两母线研究fixture，不接受任意变更JSON。examples/r80.fixed.synthetic.json是导出说明，非参数修改入口。prepare是W0纯预览；run从冻结源码重新构造因果两窗链，不复用预览作为旧许可。新整数固定到SCED，保留首次通知/曲线进度、已发布前缀、SOC/日期吞吐/自然小时方向。原R80已有4次实际Optimal及独立事件复核（145前缀漂移0、热态notice0/start3/曲线至8/stable9、储能新放电模式），evidence/R80_EVENT_SUMMARY.json含来源hash。它只发布6格、实际已执行0格，不是假称执行30分钟；固定研究AGC非正式竞争、DC非AC安全认证。

搬迁包新增R80纯源码与LP KKT依赖原样vendor，新增一次独立复制/其他cwd测试完成runner导入、1361列2439行W0小构造及run dryrun；未重复4次native。原完整96窗链仍不由这个两窗入口取代。无新大模型、无广泛重复审计。
