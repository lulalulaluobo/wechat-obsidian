# wechat-md-server

一个面向 Obsidian 的本地服务，用来抓取微信公众号文章和普通网页长文，清洗后转换成 Markdown，并同步到 Obsidian。

项目提供 Web 管理界面，支持单篇转换、批量转换、任务历史、图片处理、可选 AI 润色，以及 Telegram / 飞书 Bot 接入。

## 项目简介

这个项目主要解决两个问题：

- 把微信公众号文章转成适合长期保存的 Markdown
- 把转换结果稳定同步到 Obsidian 笔记库

如果你的使用场景是“看到一篇公众号文章或网页长文，想尽快入库到 Obsidian”，这个项目就是为这个流程准备的。

## 核心功能

![转换中心主界面](docs/images/1775994257197.png)

- 支持微信公众号文章抓取与转换
- 支持普通网页文章抓取与转换
- 支持单篇和批量导入
- 支持任务历史记录与失败重跑
- 支持两种图片模式：
  - `wechat_hotlink`：保留原始微信图片链接
  - `s3_hotlink`：上传静态图片到兼容 S3 的对象存储
- 支持可选 AI 润色：
  - 摘要
  - 标签
  - Frontmatter
  - 正文补充块
  - 全文润色
- 支持 Telegram Bot / 飞书 Bot webhook 接入
- 支持登录保护和运行时敏感配置加密存储

## 依赖项目

这个项目和下面两个开源项目关系比较大：

### 1. `wechat-article-exporter`

项目在微信公众号文章抓取、导出链路的调研和实现思路上参考了这个开源项目：

- GitHub: `https://github.com/wechat-article/wechat-article-exporter`

### 2. `obsidian-fast-note-sync`

Obsidian 同步入库这部分，核心依赖的是 Fast Note Sync 对应的能力和配置：

- GitHub: `https://github.com/haierkeys/obsidian-fast-note-sync`

如果你希望把 Markdown 真正同步进 Obsidian，这部分配置是必需的。

## 快速开始

### 1. 准备环境变量

复制示例配置：

```bash
cp .env.example .env
```

至少需要确认这几个变量：

```env
WECHAT_MD_APP_MASTER_KEY=replace-with-a-long-random-secret
WECHAT_MD_ADMIN_USERNAME=admin
WECHAT_MD_ADMIN_PASSWORD=replace-with-a-strong-password
```

说明：

- `WECHAT_MD_APP_MASTER_KEY` 用于加密运行时敏感配置
- `WECHAT_MD_ADMIN_USERNAME` 和 `WECHAT_MD_ADMIN_PASSWORD` 是后台登录账号

### 2. 本地启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

启动后访问：

- `http://127.0.0.1:8765/login`

### 3. Docker Compose 启动（推荐）

**方式一：直接使用项目内的 compose 文件**

修改 `docker-compose.yml` 中的环境变量后启动：

```bash
docker compose up -d
```

**方式二：一键脚本部署（适合服务器生产环境）**

> 仅支持 Debian / Ubuntu，需要 root 权限。脚本会自动安装 Docker 并生成随机密钥。

```bash
curl -fsSL https://raw.githubusercontent.com/lulalulaluobo/wechat-md-server/main/deploy/install.sh | sudo bash -s -- install
```

脚本支持以下子命令：

```bash
sudo ./install.sh install    # 首次安装
sudo ./install.sh update     # 更新镜像（保留数据）
sudo ./install.sh status     # 查看服务状态
sudo ./install.sh logs       # 实时查看日志
sudo ./install.sh restart    # 重启服务
sudo ./install.sh uninstall  # 卸载（可选保留数据）
```

启动后访问：

- `http://服务器IP:8765/login`

## 快速使用教程

### 第一步：登录后台

打开 `http://127.0.0.1:8765/login`，使用 `.env` 中设置的管理员账号登录。

![登录页面](docs/images/1775994187555.png)

### 第二步：配置 Fast Note Sync

进入“设置”页面，填写 Fast Note Sync 相关信息：

- FNS 服务地址
- API Token
- Vault 名称
- 目标目录

默认目标目录为：

```text
00_Inbox/微信公众号
```

如果这一步没有配置完成，Markdown 虽然可以生成，但无法稳定同步到你的 Obsidian 仓库。

![设置页 - FNS 同步配置](docs/images/1775994382775.png)

### 第三步：选择图片处理模式

在设置页选择图片模式：

- `wechat_hotlink`：部署简单，适合先跑通流程
- `s3_hotlink`：适合追求图片长期可控和可迁移

![设置页 - 图片模式](docs/images/1775994409747.png)

### 第四步：开始转换

回到首页后可以：

- 粘贴单篇链接直接转换
- 批量粘贴多个链接
- 上传文本文件批量导入链接

转换成功后，文章会按照当前配置同步到 Obsidian 对应目录。

![同步结果 - Obsidian 笔记](docs/images/1775994576896.png)


### 第五步：查看任务结果

在任务历史页面可以查看：

- 当前任务状态
- 来源类型
- 触发方式
- 标题和原始链接
- 失败任务的重跑入口

## 支持的能力

### 微信公众号文章

适合作为主要输入源，转换后可直接沉淀为 Markdown 笔记。

### 普通网页文章

也支持普通网页长文提取。对于正文结构清晰的文章页，通常可以直接转换；对于强反爬、强前端渲染或登录后可见页面，成功率会下降。

### AI 润色

项目内置可选 AI 工作流，默认关闭。配置完成后可以生成：

- `summary`
- `tags`
- `frontmatter`
- `body_polish`
- `content_polished`

当前支持的 Provider 类型包括：

- OpenAI Compatible
- Anthropic
- Gemini
- Ollama
- OpenRouter

![AI 润色配置](docs/images/1775994440564.png)

### Bot 接入

项目支持：

- Telegram Bot
- 飞书 Bot

典型流程为：发送链接 -> 服务异步转换 -> Bot 回执结果。

![Telegram Bot 回执示例](docs/images/1775994505566.png)


## 目录结构

```text
.
├── app/
│   ├── api/
│   ├── cli/
│   ├── core/
│   ├── web/
│   ├── config.py
│   ├── services.py
│   └── main.py
├── deploy/
│   └── install.sh          # 一键安装脚本（Debian/Ubuntu）
├── docs/
├── scripts/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 常用命令

运行测试：

```bash
pytest
```

重置管理员密码：

```bash
python -m app.cli.reset_admin_password --password "new-secret"
python -m app.cli.reset_admin_password --random
```

## 使用说明与声明

- 本项目主要用于个人学习、技术研究和工作流实践
- 请仅在合法、合规、尊重版权的前提下使用
- 通过本项目获取和保存的文章内容，其版权归原作者或原权利人所有
- 如果你将本项目用于生产或公开服务，需要自行评估目标站点的服务条款、版权要求和合规风险

## 开源协议

本项目采用 MIT License 开源。
