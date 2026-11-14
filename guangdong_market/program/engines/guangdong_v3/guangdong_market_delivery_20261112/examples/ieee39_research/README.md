# 已验R56研究输入副本

完整命令（会构造大模型，不默认执行）：`python market.py prepare-scuc --input examples/ieee39_research/bundle.json --output ./prepared-ieee39`。四个显式输入已齐全，无需追索旧目录；相对bundle路径。

case/policy逐字复制，98grid和AGC由冻结v2.fixture()原样提取。ORIGINS记录旧来源及本地hash。case中原历史绝对路径仅来源声明，未被改写；新builder使用显式值，不依赖旧raw解恢复。原400MW等数值未变，不补资格。AGC仍显式合成热机各10MW、D日合计50MW研究设定；不赋予储能资格。

此为uniform-event研究fixture，不是真实申报/运营数据，不称正式完整三态模型。保留48h物理序列、98点权重、合成最低费等原假设。只进行codec加载、horizon重建与R56 validate合同预检：576物理格/98市场点通过；未大build、未native。详见LOAD_CONTRACT_CHECK.json。
