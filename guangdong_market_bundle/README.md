# 广东省内市场研究程序与24小时研究结果（2026-11-14）

这是已交付完整程序包的无损分块副本。为适配上传通路，GitHub 暂以分块保存；解包后有完整可编辑源码、必要输入及研究结果，不覆盖仓库原有程序。

## 解包

克隆/下载本仓库后，在仓库根目录执行（建议 Python 3.12）：

```sh
python guangdong_market_bundle/restore.py
```

脚本核验每一块及整个程序包的 SHA256 后还原 `guangdong_market`。若该目录已经存在则拒绝覆盖。Windows 可用 `py -3.12` 替代 `python`。

## 安装与启动

```sh
cd guangdong_market/program
python -m venv .venv
# Windows: .venv\Scripts\python.exe -m pip install -r requirements.txt
# Windows: .venv\Scripts\python.exe -m streamlit run app/主页.py
# macOS/Linux:
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app/主页.py
```

完整说明见解包后的 `guangdong_market/使用说明.md`。随包全天图表结果在 `guangdong_market/portable_results`。

## 验证与限制

已验证包与原交付文件一致、完整性门禁和首页加载；未对这次发布做 Windows/macOS 冷安装或新的全天求解验收。MIP 配置为1%、每次求解上限3600秒，历史结果保留原参数。

这是研究仿真，不是正式市场平台或安全调度结果：末季度价格为事后研究价；交流安全未通过；真实成本/辅助服务输入不完整时利润为部分结果。上传本身不意味着模型合规、安全或结算验收通过。
