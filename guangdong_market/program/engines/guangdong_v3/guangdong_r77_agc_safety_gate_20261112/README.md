# R77 清算后§58安全结果闸门

独立纯接口，不改v24、实际执行链或任何资格。不调用求解器、不重跑R76。运行：

```sh
ieee39-cloud/repo/.venv/bin/python -B guangdong_r77_agc_safety_gate_20261112/gate.py guangdong_r77_agc_safety_gate_20261112/example.synthetic.json
```

输入schema `gd.agc.safety.r77.v1`、mode仅research。`procurement_target_mw`和`awarded_units`必须提供。每单元声明resource_id/kind/awarded_mw/sorting_price/P/k1与award_evidence。kind用于决定技术专属检查适用性，不授予资格、不计算储能Fm；后续R78可提供有来源的排序结果。

六检查必须全部存在：agc_normal、capacity_balance、network_sections、hydro_vibration、storage_soc、no_increase_curtailment。每项status必须pass/fail/unknown/not_applicable，附reason和evidence（id/source/source_grade/as_of）。这些是外部结果声明，闸门不执行电网潮流、容量平衡、AGC健康、振动区或SOC认证，也不认证来源真实性。证据等级research/retained_reposted_scan/caller_supplied_unverified。

not_applicable只允许非水电的振动区、非储能的SOC检查；其他检查不能以不适用绕过。unknown保留未知并要求解决，不变成pass。fail额外要求unit_specific归因、cause_id、cause_evidence；同因证据须一致。不能用全系统弃电差直接分摊到单机fail。

明确同因失败的所有单元按排序价降序、P升序、k1升序移除；三者相同用resource_id稳定排序，仅研究约定。不同原因分别列序，不称条文规定跨原因先后。多因同机只扣一次容量，保留逐因记录；输出重清算要求、剩余容量、短缺和未决检查，不补其他资源或创造资格。短缺即使没有失败也要求重清算。全pass亦不授予正式发布/调度许可。

来源是R72保存的总调市场〔2026〕8号§58转载扫描，印刷p22：https://finance.sina.com.cn/roll/2026-09-17/doc-iniscqyc0379275.shtml 。未获得直接官方全文，来源等级不升级。第58条包含不得直接造成或增加弃水/风/光；本接口将其作为统一no_increase_curtailment检查，具体分技术证据可放reason/source，不定义反事实算法。

CURRENT_CHAIN.json只引用R76已完成的固定UC研究比较：AGC50日弃电928.4927026821798、AGC0为612.6071506621912 MWh，差315.8855520199886。状态research_no_increase_not_certified；没有单机归因，removed_units=null而非假定无人失败。不是正式§58算法或本50MW场景安全通过证明。未改原50MW链和400MW备用。输入aggregate_no_increase只接受unknown或research_no_increase_not_certified，不能自动升级为认证。

9项纯测试+JSON例通过，0native。CLI可选--output新文件，拒绝覆盖。正式mode拒绝。未接正式发布或冻结v24。

时序绑定补充：顶层hour_start与gate_as_of均为带时区ISO；award/check/cause（及提供的aggregate）证据均必须具有与清算相同的hour_start，as_of不得晚于gate_as_of。拒绝其他小时和未来证据，不升级来源可信度。新增两项拒绝测试后共11项纯测试通过，0native。
