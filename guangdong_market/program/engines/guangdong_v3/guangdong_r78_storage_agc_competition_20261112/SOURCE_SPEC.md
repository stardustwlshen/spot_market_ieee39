# 完整读取依据与实现契约

来源《南方区域调频辅助服务市场交易实施细则（2026年版）》，总调市场〔2026〕8号，通知签发2026-08-31。URL https://finance.sina.com.cn/roll/2026-09-17/doc-iniscqyc0379275.shtml 。来源等级始终新浪转载扫描、未认证官方全文。官方新闻确认实施不认证当期参数。页hash见PAGE_HASHES.json；只读规则worker本轮实际核过，不新下载/求解。

## 公式与输入
按每出清小时、每调频资源分布区计算。D_z为区系统调频容量需求MW；q_i有效申报MW；R_i额定功率MW（§44）；C_i装机容量MW（附录6）；R与C独立提供不可偷换。b_i调频里程报价元/MW（非元/MWh）；k_i综合排序性能；b_floor当期下限；K_U性能理论上限，不是全控制区kmax。

§44 qmax=min(R,.20D)，qmin=min(max(.20R,Qlb),.15D)，端点可取，qmin>qmax无效。§42容量1MW、报价0.1元/MW最小单位。§45当期报价上下限；§46第三方未申报不参与无缺省。

附录6 r=q/C，声明域[QLL,1]。B=G(k/K_U)/(b/b_floor)，A=H(1-r)/(1-QLL)，S=B+A，满分100，不截奖励/得分。K_U=lambdaI*KI_upper+lambdaII+lambdaIII；基线.5*5+.25+.25=3，来源附录2、7上界推导且例表吻合，不是发布kmax=3。输入全区完整集合及独立有来源全控制区kmax不可省。

区内完整储能集合为该小时参加且正常提供服务、未被运营机构剔除的所有独立储能，非仅中标者。每排除者留原因/对应小时/证据。排序四键(-S,b,-k,q)，完全同键必须整组并列，不按ID破同。逐组累计x=(到该组全部q)/D；同组共同用组末端x。输入十进制字符串或整数，Decimal精度60、不显式中间quantize，循环小数仍有限精度，属于实现策略，不称官方生产精度。

Fm曲线：0≤x≤zx，F=Uy+(zy−Uy)x/zx；zx≤x≤Ux，F=zy(Ux−x)/(Ux−zx)。要求0<zx<Ux、0<zy<Uy。x=zx两段同值zy；x=0为Uy；x=Ux诊断Fm0但无排序价，x>Ux无值，均unsupported，不能epsilon/负Fm/自行淘汰。q/C<QLL或>1先返回unsupported且不做不完整集合映射。例R=C200,D50时qmin7.5、qmax10但r≤.05，与附录域冲突；不能改QLL或clip奖励。

§47全南方电网统一调频控制区所有调频单元kmax=max(k)，P=k/kmax，不从本地储能集合计算。§48储能排序价=b/(P*Fm)，非储能=b/P。排序价不可封顶15，例H/I为21.80/26.73。§49储能中标容量=申报q，不套热机厂级公式，不部分截额。本模块仅候选映射，不决定中标。

主市场排序为排序价升/P降/kI降，与内部评分q键不同；完全同值最终破同未规定。§57下限内资源不定价，无边际沿用上一小时，统一价=min(边际,出清上限)。本模块不算区域统一价。Fm只影响出清排序价，**不进入实际里程补偿公式**。

## 扫描基线，不是当期发布认证
G80/H20/QLL.20/Ux.70/Uy2.5/Z(.40,.60)/Qlb5MW；lambda(.5,.25,.25)、KI_upper5、推导KU3；报价floor3（可动态调）、报价upper15、出清upper15；Lss1h、Ts8h。Ux/Uy/Z可运营机构动态调整；持续时间§25可调整；floor附录5动态调整。不按年份推当期值。全部实现参数必须显式带版本/证据/生效起止覆盖完整小时；出清upper未用于候选算法，不设隐式统一价。当前完整数值均由调用方声明，样例仅扫描基线合成fixture。

## 资格、运行与安全边界
§8第三方准入，§9调度直控、并网调度协议/购售电合同；§16统一调频区AGC功能/注册；§25额定充放同向响应≥Lss；§27首次建模/性能测试、AGC连续Ts小时AUTOR并正式发布初值；§29额定功率初值来自并网协议。§43同时参与必须双市场资格并考虑最大容量/电量；通知p2说2026年9月同时参与技术测试，完成前按现行，不以生效日代替完成。本版只支持显式simultaneous_energy_agc分支，必须声明上述资格/测试，AGC-only暂unsupported；不是要求所有法规分支都有双资格。

§55基准功率为现货计划，未参与现货则0；本候选评分不计算/补填基准功率。§51/58另有SOC、AGC状态、网络/平衡和不得直接增弃电，尚需R77外部安全结果，资格声明非安全通过。没有IEEE39资格/测试数据，不接原50MW实际链。

## JSON接口与输出
schema gd.agc.storage.r78.v1，mode research；顶层hour_start/as_of带时区。population给complete=true、zone、demand_mw、offers及证据。performance_universe给complete=true、scope=entire_southern_unified_control_area、kmax及证据。每证据id/source/source_grade/hour_start/as_of；hour一致、证据不晚于as_of；等级research/retained_reposted_scan/caller_supplied_unverified。每offer submitted/normal_service/operator_excluded严格布尔；排除者reason/evidence，无默认报价/容量。活动offer字段与qualification完整见example.synthetic.json。外部完整性/资格只是有来源声明，接口不认证真实世界。

输出每资源score/base_score/reward/ratio/qmin/qmax/tie_group/cumulative_q/x/Fm/P/sorting_price，排除及unresolved明确保留；缺字段/资格/全集拒绝，unsupported不产可用整体映射，formal始终拒绝。源码无native、无补偿金额公式、无市场采购决策。

冻结前范围补充：每offer必须participant_type=independent_storage_third_party，即适用§49全额申报q规则的独立储能第三方；其他储能类型或缺该声明unsupported，不把Fm适用误等同全部储能全额q。当前共同理论上界K_U假设下要求外部全控制区kmax≤K_U；异质性能上界场景unsupported，不静默使用不一致输入。新增两项scope guard测试，共11项纯测试，0native，原公式未改。
