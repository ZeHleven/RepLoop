# 网页试玩

开发、测试、启动和打包都从项目根目录执行。版本和验收结果见 [当前状态](../../docs/current-status.md)。

## 本地启动

本机已安装 Python 虚拟环境、前端依赖和 Docker 时：

```powershell
docker compose -f docker-compose.demo.yml up -d postgres
node miniapp/scripts/build-h5.mjs
.\.venv\Scripts\python.exe scripts/run_demo.py
```

打开 `http://127.0.0.1:8017`。独立数据库使用 `127.0.0.1:5547` 和 `fitnessagent_demo_postgres` 数据卷；旧服务的端口和数据卷保持原用途。按 Ctrl+C 停止网页服务，使用 `docker compose -f docker-compose.demo.yml stop` 停止演示数据库。

首次启动会创建被 Git 忽略的 `.env.demo.local`，生成独立登录密钥。默认模型 Key 为空、总额度为零，可以查看示例资料与训练记录。启用实时 AI 时，在这个文件填写 `DEEPSEEK_API_KEY`，并设置 `DEMO_TOTAL_BUDGET_NANO` 后重启；`1000000000` 表示应用费用账本的 1 元额度。这是保守核算值，不是供应商账单。所有访客和重置前后的请求共同消耗总额度。

## 行为与边界

- 点击进入创建独立用户，预置虚构资料、计划、最近四周历史和体重记录；数据日期沿用项目的 Asia/Shanghai 业务日历。
- 默认体验 24 小时、每位访客最多重置 5 次。重置会更换业务数据身份，保留原有效期与费用账本；旧凭证失效，进行中的旧回答不能进入新体验。
- 同一浏览器重试进入会恢复同一访客。服务端保存随机进入凭证的 SHA256；网络来源只保存带服务密钥的 HMAC，不记录原始 IP。
- 默认全站最多 200 位访客、同一网络来源每小时最多 30 位。反向代理后的多个访客可能共享来源，比赛时按实际人数调整 `DEMO_VISITS_PER_HOUR`；应用不信任任意 `X-Forwarded-For`。
- `DEMO_ENABLED` 默认关闭；启动演示服务要求独立数据库名称以 `_demo` 结尾。演示环境使用独立密钥和数据库；不接入真实用户数据库。
- 默认每位访客额度 `DEMO_USER_BUDGET_NANO=5000000000`；全站额度 `DEMO_TOTAL_BUDGET_NANO` 包括已花费和预留费用，重启、换账号和重置不清空。未知费用继续保留预留值。

## 打包与部署

```powershell
node miniapp/scripts/build-h5.mjs
.\.venv\Scripts\python.exe scripts/package_demo.py
```

产物位于 `deploy/demo/`，含后端、网页、Dockerfile、构建身份与文件校验清单，不包含本机配置或密钥。解压后从包根目录执行 `docker build -f deploy/demo/Dockerfile -t fitness-agent-demo .`。

CloudBase 等容器平台可以使用这个镜像。首次部署前准备独立 PostgreSQL（支持 vector）、运行迁移和基础种子导入；设置 `DATABASE_URL`、独立 `SECRET_KEY`、模型 Key、额度，以及 `AGENT_ASYNC_WORKER_ENABLED=true`。容器端口为 8000。网页和 `/api/v1` 同域，H5 使用 hash 路由；开启 HTTPS 后再分发链接。镜像中的默认 `DEMO_ENABLED=true` 仍受数据库名称检查约束。

本地构建不会自动上传或替换现有线上服务。费用与人数上限属于整个演示环境，不会每天自动清零。
