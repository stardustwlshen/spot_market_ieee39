# 广东省内实时组合研究程序 v3.2：通用输入与完整压缩证据

这不是完整广东合规产品。仍为已验原统一事件费用、原400 MW、原资源资格，未接三态；AGC/AC安全放行和正式结算不自动通过。源内 `legacy_v1` 是经原MANIFEST逐文件核验的完整冻结数学依赖，安装无需引用本机旧项目绝对目录；python依赖见其 requirements.txt。

## 从用户选定且已接受的日前运行开始

```sh
python program.py seed --da-run /所选DA运行 --legacy-program ./legacy_v1 --run /全新RT研究目录
python program.py build --run /全新RT研究目录 --window 0
python program.py run --run /全新RT研究目录 --window 0
```

最后一条默认不求解。明确追加 `--execute-native` 才按一次60秒固定旧整数LP热启动→300秒自由SCUC→120秒新整数SCED执行。LP仅作热启动，不取代最终自由组合。已完成原生阶段逐字节/数学/worker身份复核后复用；未知、缺完成票据、损坏或改源拒绝重试。已接受窗口执行同命令时重做严格数学审查，0新求解。

```sh
python program.py accept --run /RT研究目录 --window 0
python program.py handoff --run /RT研究目录 --window 0 --simulate-no-deviation
```

handoff必须明确反事实假设，绑定前窗原始解、完整prefix、receipt、case/run/window与时钟。新进入的窗在build时复核边界。保留400 MW、通知与已下发状态，不将初始3格已下发说成实测已执行。

用户编辑的solar90/C1报价+1已接受DA已实际通过此通用seed和W0完整纯建模（264048列、248256行），0原生调用。本目录W4迁移也已纯通过；W4实际运行仍在独立v2证据中，没有重跑。

## 已完成压缩证据的兼容导入

`migrate-v2 --source-run ... --source-program ... --review <独立复核SUMMARY.json> --run <新目录>` 显式导入已审v2 W4结果及W5边界，登记原始source/receipt/raw双hash，不改旧票据，不在新版本冒充新求解。

后续冷温热三态必须使用独立参数/时间合同及新state(schema)：kind、各态notice、真实解列计龄、进行中曲线和尾段政策，不能隐式替换本版本uniform或自动迁移旧rt_notice。

当前通用新入口只做了纯输入/构建/迁移和故障门禁测试；v3.2自身还未运行新的原生SCUC链，不能用v2 W4实际结果冒称v3端到端实算。尚未运行96窗，也未通过目标Windows/Mac实机安装测试。

## 3.1 接口修正

旧 v3 保留不改。本版通用 seed 在创建目录前核父 DA RUN/ARTIFACTS/SCED acceptance 的 run、source、input 一致、完整 KKT 接受与原始 artifact/source pins；PREPARED 窗口、请求与当前边界逐对象绑定；verified 本身绑定 accepted request/names/SCED response 到 PREPARED 和实际完成票据。三个原缺口均须通过独立负向测试后才可安装，不借本说明替代测试结果。新原生counter来自实际 h.run 计数，历史 v2 未记录该字段的事实不回填。

3.2额外冻结HANDOFF所有非滚动顶层与state字段，防止保持prefix却串改DA_online、原点、来源或政策。原3.1纯结果保留，不回填旧票据。
