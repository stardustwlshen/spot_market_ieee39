# 私有云：四川 IEEE39 研究模拟

这是托管配置，不是已部署的访问地址。镜像运行原有 Streamlit / Python / HiGHS，能够提交新算例；不带本地任务档案、广东旧引擎、实网数据或登录凭据。结果仍按四川规则标识归档，不用于正式结算。

在支持 Linux 容器的私有主机中，从 `guangdong_market/program` 构建并启动：

```sh
docker build -f Dockerfile.sichuan -t sichuan-ieee39:test .
docker volume create sichuan-ieee39-runs
docker run --name sichuan-ieee39 -d --restart unless-stopped \
  --publish 127.0.0.1:8501:8501 \
  --mount type=volume,source=sichuan-ieee39-runs,target=/opt/sichuan/runs_sichuan_v5_consultation \
  sichuan-ieee39:test
curl --fail http://127.0.0.1:8501/_stcore/health
```

- 这是**单实例、单信任域**服务。Streamlit 本身不验证访客身份，且同一实例内的应用会列出同一结果卷。只通过已有的、已授权的私有身份入口（HTTPS，支持 WebSocket）对接本人，或用受控的私有隧道；严禁把容器端口直接发布到公网。没有经过验证的身份入口，就不要开放。外部访问实际使用入口生成的私有 URL，不使用这里的 `127.0.0.1`。
- 镜像内服务监听容器网卡，示例只映射主机回环。若平台要求动态 `PORT`，在容器环境中设定，并让平台健康检查访问对应端口的 `/_stcore/health`。没有可信代理时不要禁用 Streamlit 的 CORS / XSRF 检查。
- 持久卷挂在 `/opt/sichuan/runs_sichuan_v5_consultation`；容器以 UID/GID 10001 运行。若平台的卷不自动继承权限，应在主机配置初始化可写权限；不要改为 root，也不要挂载包含其他人的档案或凭据。更新镜像时复用并备份同一卷；不要第一次部署就导入本地历史数据。
- 健康检查只说明网页进程在线。上线验收应从本人私有入口完成一次 4/96 出清、重启实例后只读打开结果，并确认未授权会话不能访问页面或 WebSocket。在私有托管和身份入口通过验收之前，不把本配置当作已经可远程使用。
