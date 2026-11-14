# 受限SCED数值恢复控制器原型（未接UI，0native验证）

所有新增源码仅本目录，未改当前v3引擎、原恢复目录或实际run。可移动本目录；显式`--engine`，按SUPPORTED_ENGINE文件hash核对兼容实现，不接受未经评审的任意引擎版本。context固定run/engine路径及hash，不能搬动已启动run后静默重绑。

```sh
python controller.py preflight --run RUN --engine ENGINE
python controller.py prepare --run RUN --engine ENGINE
python controller.py execute --run RUN --execute-native
python controller.py accept --run RUN
# accept成功返回SCED_recovery_accepted_PRICE_prepared后，原引擎正常resume
python ENGINE/market.py energy resume --run RUN --rt-windows 4 --execute-native
```

前置条件：当前源及SCUC已接受证据完整；SCED无acceptance、旧native明确COMPLETED+Optimal+有效原始primal/dual；隔离子进程重算原1e-8 reduced KKT确实failed。未知/超时/已经接受/同run已有候选一律拒绝，不把一般不可行当数值恢复。preflight只读；prepare排他创建run/recovery_controller_v4，将全部runtime+worker冻结，并绑定context和输入源码；原始失败不覆盖。

唯一候选保持完整数学和固定整数，仅IPM、crossover off、primal1e-10、ipm1e-12；原dual1e-10、scale0、HiGHS gap.001/abs0/thread1不变。执行显式授权一次，native_once排他建立；未知或已有记录不自动重试。启动request/worker、optimize_calls1及全部响应hash绑定。非有限诊断沿用明确null记录而非伪可行。

accept复用显式派生审查：重新验证candidate→原严格reduced/full expanded KKT→SCUC整数一致→原physical geometry；任何失败保留证据，不调阈值、不裁剪raw。通过后逐字导入run内来源可追溯occurrence，新增recovery_source_identity与原base身份并存，prepare-price引用新的SCED实际收据；仍由普通core执行PRICE/RT。未自动做后续native。接受后PRICE纯准备若中断需人工确认，不宣称已可恢复，更不自动重跑solver。

8项纯/zero-native测试通过：合法失败条件、已接受/重复/未知/原KKT通过/跨run拒绝、accepted preflight在子进程前拒绝、复制后context导入及源码篡改拒绝。未跑大型构模、真实新候选或原场景；完整实际控制器流程尚待一次授权验证，不能用之前手动恢复成功冒称本自动原型已实际验收。

## 最小接线要求

1. market增加独立 recovery preflight/prepare/execute/accept路由（不得无条件自动恢复）。
2. UI仅在已验证的completed Optimal但strict KKT失败显示明确“单次数值恢复”，默认preflight，用户明确执行；不删旧锁/原错误。
3. UI新锁包含run+controller/context+candidate身份。未知native只读，不触发重试。
4. 只有accept成功、SCED接受与PRICE PREPARED来源都匹配后，显式记录reconcile证据解除旧失败锁并普通resume。控制器不修改UI锁。
5. 暂不支持多个数值候选或任意算法编辑；原SCUC不重复、不复活旧live许可。
