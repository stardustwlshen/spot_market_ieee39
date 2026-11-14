# R74 八季度独立 PRICE 研究对照

官方原件 region202610_official.pdf，SHA978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf；可检索保留文本 ../guangdong_r48_da98_structure_20261112/official.layout.txt 第5786行：T为每15分钟一个时段、考虑8个时段；第6033行§3.4.4.8为Pi,t−Pi,t−1≤ΔP上、Pi,t−1−Pi,t≤ΔP下。该段没有指定24×5min内部功率路径。本方案明确按输入MW/h×.25作为每季度Δ，首时段仍actual前功率。此为时间域一致研究方案，不声称已认证首点含义/储能报价/所有正式歧义。

原R69四例由实际前功率+5min路径平均与DA季度带直接矛盾：W25 C3季度必要≥455而上界446.9579；W27 C2≥456而上界421.42065；W32 ES4≥−116.6667而上界−120.07455；W48 ES4/5≥−116.6667而上界−120。精确原矩阵分数证书在 ../guangdong_r69_four_price_diagnosis_20261112。无需SOC条件即可证明，因此本次不采用改DA初始功率来保可行。

## 精确数学差分
从冻结R69 C serialized直接转换，无95次Pyomo重建：
- 删除240个thermal/BESS lift变量及480条5min ramp、80均值行。
- VRE子格没有ramp或独立5min电网，只受0≤P,C≤A及均值。消去120P+120C和120守恒+40均值，保留原40季度P+C=A、原季度上限/DA带与目标。这是子格可分配性的投影，不把DA可用量再次固定注入。
- 新增10资源×8季度×上/下共160条15min ramp。相邻季度用季度P；首段使用原RT实际前功率，不改运行状态。单季度内rate若不同、thermal出现transition则拒绝，不臆造转换。
- 原日期throughput行按原序列化均值等式代入季度P，保留R69 C中的日期U RHS。quarterSOC、D96硬锚、reserve/400、AGC、线路/平衡、目标与所有剩余变量域逐项不变。原δ=.1及C的DA-prefix SOC/U仍是显式研究假设。

6纯差分测试通过、0native：W0/25/27/32/48各726列1261行，W95多日期与D96合计1271行。W95 D96 k3原目标不变。不会改变任何actual RT约束或执行量，不回写原4个失败，不用fallback。

batch_six_v1/BATCH_PLAN.json为唯一六窗计划；主线程独占真实求解。成功仍是研究raw，不产生market/settlement price；完整后审自动检查新全矩阵KKT与39首季度raw导数。其他90窗尚未准备/求解，须等主线程后续指令。
