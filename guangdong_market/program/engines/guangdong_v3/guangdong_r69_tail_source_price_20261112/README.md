# R69 R67 全尾部执行源的独立 C 价格适配

当前只接口，0 native、无伪造 actual ticket/PRICE plan。四项纯测试通过：R67 pure LOCAL_W0_INPUT 独立构造数学与同源R64W0相同；全尾部输入冒充24格、旧bid0、纯fixture冒充actual源均拒绝。测试不产生真实来源或价格结果。首次测试仅同名模块导入冲突，已用唯一文件模块名纠正，无原数据或数学变化。

实际来源需 runroot/SOURCE_CONTRACT.json、SOURCE_IDENTITY.json、CACHED_TEMPLATE_BINDING.json；各W的prepared/{TICKET,input,local_RT_input,template,names,FEASIBLE_EXTENSION}.json、native原件和accepted/ACCEPTANCE.json。严格核 R67 新 schema、同次事件、源contract/identity、global_offset=3w、global309/local24、当前目标24格范围、tail_cost_zero、local_RT_input_hash、issued3及全尾部/局部strict acceptance。源码/缓存/来源bytes哈希入口出口验证，每窗完整memory/票据/实际源校验。未启动来源前不会生成计划。

只把已接受local24输入交给独立PRICE builder，再用R61通用C provider替换PRICE DA-prefix SOC/日期U并聚合季度SOC。绝不取全尾部LP dual作PRICE，亦不重复全尾部求解；C边界不执行、不修改RT。报价−50和原AGC/400/VRE/网络/ramp保持，同源R50v2/R53；保留D96索引与次日账。PRICE仍8季度网络，不宣称24格电气执行。金融价/账单未认证，market/settlement=None，成功不是官方修复。

实际W0接受后纯构造：
```
python -B guangdong_r69_tail_source_price_20261112/prepare_batch.py --actual <R67实际runroot> --windows 0 --output <全新prepared目录>
```
其输出BATCH_PLAN及哈希交主线程，显式launch_batch_once.py --plan/--plan-sha/--output才会真正求解，完成后独立全KKT/39首季raw LMP。每窗一次60s，不重试、不回填旧分支；后续批次待W0实测和主线程授权。
