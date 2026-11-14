# R79 热机+独立储能广东局部研究采购候选

运行：`ieee39-cloud/repo/.venv/bin/python -B guangdong_r79_mixed_agc_projection_20261112/mixed.py guangdong_r79_mixed_agc_projection_20261112/example.synthetic.json`

0native，8项针对测试及JSON示例通过。R73/R78/v24与原AGC50实际链均不修改。现IEEE39储能缺市场资格及技术测试声明，本例是独立合成输入，不将该集合赋予原场景。

## 契约

schema=gd.agc.mixed.r79.v1，mode=research。thermal为完整R73输入；storage为完整R78输入，两个冻结校验器原样调用。热机单位增加zone；储能增加主市场排序k1（不能拿内部评分q代替）。common_binding包含zone与source_ids，后者显式把R73参数version映射至R78对应evidence.id。时段/as_of、分布区需求、外部全控制区kmax、当期floor、floor有效期必须相同，source/来源等级/发布时点也绑定。仅来源等级名称作等义映射：research_assumption↔research、caller_supplied_publication_unverified↔caller_supplied_unverified，不能据此升级官方认证。R78完整群有任何unsupported即整个混合候选拒绝，不暗丢问题单元。

只支持R73热机和R78适用§49的独立储能第三方、simultaneous_energy_agc分支。储能全资格/技术测试/完整区群及外部参数要求不变。两个资源集ID不得重叠；不定义混合技术同厂共享上限，热机厂级计数仅热机，储能按§49完整q，不参与热机分摊。若需要其他储能身份/混合共享厂约束则本接口不支持。

## 算法及边界

主市场排序：(排序价升、P降、k1降)，三者完全相同用resource_id稳定顺序仅研究约定，需explicit exact_tie_policy=resource_id_research。热机排序价b/P；储能复用完整群Fm后的b/(P*Fm)，不封顶排序价。

每加入热机后重算所有已选同厂热机容量min(申报比例×扣停运容量, .2×分布区需求/中标热机台数)，广东Qu=Qr=0；储能始终完整q，不能按剩余目标部分截标，不能套热机容量公式。累计达到省内采购目标停止，整台可超目标。采购目标、分布区需求与保留下限独立。

保留下限内以及跨越下限最后整台均不定价，后者是明确研究约定，必须floor_crossing_policy=exclude_crossing_unit。无定价机组时返回None，本版不实现有来源上一小时边际输入，不伪造历史。短缺返回unmet且candidate_price=None，不补其他资格。最终candidate_price才以显式candidate_price_cap=15封顶；这是保留扫描基线研究域，不支持其他cap或与R73不一致的报价上限，不称当期官方认证。结果不是全区域统一价，也不是结算价。

依据仍R78 SOURCE_SPEC中§47/48主排序、§49热机与第三方容量、§57价格及R73保留研究歧义。源码只额外实现混合循环，复用资格及Fm而不重写。必须下接R77 §58外部结果和联合能量/网络/SOC/备用检查；未做这些检查不可称安全通过。无实际调度或正式发布。

示例储能D排序在前，全q40；两热机各40，合计120覆盖目标100。改热机同厂则两台各20且追加其他机组，储能仍40。此为可验证合成算例，不是运营数据。

共同性能上界补充：混合主排序使用的热机及储能k1均要求≤R78参数KI_upper，缺失不从综合k反推，越界拒绝。新增一条两类越界负控，共9项纯测试。
