# R63 电能量结算 typed JSON 适配器

复用冻结 R36 `settle_energy`、`paired_storage`、`aggregate_hour` 及其保留 R14 算术，**没有重写新能源公式，也没有新求解**。仅支持已建立的普通煤/气、独立储能参与日前电能量核算范围。

```sh
ieee39-cloud/repo/.venv/bin/python -B guangdong_r63_settlement_adapter_20261112/adapter.py \
  guangdong_r63_settlement_adapter_20261112/synthetic_coal_hour.json
# 充电、放电两账户分开核算，再展示代数合计：
ieee39-cloud/repo/.venv/bin/python -B guangdong_r63_settlement_adapter_20261112/adapter.py \
  guangdong_r63_settlement_adapter_20261112/synthetic_storage_pair.json
```

可加 `--output NEW_FILE.json`，禁止覆盖已有文件。结果 pending 时退出码2；成功只是电能量分项算术，所有结果 `formal_bill=null,payable=false`。本目录依赖相邻 R36 及其已恢复源码，`PINS.json` 固定原公式来源。不是自包含安装包。

## 输入契约

完整可用JSON见两份 `synthetic_*.json`。数字使用十进制字符串，单位明确为 `MWh` 或 `CNY/MWh`，时间须含时区。

- 单账户 schema `gd.settlement.r63.ledger.v1`。`key` 必须包含资源、结算单元、节点、charge/discharge方向和准确起止。DA/RT award 必须绑定完全相同key，运行/定价/发布ID独立且完整。煤气按整点小时；储能15分钟必须另有资格attestation。
- `channel=simulation` 配 `authenticity.status=synthetic_declared`；`formal_data` 配调用方提供的 `externally_verified` 及来源引用。**这些字段不是本模块完成认证的证明。** 正式数据算术仍需R36计量/损耗/税/节点/合同范围basis、官方分类/适用性声明及适当数据来源；输出仍不是机构账单。
- 每个数值、资格、账户映射、合同库存都带 `Evidence`：status、source_id、SHA256、version、issued_at、available_at；不得晚于asof。未知使用missing+原因和null，不得默认0。
- meter_kind正式仅`metered`；模拟仅`synthetic_meter_fixture`或`simulated_executed`。R62执行功率乘时长只可进入simulation，不冒充真实表计。
- 正式价格标识必须`verified_published_settlement`，模拟必须`synthetic_price_fixture`。不接收报价、raw_solver或bounded_candidate。final发布状态、来源与可用时点仍由保留typed工具检查。
- 合同ID唯一，与代购ID不重叠；每份合同声明参考节点、DA/RT参考市场、该合同参考选择的依据及小时参考区间。官方允许参考DA/RT不表示任意选择市场或参考点。季度合同需原小时电量及资格，按原细则校验`Qquarter=Qhour/4`。
- 已知空合同仅允许显式研究演示：`contracts=[]`、`known_empty_contracts=true`及有来源库存声明。适配器透明生成带固定内部ID的零项来满足R36既有API，不声称存在零量商业合同，结果始终不可支付。真实未知库存或空库存没有被偷换为0。
- 实际有来源的零DA量合法；未知参与状态不合法。普通煤气/储能non-DA映射未建立，不借用新能源分支。煤气代购模式/显式零基数及代购收入遵守R36现有边界。

双账户 schema `gd.settlement.r63.storage_pair.v1`，含discharge和charge两个完整ledger。同资源/节点/时段且不同结算单元、同channel/asof/参与状态；不能先净电量删除两账。上网正、充电负由原算法校验。

小时聚合 schema `gd.settlement.r63.aggregate_hour.v1`，含kind=`price|energy`、channel、asof、4个连续quarters，每项start与typed value；价格另需price_kind。复用R36：价格算术平均，电量求和。小时合同分季度的`/4`不是实际计量聚合规则。

## 已测

9项纯测试：实际调用R36并匹配原47900煤机示例；100/200/300/400价格均值250、2MWh偏差为500；储能RT两账±600；−200小时合同→−50季度及资格；raw/candidate/报价拒绝、缺表计拒绝；未知参与/代购/库存及重复合同拒绝；合法零DA；明确空合同仅模拟；单位与节点时段绑定。没有实际账单或优化器调用。
