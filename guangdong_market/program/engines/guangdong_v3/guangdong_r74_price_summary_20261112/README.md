# R74 全日独立八季度价格汇总

从冻结R69汇总器派生，接受R74批次schema，不接受R69价格来混成R74全日结果。输入实际六窗批次及后续90窗批次，必须96唯一窗口、同一R67实际run与同一DA source，完整绑定prepared/原响应/新全矩阵审查。不重复native/KKT。

输出明确R74八季度功率/ramp研究模型，首段实际前功率、MW/h×.25、C的DA-prefix SOC/日期U仍为研究。原R69四窗Infeasible与其余旧raw不覆盖、不回填。R74研究成功不是完整正式解释的认证。

调用冻结R47按公开−50..1800基线逐quarter裁界，再每小时四点算术均值；保留3744个raw与研究候选季度node位置、936小时候选。动态当前认证仍无，market/settlement/formal_bill=None，不付款。实际两批共96窗已完成 Optimal 与完整 KKT；全日结果为 `actual_day_v1/PRICE_SUMMARY.json`，3744 个 raw 节点价格齐全、936 个小时研究候选，无 pending。

`summarize_corrected_v2.py --batch <six plan> <six native root> --batch <remaining90 plan> <remaining90 native root> --output <new dir>`。

## 最终勘误与冻结

正式用于本次研究汇总的入口是 `summarize_corrected_v2.py`；原 `summarize.py` 和旧 `MANIFEST.json` 原样保留。`actual_day_v1/ERRATUM.json` 明确原 R74 批次 review 继承的 `source_transport_only_delta=True` 标签不适用：R74 的数学时间域确实改变，最终汇总为 `mathematical_domain_changed=true`、`source_transport_only_delta=false`。勘误逐窗绑定 transformation 精确差分，不修改原 response、review 或 KKT 计算。

`FINAL_MANIFEST.json` 是新建的最终文件清单，覆盖 R74 source、两批 actual、summary，以及 R69 四例不可行证明目录；不替换既有源绑定。此后这些成果冻结，未新增求解或重复审查。
