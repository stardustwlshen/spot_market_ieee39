# fresh DA → fixed-DA RT adapter

函数：
- `accepted_da(run)`：只接`ARTIFACTS.json` schema gd.pipeline.da_artifacts.v1、三阶段accepted；逐文件run相对路径/hash、阶段run/input/source身份及实际Optimal响应核验，并调用公共verify_sources。
- `release_from_da(run)`：沿R62数量与R46 assemble_slices，产本run SCED物理96发布/103季度参考，不读取固定R53结果。
- `contract_from_da(run)`：显式inputs.rt_assets初始输入绑定；新建R62 source permit，不恢复旧live。static只原报价/库存，priors只四族外生先验，prep仅3格研究初态，不是fresh actual。
- `prepare_rt(run,output)`：显式构造R67全309物理LP，从本run SCED physical/expanded/names构造首可行见证并全行/全列审查；无native，可能构造较大LP，导入/本轮测试均不调用。
- `open_rt(run,prepared,output)`：新会话或从已有完整accepted occurrence重建cache固定前缀/实际向量/命令，重建新permit而非旧ticket。
- `run_rt(...,windows=96,execute_native=False)`：默认dryrun；显式True才逐窗一次新native。完整accepted窗口不重跑；带attempt但缺完整acceptance的窗口停止pending待核对，不猜测失败或自动重试。边界取消可以安全恢复。准备已写但未launch的中断目前明确需恢复处理，不静默覆盖。仅本run结果与前缀，不调用R80改启停。

目录兼容R67：Wn/prepared/input.json是本次local24，template为global309；accepted保存ACCEPTANCE/local24/issued3/STATE_FACTS，并在最后COMPLETE绑定该次所有文件。RUN_RESULT仅完整96写；部分阶段各PARTIAL_Wn，不覆盖最终。D96最后3格正常drain，288已执行，末尾21格仅lookahead。价格模块只读local24+actual接受收据，RT尾段dual不是独立PRICE。

各stage独立身份：DA共享协议source_identity；RT PREPARATION另含RT_source_identity（本脚本及R67/R62/R52/R46/R21/精度worker实际源）。RT修改不使DA重算，但已有RT准备/接受不能冒用新代码。run汇总记录两stage身份。

explicit_rt_assets/ASSETS schema gd.pipeline.rt_assets.v1，files三引用相对manifest：preparation/static/priors，每项path+sha256。必须DA init前绑定input_identity，不能DA完成后补换。源说明与原hash保存；提取没有复制旧SCED功率或native解。

此次6项纯边界测试：未accepted、hash/路径越界、未知attempt不重复、accepted恢复且tamper拒绝、dryrun不读取旧case、不连续日志拒绝。无大build/native，尚未声称新full chain已实际跑通。固定DA、冻结预测、零跟踪误差、原D96/日期吞吐及400/400数学保持；不是完整正式运营链。
