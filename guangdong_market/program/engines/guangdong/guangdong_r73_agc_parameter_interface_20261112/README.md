# R73 当期调频参数接口（研究投影，非正式出清）

可运行：从恢复根目录执行

```sh
ieee39-cloud/repo/.venv/bin/python guangdong_r73_agc_parameter_interface_20261112/adapter.py guangdong_r73_agc_parameter_interface_20261112/example.synthetic.json
```

可加 `--output 新文件.json`，拒绝覆盖已有文件。输入错误返回退出码2；无任何求解器调用。例子当期下限1是明确合成参数，不声称广东当前值。9项纯测试通过；示例分布区需求200、省内采购目标100、保留量0、外部kmax2，因此三台各40MW整台中标、合计120MW，研究价6；不是省内实测结果或账单。

## JSON契约

`schema=gd.agc.r73.v1`，`mode=research`，`scope=guangdong_local_research_projection`，`cross_zone_exchange_assumption=0`。`as_of/hour_start` 必须带时区，准备时点不晚于T−30分钟。

`parameters` 五项均不可缺失：`bid_floor`（yuan/MW_mileage）、`distribution_zone_demand_mw`（MW）、`provincial_procurement_target_mw`（MW）、`local_reserved_floor_mw`（MW）、`full_control_area_kmax`（dimensionless）。各项包含 `value,unit,version,source,source_grade,available_at,effective_from,effective_to`；有效期覆盖整个小时，发布可用时间不晚于as_of。来源等级只能 `research_assumption`、`retained_reposted_scan`、`caller_supplied_publication_unverified`。接口不认证调用方的来源声明，不自动认定官方参数。

下限须外部提供，保留扫描的范围1至3仅是输入包络，不推演当前值，不按次数自动下降0.5。上限仍15。分布区需求用于同厂20%容量上限；省内采购目标用于排序截止/短缺；本地保留量用于非定价范围，三者不能混为一值。全控制区kmax不能从本地集合推断。参数不足即失败，不从旧例子填补。

`units` 每项要求 `resource_id,participant_type,submitted`；participant_type为generator或third_party，submitted严格布尔。第三方明确未申报则列入nonparticipants，不补报价/容量、不参与排序；未知申报状态拒绝。已申报只接受有显式资格声明的thermal，其余R31热机字段见例子。普通发电机缺省申报适配未实现，未申报拒绝。储能Fm/资格/测试与水电计划/振动区未实现，已申报这些技术明确拒绝；未申报第三方储能被排除不等于获得资格。历史边际价的来源适配未实现，本版本拒绝输入裸previous_raw_marginal，缺当前定价机组时保留未定价。

## 来源与边界

先对照R72 `README.md/evidence.json` 后实现。沿用总调市场〔2026〕8号转载扫描：§45/附录5、7动态报价下限，§46第三方未申报不参与，§47–49归一化及容量，§57价格，§58安全校核。来源 https://finance.sina.com.cn/roll/2026-09-17/doc-iniscqyc0379275.shtml 。官方新闻仅确认修订实施；本轮无直接官方全文附件，不升级扫描等级。R72第58条证据仅证明安全校核要求，不能据此认证本程序的校核。

`projection.py`是R31的可审计派生，基准路径/哈希及三处变化见DERIVATION.json：必填当期报价下限、校验范围、将20%分母改为独立分布区需求。原R31、v23均未修改。其余算法及研究跨越机组/同价次序约定不变。外部安全排除名单不是网络/弃电/SOC/振动区独立安全证明。

返回unmet_requirement_mw且短缺时price=null，不虚构跨区补足。始终formal_supported=false、security_certified=false、formal_publication_allowed=false、dispatch_authorized=false。formal模式一律拒绝。此模块可供未来统一入口调用；未接入冻结v23，不改变400MW备用，未跑native、未构造新大模型、未产生应付账单。
