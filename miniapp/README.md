# Fitness Agent 微信小程序

当前主客户端，基于 Taro 4.2.1、React 18 和 TypeScript。

从主工作区根目录的 `miniapp/` 开发和构建；[当前状态](../docs/current-status.md) 统一记录源码与发布身份。版本以 `backend/VERSION` 为准，`package.json` 通过 `python scripts/check_workspace.py --sync-version` 同步；微信构建会拒绝版本不一致或冲突的环境变量覆盖。

## 已接入能力

- 邮箱注册、登录、令牌刷新与退出。
- 微信一键登录：小程序获取临时 code，后端换取微信身份并签发业务 JWT。
- 首次登录六步引导：基本资料、目标、经验、训练偏好、健康筛查和饮食偏好。
- 基于目标、经验、地点、训练频率、时长和健康筛查生成首份个性化计划草稿。
- 保存前预览生成依据与安全提示；支持调整频率、时长、动作、组数、次数和休息时间。
- 确认保存计划，或一键保存并开始第一练。
- 开始或恢复训练。
- 逐组记录重量与次数。
- 自动带入上次同组重量和次数。
- 个人最佳展示与新纪录提示。
- 基于结束时间戳的组间休息倒计时，支持 `+30 秒` 和跳过；返回前台时自动校准。
- 完成或放弃训练。
- 完成训练时填写整体难度、RPE、精力、疼痛部位和补充感受。
- 根据实际完成组次、重量与主观反馈，自动调整下一练的建议重量、组数、次数、休息或动作。
- 训练完成页和历史记录展示调整结果与原因；疼痛风险优先降量或替换动作。
- 最近 8 周训练进度、周训练量和训练历史。
- Agent 对话：会话恢复、快捷问题、澄清承接、异步运行状态轮询和结构化训练卡片。

## 本地运行

1. 按仓库根目录 README 启动 FastAPI 后端。
2. 在后端 `.env` 配置微信小程序身份，AppSecret 绝不能写入 `miniapp`：

```dotenv
WECHAT_APP_ID="你的测试小程序 AppID"
WECHAT_APP_SECRET="你的测试小程序 AppSecret"
```

3. 确认 `.env.development` 中的 `TARO_APP_API_BASE_URL` 指向后端。
4. 创建本地微信项目配置。公开模板使用 `touristappid`，真实 AppID 只保留在被忽略的本地文件：

```powershell
Copy-Item project.config.example.json project.config.json
```

5. 安装依赖并启动监听构建：

```powershell
pnpm.cmd install --frozen-lockfile
pnpm.cmd dev:weapp
```

6. 在微信开发者工具中导入本目录。开发工具读取 `project.config.json`，小程序源码目录为 `dist`。

模板使用 `touristappid`，只用于本地预览。需要真机调试时，在不提交版本库的 `.env.development.local` 中设置：

```dotenv
TARO_APP_ID="你的测试小程序 AppID"
TARO_APP_API_BASE_URL="https://你的测试接口域名/api/v1"
```

开发配置暂时关闭域名校验以访问本机 API；正式发布必须使用已在微信公众平台配置的 HTTPS 域名。

## 验证

```powershell
pnpm typecheck
pnpm test:build-contract
pnpm build:weapp
```

`build:weapp` 会在 Taro 编译后校验产物：小程序 JavaScript 不得残留
`process.env`，Proposal 详情路由必须存在，正式项目配置必须以产物目录为根且
不得开启热重载。构建同时生成 `dist/build-info.json`，并把版本号、完整 Git
提交和源码 dirty 状态注入运行时；登录页底部和开发者工具控制台均可核对简短标识。
已登录用户也可在 Agent 页标题下直接核对该标识。

## 微信登录数据边界

- 小程序只把 `wx.login` 返回的一次性 code 交给 Fitness Agent 后端。
- AppSecret 只配置在后端；微信返回的 `session_key` 不下发给小程序，也不写入业务令牌。
- 数据库按 `AppID + OpenID` 保存微信身份；有 UnionID 时可自动关联同一开放平台下的多个小程序身份。
- 当前不会读取微信头像、昵称、手机号或通讯录。
- 健康筛查草稿只保存在当前页面内，提交前不会写入小程序本地存储。
- 个性化计划草稿不会直接落库；用户确认时，后端会再次校验动作状态、地点器械与伤病限制。

## 自建部署清单

- 在微信公众平台申请自己的 AppID，并只在后端托管平台保存 AppSecret。
- 复制 `.env.production.example` 为 `.env.production`（或使用优先级更高的
  `.env.production.local`），填写自己的 CloudBase 环境和服务名。
- 配置微信平台允许的 HTTPS 域名，完成真机网络、弱网、前后台切换和授权失败测试。
- 根据实际主体和类目准备隐私协议、用户信息处理说明与平台审核材料。
- 生产发布前重新运行类型检查、微信构建和完整手机验收。
- 使用仓库根目录 `scripts/upload_experience.ps1` 上传时，脚本会将微信版本号
  注入构建，并拒绝提交不匹配或源码未提交的产物。
