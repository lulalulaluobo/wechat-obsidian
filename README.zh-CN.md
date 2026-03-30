# wechat-md-server

[English](README.md)

一个本地运行的 FastAPI 服务，用于把微信公众号和普通网页长文本链接转换为 Markdown，并同步到面向 Obsidian 的 Fast Note Sync。

## 运行方式

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

打开 `http://127.0.0.1:8765` 或 `http://<your-lan-ip>:8765`。

- 主页面：`http://127.0.0.1:8765/` 或 `http://<your-lan-ip>:8765/`
- 登录页：内置单账号登录
- 设置页：`http://127.0.0.1:8765/settings` 或 `http://<your-lan-ip>:8765/settings`
- 任务历史页：`http://127.0.0.1:8765/tasks` 或 `http://<your-lan-ip>:8765/tasks`

## Docker

推荐的容器基础镜像：

- `python:3.14-slim-bookworm`
- `amd64 / x86_64`
- 以 `docker-compose.yml` 作为主要部署入口

使用 Docker Compose 启动：

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

面向生产的 Compose：

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f
```

访问地址：

- `http://127.0.0.1:8765/login`
- `http://<your-lan-ip>:8765/login`

容器行为：

- 应用监听在 `0.0.0.0:8765`
- 运行时数据通过 `./data:/app/data` 持久化
- 运行时配置路径为 `/app/data/runtime-config.json`
- 临时工作输出目录为 `/app/data/workdir-output`

部署相关文件：

- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- `docker-compose.prod.yml`

镜像体积预估：

- 当前 slim 构建通常约为 `65MB - 80MB`
- 具体大小会受镜像元数据和 wheels 复用情况影响

为什么不选 Alpine：

- `Pillow` 和 `cryptography` 在 Debian slim 上更容易保持稳定
- 这个项目更受益于可预测的 wheels 和更容易排障的环境，而不是节省几十 MB

## 必需环境变量

运行时敏感配置依赖主密钥，没有它服务无法加载加密后的运行时配置。

- `WECHAT_MD_APP_MASTER_KEY`
- `WECHAT_MD_ADMIN_USERNAME`
- `WECHAT_MD_ADMIN_PASSWORD`

如果首次启动时省略 `WECHAT_MD_ADMIN_PASSWORD`，服务会自动生成一个随机初始密码，并在 stdout 中打印一次。

把 [.env.example](/path/to/wechat-md-server/.env.example) 复制到你的部署环境里，并替换所有占位值。

## 默认值

- FNS 目标目录：`00_Inbox/微信公众号`
- 图片模式：`wechat_hotlink`
- 运行时配置路径：`data/runtime-config.json`
- 内部工作目录根路径：`data/workdir/`
- 任务历史路径：`data/task-history.jsonl`

可选环境变量：

- `WECHAT_MD_RUNTIME_CONFIG_PATH`
- `WECHAT_MD_DEFAULT_OUTPUT_DIR`
- `WECHAT_MD_SESSION_COOKIE_SECURE`
- `WECHAT_MD_FNS_BASE_URL`
- `WECHAT_MD_FNS_TOKEN`
- `WECHAT_MD_FNS_VAULT`
- `WECHAT_MD_FNS_TARGET_DIR`
- `WECHAT_MD_IMAGE_MODE`
- `WECHAT_MD_IMAGE_STORAGE_PROVIDER`
- `WECHAT_MD_IMAGE_STORAGE_ENDPOINT`
- `WECHAT_MD_IMAGE_STORAGE_REGION`
- `WECHAT_MD_IMAGE_STORAGE_BUCKET`
- `WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID`
- `WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY`
- `WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE`
- `WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL`

## 当前行为

- Web UI 受登录保护。
- Session Cookie 可以通过 `WECHAT_MD_SESSION_COOKIE_SECURE=true` 开启 `Secure` 模式。
- 连续登录失败会触发限流。
- 敏感运行时值在写入 `runtime-config.json` 前会先加密：
  - FNS token
  - S3 secret access key
  - session secret
- 转换后的笔记会写入当前配置的 Fast Note Sync 目标。
- FNS 模式会使用内部临时工作目录，不会直接写入 Obsidian Inbox 路径。
- 图片处理由 `/settings` 全局控制：
  - `wechat_hotlink`：Markdown 中保留原始微信图片链接
  - `s3_hotlink`：把静态图片上传到兼容 S3 的对象存储，并使用 `public_base_url/object_key`
- 在 `s3_hotlink` 模式下，GIF 和 SVG 继续保留原始微信图片链接。
- 可选 AI 润色可以生成：
  - frontmatter
  - summary
  - tags
  - 模板块内容
  - 可选的 `content_polished` 正文
- 正文润色默认关闭，并且单篇/批量都支持单次覆盖。
- 设置页支持从 Clipper JSON 文件导入模板，并映射到当前解释器模板字段。
- 任务历史页支持按触发方式、输入源和状态查看最近任务。
- 失败任务支持单条重新转换，也支持勾选后批量重跑。
- 任务历史与运行时配置分离，历史数据单独持久化到 `task-history.jsonl`。

## 任务历史

- `/tasks` 提供独立的任务历史页。
- 每条记录包含：
  - 触发时间
  - 触发方式（网页端 / Telegram / 飞书）
  - 输入源类型（微信公众号 / 普通网页）
  - 任务笔记名称
  - 源文链接
  - 执行状态
- 失败任务可直接重新转换。
- 勾选多条任务后可批量重跑，无需重新转发或重新粘贴链接。

## AI 润色

- 内置 AI 工作流是可选增强，默认关闭。
- 当前支持的 Provider 类型：
  - `OpenAI Compatible`
  - `Anthropic`
  - `Gemini`
  - `Ollama`
  - `OpenRouter`
- 内置 Provider 为只读预设，同时也支持新增自定义 Provider。
- 可以维护多个模型，但运行时只使用当前选中的一个模型。
- `测试 AI 连通性` 会测试当前选中的 Provider/模型组合，不会真正执行文章转换。
- 解释器相关设置包括：
  - 上下文模板
  - 提示词模板
  - frontmatter 模板
  - body 模板
  - 可选额外正文补充块
  - 可选全文润色输出 `content_polished`

## 设置页

- `/settings` 提供服务端持久化的管理员设置页。
- 设置页包含概览卡片、FNS 连通性检测、图片模式选择和表单内联校验提示。
- FNS 配置支持从剪贴板导入，或粘贴如下 JSON 结构：
  - `api`
  - `apiToken`
  - `vault`
- 剪贴板导入只会填充表单，只有点击保存后才会真正持久化。
- 敏感字段在重新加载后会以掩码显示，不会以明文从设置 API 返回。
- S3 图片配置需要在设置页手工填写，不依赖 Obsidian 插件或 R2 配置文件。
- AI 区现在分为：
  - 当前模型选择
  - Provider 管理
  - 模型池管理
  - 解释器模板配置
- 单篇转换成功后会自动清空文章链接输入框。
- 批量任务创建成功后会清空多行链接框和文件选择。

## Bot 集成

- 已支持 Telegram Bot webhook 单篇转换。
- 已支持飞书 Bot webhook 单篇转换。
- 两类 Bot 入口都会：
  - 每条消息只接受一条链接
  - 支持公众号和普通网页链接
  - 先回执“已接收，开始转换”
  - 异步执行转换
  - 完成后回执标题、同步路径和图片模式
- 飞书 v1 当前支持：
  - 仅私聊
  - `open_id` 白名单（联调阶段可留空）
  - 开发者服务器 webhook 模式
- 如果飞书因为应用权限不足而发送回执失败，webhook 不会直接崩溃，而是记日志继续返回 `200`。

## 重置管理员密码

如果 `.env` 已经改过，但现有 `runtime-config.json` 中已经保存了管理员密码哈希，就不要指望 `.env` 自动覆盖，而应使用离线重置命令。

Python CLI：

```powershell
python -m app.cli.reset_admin_password --password "new-secret"
python -m app.cli.reset_admin_password --random
python -m app.cli.reset_admin_password --username admin --password "new-secret"
```

PowerShell 包装脚本：

```powershell
.\scripts\reset-admin-password.ps1 -Password "new-secret"
.\scripts\reset-admin-password.ps1 -Random
```

说明：

- 命令需要正确的 `WECHAT_MD_APP_MASTER_KEY`
- 重置密码时也会轮换 `session_secret`，因此已有登录会话会失效
- 这个命令只更新管理员凭据，不会修改 FNS 或 S3 配置

## VPS 部署

推荐目录结构：

```text
/opt/wechat-md-server/
├── .env
├── data/
│   ├── runtime-config.json
│   ├── workdir/
│   └── workdir-output/
├── docker-compose.yml
└── deploy/systemd/wechat-md-server.service.example
```

推荐部署步骤：

1. 把 `.env.example` 复制为 `.env`，并设置强随机的 `WECHAT_MD_APP_MASTER_KEY`
2. 设置 `WECHAT_MD_SESSION_COOKIE_SECURE=true`
3. 如有需要，先创建宿主机 `data/` 目录
4. 编辑 `docker-compose.prod.yml`，把占位的运行时密钥替换成真实值
5. 运行 `docker compose -f docker-compose.prod.yml pull` 和 `docker compose -f docker-compose.prod.yml up -d`
6. 生产版 compose 目前仍直接暴露 `8765`，因此可以直接通过 `http://<server-ip>:8765` 访问
7. 如果后面想改成只走反代，把端口绑定改回 loopback，并在前面加 Nginx 或 Caddy
8. 如果你不想容器化，仍可以使用 [wechat-md-server.service.example](/path/to/wechat-md-server/deploy/systemd/wechat-md-server.service.example) 里的 systemd 样例

推荐的反向代理边界：

- 只暴露 Web 服务入口
- 应用可以监听在 `0.0.0.0`，但应在主机防火墙或反代层限制暴露范围
- 让反向代理处理 HTTPS 终止
- 建议在代理层增加 HSTS 和 Host 限制

## 备份与恢复

需要备份：

- `data/runtime-config.json`
- 如果你选择保留成功任务的临时产物，也要备份 `data/workdir/`
- `.env` 文件，或者至少保存 `WECHAT_MD_APP_MASTER_KEY`

恢复流程：

1. 恢复项目文件并安装依赖
2. 恢复 `runtime-config.json`
3. 恢复完全相同的 `WECHAT_MD_APP_MASTER_KEY`
4. 启动服务

如果主密钥变了，之前加密的运行时敏感字段将无法解密。

## 开发说明

- 不要提交 `_integration_output/`、`_integration_output_v2/` 这类集成输出目录
- 不要提交 `.env` 或 `data/runtime-config.json`
- 如果你想在 Windows 本地使用固定启动入口，可以直接用 [start-server.ps1](/path/to/wechat-md-server/scripts/start-server.ps1)

## 下一步版本更新计划

从当前状态看，V3 已经足够日常使用。下一阶段更适合做的是运维与体验收口，而不是继续扩展核心抓取能力。

当前候选方向：

- 飞书加密事件解密支持
  - 目前飞书 webhook 在控制台清空 `Encrypt Key` 后已经可用
  - 如果后续要长期稳定使用飞书，建议补齐加密事件解密逻辑
- 飞书权限与联调文档
  - 把事件订阅、权限申请、白名单初始化和日志排障路径整理成一页明确手册
- Bot 回执体验优化
  - 当前 Telegram / 飞书回执都是纯文本
  - 后续可以升级成更清晰的结构化消息，但这不是阻塞项

建议的下一步：

- 先让 V3 在真实环境稳定运行一段时间
- 收集实际使用反馈
- 再决定优先做飞书加密支持，还是更细的运维文档
