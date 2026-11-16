# 本地运行循练 RepLoop

本指南用于运行自己的开发环境。文档入口见 [项目文档](README.md)，源码、发布状态及当前待办统一见 [当前状态](current-status.md)。

## 主工作区与版本

现有本机项目使用 `C:\Users\12521\Documents\ChatGPT\FitnessAgent` 根目录；无需重新克隆或进入 `.tmp/` 中的历史候选。新克隆使用其自身根目录。先运行：

```powershell
python scripts/check_workspace.py
```

本机已安装的 Python 也可通过 `.\.venv\Scripts\python.exe` 调用。输出包含实际根目录、版本、分支、完整提交和 dirty 状态。`backend/VERSION` 是应用版本源；调整版本后执行 `python scripts/check_workspace.py --sync-version` 同步小程序声明。API、打包、小程序微信构建及 CI 会使用或核对该版本；发布状态只在 [当前状态](current-status.md) 记录。

旧候选与原始验收结果继续保留在 `.tmp/` 和 `artifacts/`，不作为日常开发或打包入口。Flutter 备用客户端及其依赖清理保留在 `frontend/`。

后端本地配置统一使用根目录 `.env`，生产配置及发布预检使用根目录 `.env.production`。两者都不提交 Git；已有配置不被示例覆盖。生产 Compose 是基础文件的覆盖层，只校验配置可运行 `docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml config --quiet`。

## 准备环境

- Git。
- Docker 与 Docker Compose，用于运行 PostgreSQL 和 FastAPI。
- Node.js，以及与 `miniapp/package.json` 中 `packageManager` 一致的 pnpm 版本。
- 微信开发者工具。
- 使用 AI 功能时，需要自己的 DeepSeek API Key；使用微信登录时，需要自己的小程序 AppID 与 AppSecret。

以下命令使用 PowerShell，适用于新克隆的仓库。如果已经有本地配置，请编辑原文件，不要用模板覆盖。

## 1. 获取源码并配置后端

```powershell
git clone https://github.com/ZeHleven/RepLoop.git
cd RepLoop
Copy-Item .env.example .env
```

编辑根目录 `.env`：

| 配置 | 说明 |
| --- | --- |
| `DATABASE_URL` | 本地 Docker 开发可使用模板中的容器地址 |
| `SECRET_KEY` | 替换为自己生成的随机密钥 |
| `DEEPSEEK_API_KEY` | 启用 AI 对话与相关 AI 接口时填写 |
| `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 使用微信登录时填写；AppSecret 只放在后端 |
| `RAG_ENABLED` | 初次运行保留 `false` |

未配置模型密钥时，可以先调试普通业务接口；需要模型的 AI 接口会提示服务未配置。邮箱注册和登录可用于本地开发，不依赖微信身份接入。

模板中的各领域 Proposal 功能开关默认关闭。如果需要体验 Agent 发起修改的流程，请阅读[部署包说明中的开关说明](../deploy/cloudbase/README.md)，按需逐项启用并验证。启用后仍需用户确认才能执行。

## 2. 启动后端

```powershell
docker compose up -d --build
docker compose ps -a
```

Compose 会先等待数据库就绪，再由 `bootstrap` 完成迁移、导入动作和食物数据，最后启动 API。首次构建需要下载镜像与依赖。

从旧版配置升级时，请移除本地 `.env` 中的 `REDIS_URL`；当前服务不再需要 Redis。

- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 存活检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- 就绪检查：[http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready)
- 本地 Caddy 代理：[http://127.0.0.1:8080](http://127.0.0.1:8080)

如果 API 尚未启动，先查看初始化与服务日志：

```powershell
docker compose logs bootstrap fastapi
```

## 3. 启动微信小程序开发构建

在新的终端中进入仓库的 `miniapp` 目录：

```powershell
cd miniapp
Copy-Item project.config.example.json project.config.json
```

创建 `miniapp/.env.development.local`，填入本地 API 地址：

```dotenv
TARO_APP_API_BASE_URL="http://127.0.0.1:8000/api/v1"
```

```powershell
pnpm install --frozen-lockfile
pnpm dev:weapp
```

在微信开发者工具中导入 `miniapp` 文件夹，编译产物目录由 `project.config.json` 指向 `dist/`。访问本机 HTTP 接口时，仅在本地开发设置中关闭合法域名与 HTTPS 校验。

模板中的 `touristappid` 用于本地预览。需要微信登录或真机调试时，按[小程序说明](../miniapp/README.md)配置自己的 AppID 与网络入口；手机上的 `127.0.0.1` 指向手机本身，不能直接访问电脑后端。

## 4. 修改与验证

小程序的主要入口：

| 需求 | 代码位置 |
| --- | --- |
| 训练过程、逐组记录、组间休息 | `miniapp/src/pages/workout-active/` |
| 个性化计划预览与修改 | `miniapp/src/pages/plan-builder/`、`plan-editor/` |
| 历史记录与进度 | `miniapp/src/pages/history/` |
| 饮食记录与汇总 | `miniapp/src/pages/nutrition/` |
| Agent 对话界面 | `miniapp/src/pages/agent/` |
| 个人资料与体重 | `miniapp/src/pages/me/`、`weight/` |

修改小程序后，在 `miniapp` 目录运行：

```powershell
pnpm typecheck
pnpm test:build-contract
pnpm build:weapp
```

同时在微信开发者工具中走一遍受影响的流程。涉及后端行为时，按改动范围运行 `backend/tests/` 中的相关测试；涉及 Agent 的设计与契约，请同时阅读 [Agent 文档](agent/README.md)。

后端依赖按用途安装：生产镜像使用 `backend/requirements.txt`；本地单元测试使用 `backend/requirements-dev.txt`；完整 CI 和 Agent 评测使用 `backend/requirements-evals.txt`，后两者会自动包含运行依赖。请在 Python 3.13 虚拟环境中安装对应文件，例如：

```powershell
python -m pip install -r backend/requirements-evals.txt
python -m pip check
```

数据库测试使用独立的本地 `*_test` 数据库，并通过 `TEST_DATABASE_URL` 和 `AGENT_QUERY_EVAL_DATABASE_URL` 指定；CI 中有完整的迁移和评测命令。

## 部署自己的版本

自行部署需要使用自己的微信小程序身份、数据库与模型服务配置，并自行承担相应服务费用和维护工作。后端部署产物的生成方式见[云托管部署包说明](../deploy/cloudbase/README.md)。

`.env`、AppSecret、模型 API Key、数据库密码和真实用户数据不应提交到 Git。公开分享自己的版本时，请使用配置模板和示例数据。
