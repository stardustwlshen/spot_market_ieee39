# R53 同一新98目标/−50报价的 SCED→独立DA PRICE

独立于R42/R44旧链，主线程控制native。本目录脚本均不调用求解器。

1. `prepare_sced.py --review NEW_R50_REVIEW_DIR --native NEW_R50_NATIVE_DIR --output NEW_SCED_PREPARED_DIR`
   - 先核R50v2 READY全部源、实际SCUC票据和新接受复核，不接受R42/诊断票据。
   - 固定新SCUC选出的16560个原整数。运行目标保留98点热机增量报价、曲线能量、储能和−50P+1000C。
   - 明确去掉固定启动/停机事件费和connected最低费；保留固定启停曲线能量常量。COST_SCOPE逐项列出，不拿旧no_load代替80元/MWh合成最低申报价。
   - 根据新谱重新做24小时5×10MW的纯AGC选择，保留合成资格标签和原物理预留。不导入旧AGC接受票据。
2. 主线程单独执行新SCED LP。
3. `review_sced.py --prepared NEW_SCED_PREPARED_DIR --native NEW_SCED_NATIVE_DIR --output NEW_SCED_REVIEW_DIR`
   - 新compact/full逐行逐列KKT，所有列（含free angles）统一1e−8 stationarity、1e−6 primal，fsum累计。**不沿用R42原五角例外**；若不通过保留失败并另诊断。
   - 与同一新整数谱一致、对新R50自由全矩阵作物理复核、独立逐段新成本复算。
   - 物理几何抽取复用旧代码逻辑，旧48h费用标量丢弃且不验证/不交付；新成本components与SCED objective独立绑定。
4. `prepare_price.py --sced-prepared ... --sced-review ... --sced-native ... --output NEW_PRICE_PREPARED_DIR`
   - 只能从新SCED Optimal/KKT、new physical witness与新hash创建R53 receipt。
   - 复用独立DA98价格数学、576物理lift、eligible VRE block；源换成新SCED，不用旧permit。
   - 新SCUC/SCED/DA PRICE读取同一−50公开参数基线，PRICE M2′1000另与调度研究M2=1000区分。
   - 保留代表点0.25h、connected最低费80与资格/预测为研究声明；不将PRICE量替换SCED物理发布量。
   - 金融DA参与未知，金融价格/正式账单仍None。后续PRICE实际求解和定价后验尚未由本目录完成。

原物理contract保存旧研究来源与5分钟几何用于验证；生成新PRICE case另显式`market98_hidden_physical_research`，没有把原48小时经济目标继续冒充新98目标。合同源/规则/来源接口不是实网认证。

本目录与R51一小时结算模块不同。最初两份未运行源码已从临时R51桥接目录移入R53，未触碰R51结算目录。
