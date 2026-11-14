# R78 储能调频竞争评分候选接口

运行：`ieee39-cloud/repo/.venv/bin/python -B guangdong_r78_storage_agc_competition_20261112/storage.py guangdong_r78_storage_agc_competition_20261112/example.synthetic.json`

完整公式、输入、扫描基线、资格/测试边界与来源见SOURCE_SPEC.md，19页证据hash见PAGE_HASHES.json。例子全为明确合成数据；未赋予当前IEEE39储能资格。只simultaneous_energy_agc研究分支，AGC-only暂不支持。

9项纯测试通过，0native。Decimal精度60，无显式中途舍入；同四键组末累计，不按ID拆组；KU上界与外部全控制区kmax分开。q/C越域或x≥Ux明确unsupported，Fm0不除、不补epsilon。排名价可>15、不截断；Fm不用于补偿。不做完整区域clearing、不发正式市场价格、不修改R77/v24或原50MW链。

冻结前范围补充：每offer必须participant_type=independent_storage_third_party，即适用§49全额申报q规则的独立储能第三方；其他储能类型或缺该声明unsupported，不把Fm适用误等同全部储能全额q。当前共同理论上界K_U假设下要求外部全控制区kmax≤K_U；异质性能上界场景unsupported，不静默使用不一致输入。新增两项scope guard测试，共11项纯测试，0native，原公式未改。
