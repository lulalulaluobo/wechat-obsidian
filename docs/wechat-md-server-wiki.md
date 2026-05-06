---
title: wechat-md-server 项目 Wiki
note_type: project-wiki
source_type: own-project
repo_path: wechat-md-server
tags:
  - project-wiki
  - obsidian
  - wechat
  - markdown
summary: 面向编程新手的 wechat-md-server 项目完整解读，覆盖全部模块、数据流、架构设计和可复用机制
created: 2026-05-06
updated: 2026-05-06
---

# wechat-md-server 项目 Wiki

## AI 接手摘要

- 项目定位：把微信公众号文章和普通网页转成 Markdown，同步到 Obsidian 笔记库的本地服务
- 核心技术栈：Python + FastAPI + SQLite + 原生 HTML/JS 前端
- 核心模块：core/pipeline.py（文章处理管线）、services.py（业务编排）、ai_adapters + ai_polish（AI 润色）
- 最值得复用的机制：HTML→Markdown 清洗管线、多 Provider AI 适配、分层配置系统
- 推荐深挖方向：pipeline.py 的清洗逻辑、AI 润色的模板系统、Bot 接入模式
- 值得调用的子 Skill：skill-brick（操作指南型）

---

## 1. 项目一句话定位

> 这是一个用于把微信公众号文章和普通网页转成 Markdown 并同步到 Obsidian 的本地 Web 服务，核心价值是完成"看到文章 → 一键入库"的完整流程。

---

## 2. 项目解决的问题 / 使用场景

**它解决什么问题：**

- 微信公众号文章没有导出功能，长期保存困难
- 网页文章的 HTML 格式不适合在 Obsidian 中阅读
- 手动复制粘贴格式混乱、图片丢失、噪音内容多

**面向什么用户：**

- 使用 Obsidian 做知识管理的个人用户
- 想把微信阅读内容沉淀为笔记的人
- NAS / 本地服务器自部署爱好者

**典型使用场景：**

1. 在手机上看到一篇公众号好文章 → 复制链接发给 Telegram Bot → 自动转成 Markdown 入库 Obsidian
2. 在电脑上浏览到一篇好文章 → 粘贴链接到 Web 管理界面 → 一键转换同步
3. 关注了几个优质公众号 → 配置自动同步 → 定时拉取新文章批量入库

**为什么这个问题值得被解决：**

微信内容是中文互联网最重要的信息源之一，但它的封闭生态导致内容难以被二次利用。这个项目打通了"微信内容 → 结构化 Markdown → Obsidian 知识库"的链路。

---

## 3. 技术栈分析

| 类型 | 技术 / 工具 | 作用 | 证据来源 |
| --- | --- | --- | --- |
| 语言 | Python 3.14 | 主要开发语言 | Dockerfile |
| 后端框架 | FastAPI | 提供 REST API 和 Web 页面 | requirements.txt, main.py |
| 服务器 | Uvicorn | ASGI 服务器，运行 FastAPI 应用 | requirements.txt, Dockerfile |
| 数据存储 | SQLite | 存储文章、任务、用户、同步源等数据 | sync_db.py, 根目录 .sqlite3 文件 |
| 配置存储 | JSON + 环境变量 | runtime-config.json 存运行时配置，.env 存启动参数 | config.py |
| HTTP 请求 | requests / httpx | 抓取网页、调用外部 API | requirements.txt |
| HTML 解析 | 正则表达式为主 + BeautifulSoup4 + readability-lxml | 解析微信文章 HTML、提取正文 | pipeline.py, content_sources.py |
| 图片处理 | Pillow | 压缩图片为 WebP 格式 | requirements.txt, pipeline.py |
| 对象存储 | S3 兼容协议（自实现签名） | 上传图片到 S3 图床 | pipeline.py S3Uploader |
| 加密 | cryptography (Fernet) | 加密敏感配置（token、密码等） | auth.py, requirements.txt |
| 密码哈希 | PBKDF2-SHA256 | 管理员密码存储 | auth.py |
| AI 调用 | OpenAI Compatible API | 多 Provider 统一接口调用 AI | ai_adapters.py |
| 飞书 SDK | lark-oapi | 飞书长连接 Bot | requirements.txt |
| 搜索 | 搜狗微信搜索（爬虫） | 按关键词搜索公众号文章 | search/sogou_weixin.py |
| 前端 | 原生 HTML/JS | Web 管理界面（无框架） | app/web/ |
| 部署 | Docker Compose | 容器化部署 | docker-compose.yml |
| 进程隔离 | multiprocessing (spawn) | 单篇转换的硬超时保护 | services.py |

**技术栈特点：**

- **轻量**：没有用 ORM、没有用前端框架、没有用消息队列，依赖很少
- **适合个人项目**：SQLite 单文件数据库，Docker 一键部署
- **自包含**：S3 签名是手写的，不依赖 AWS SDK

---

## 4. 项目目录结构

| 路径 | 职责 | 重要程度 | 是否深挖 |
| --- | --- | --- | --- |
| `app/main.py` | FastAPI 应用入口，路由注册，Lifespan 管理 | 高 | 是 |
| `app/core/pipeline.py` | 核心文章处理管线：抓取→清洗→图片→Markdown | 高 | 是 |
| `app/services.py` | 业务编排中心：所有业务逻辑的汇总点 | 高 | 是 |
| `app/config.py` | 配置系统：运行时配置读写、Settings 定义 | 高 | 是 |
| `app/api/routes.py` | API 路由：所有 REST 端点 | 中 | 是 |
| `app/ai_adapters.py` | AI Provider 适配层 | 中 | 是 |
| `app/ai_polish.py` | AI 润色：模板渲染和内容处理 | 中 | 是 |
| `app/auth.py` | 认证：密码哈希、Session、加密 | 中 | 是 |
| `app/content_sources.py` | 内容源检测和文章抓取 | 中 | 是 |
| `app/source_cache.py` | 源缓存：避免重复抓取 | 低 | 是 |
| `app/bot_workers.py` | Bot 后台工作线程 | 低 | 是 |
| `app/scheduler.py` | 定时任务调度器 | 低 | 是 |
| `app/wechat_sync.py` | 微信公众号 API 交互 | 中 | 是 |
| `app/sync_db.py` | SQLite 数据库操作层 | 中 | 是 |
| `app/task_history.py` | JSONL 格式任务历史 | 低 | 是 |
| `app/search/sogou_weixin.py` | 搜狗微信搜索 | 低 | 是 |
| `app/web/` | 前端 HTML/JS 页面 | 低 | 是 |
| `app/cli/reset_admin_password.py` | CLI 工具：重置管理员密码 | 低 | 否 |
| `deploy/install.sh` | 一键部署脚本 | 低 | 否 |
| `Dockerfile` | Docker 镜像构建 | 低 | 是 |
| `tests/` | 测试 | 低 | 否 |

---

## 5. 核心模块拆解

### 5.1 main.py — 应用入口

#### 模块职责

这是整个程序的入口点。它做三件事：
1. 创建 FastAPI 应用
2. 注册路由和静态文件
3. 在 Lifespan 中启动/停止后台服务（调度器、Bot 接收器）

#### 工作机制

```python
# 启动时：
@asynccontextmanager
async def lifespan(_: FastAPI):
    start_scheduler()      # 启动定时同步调度器
    start_bot_receivers()  # 启动 Telegram polling / 飞书长连接
    yield
    stop_bot_receivers()   # 关闭时停止 Bot
    stop_scheduler()       # 关闭调度器
```

应用启动后：
- 注册所有 API 路由（来自 `api/routes.py`）
- 挂载 `app/web/` 目录作为静态文件和 HTML 页面
- 添加安全响应头中间件（X-Frame-Options、X-Content-Type-Options 等）
- 所有页面（除了 `/login`）都需要先通过 Session Cookie 认证

#### 输入与输出

```text
输入：HTTP 请求（浏览器或 API 调用）
输出：HTML 页面或 JSON API 响应
```

#### 与其他模块的关系

```text
main.py
├→ api/routes.py（路由定义）
├→ bot_workers.py（后台 Bot 线程）
├→ scheduler.py（后台调度线程）
└→ app/web/（前端静态文件）
```

#### 可迁移启发

- **Lifespan 模式**：FastAPI 的 `lifespan` 上下文管理器是管理后台服务的标准方式，可以迁移到任何 FastAPI 项目
- **安全头中间件**：添加安全响应头的做法是 Web 安全的基本实践

---

### 5.2 core/pipeline.py — 核心文章处理管线

#### 模块职责

这是整个项目最核心的模块（约 1566 行）。它负责：

1. **抓取**微信文章 HTML
2. **提取**标题、作者、公众号名、正文内容
3. **清洗** HTML（去除 script、style、空 div、1px 追踪图片等）
4. **处理图片**（下载、压缩为 WebP、上传 S3 或保留微信原链）
5. **将 HTML 转为 Markdown**（自写 HTMLParser，不依赖第三方转换库）
6. **格式化 Markdown**（去噪、去推广、规范化标题层级、修复表格）

#### 工作机制

整个处理流程是一条管线（Pipeline）：

```text
URL
→ WeChatArticlePipeline.fetch_html()        # 抓取 HTML
→ WeChatArticlePipeline.extract_article()    # 提取文章数据
→ convert_article_to_markdown()              # HTML → Markdown
│  ├→ HTMLToMarkdownParser                   # 自写 HTML 解析器
│  └→ MarkdownImageDownloader                # 图片处理（下载/上传/保留）
→ format_markdown()                           # Markdown 格式化
│  ├→ 去除公众号噪音行
│  ├→ 去除推广区块和联系方式
│  ├→ 规范标题层级（不允许跳级）
│  ├→ 修复表格分隔符
│  └→ 规范化空行
→ 写入 .md 文件
```

**关键设计点：**

- **不依赖 markdown 转换库**：项目自己写了一个 `HTMLToMarkdownParser`（继承 Python 标准库的 `HTMLParser`），而不是用 `markdownify` 等库。原因可能是微信 HTML 结构特殊，需要更精细的控制
- **图片双模式**：
  - `wechat_hotlink`：直接保留微信图片 URL（简单，但链接可能过期）
  - `s3_hotlink`：下载图片 → Pillow 压缩为 WebP → 上传到 S3 → 替换为 S3 URL（可控，但需要配置 S3）
- **S3 签名自实现**：`S3Uploader` 自己实现了 AWS4-HMAC-SHA256 签名，没有用 boto3，让依赖更轻
- **噪音去除规则丰富**：内置了多种噪音识别模式（公众号噪音行、推广关键词、联系方式、作者自我介绍等）

#### 输入与输出

```text
输入：一个 URL（微信文章或普通网页）
输出：一个文件夹，包含 .md 文件和可选的 .html 文件
```

#### 与其他模块的关系

```text
pipeline.py
↑ 被 content_sources.py 调用（微信文章走这个管线）
↑ 被 services.py 调用（run_pipeline 入口）
→ 无外部模块依赖（自包含），只依赖 requests 和 Pillow
```

#### 可迁移启发

- **"HTML→Markdown" 清洗管线**是一个通用能力，可以迁移到任何需要网页内容转 Markdown 的项目
- **S3 签名自实现**避免了 AWS SDK 的重依赖，适合轻量项目
- **噪音去除规则**是微信文章特有的，但"结构化噪音去除"的思路可以迁移

---

### 5.3 services.py — 业务编排中心

#### 模块职责

这是整个项目的"大脑"（约 2520 行）。它负责：

1. **单篇转换**：接收 URL → 调用管线 → AI 润色 → 同步输出
2. **批量转换**：JobStore 管理批量任务的状态和进度
3. **Bot 处理**：Telegram 和飞书消息的接收、解析、转换、回执
4. **同步源管理**：公众号搜索、添加同步源、拉取文章列表
5. **文章入库**：从同步源选择文章 → 批量清洗入库
6. **配置管理**：读取和暴露当前配置状态
7. **用户管理**：登录认证、密码修改

#### 工作机制

核心转换流程（`_run_single_conversion`）：

```text
1. _prepare_conversion_tracking()  # 创建任务跟踪记录
2. detect_source_type()            # 判断是微信还是普通网页
3. fetch_article_from_url()        # 抓取文章（带缓存）
4. run_pipeline()                  # 调用 pipeline.py 转换
5. apply_ai_polish_to_result()     # AI 润色（如果启用）
6. sync_result_to_output()         # 同步到 FNS 或本地
7. 记录执行结果到数据库
```

**进程隔离模式：**

```text
如果 single_conversion_isolation_enabled = True：
  → 用 multiprocessing.Process 启动一个子进程来执行转换
  → 设定硬超时（默认 180 秒）
  → 超时后 kill 子进程，防止卡死
```

这个设计很重要——因为抓取外部网页可能因为网络问题卡住，进程隔离保护了主服务不会因为单个任务挂掉。

#### 输入与输出

```text
输入：URL + 配置参数（AI 是否启用、输出目标等）
输出：转换结果字典（状态、文件路径、同步信息、AI 润色结果）
```

#### 与其他模块的关系

```text
services.py 几乎调用了所有其他模块：
→ config.py（读取配置）
→ content_sources.py（抓取文章）
→ core/pipeline.py（转换管线）
→ ai_polish.py（AI 润色）
→ sync_db.py（数据库操作）
→ task_history.py（任务历史）
→ wechat_sync.py（公众号 API）
→ auth.py（密码验证）
→ bot_workers.py 被它间接调用（通过 services 中的 Bot 处理函数）
```

#### 可迁移启发

- **进程隔离执行模式**：用 `multiprocessing` spawn 子进程执行可能卡住的任务，加硬超时保护主服务，这个模式可以迁移到任何需要执行不可信任务的项目
- **JobStore 批量任务模式**：简单的内存任务队列 + ThreadPoolExecutor，适合轻量级批量处理

---

### 5.4 config.py — 配置系统

#### 模块职责

管理项目的全部配置（约 1934 行）。它提供：

1. **Settings dataclass**：所有配置项的类型安全定义
2. **运行时配置文件**：runtime-config.json 的读写
3. **分层配置**：环境变量 > JSON 文件 > 默认值
4. **敏感字段加密**：所有 token、密码用 Fernet 加密后存储
5. **配置导入/导出**：支持 JSON 格式导入导出全部设置
6. **配置验证**：写入前检查必填字段和格式

#### 工作机制

配置分三层：

```text
第一层：环境变量（WECHAT_MD_*）
  ↓ 覆盖
第二层：runtime-config.json（加密存储敏感字段）
  ↓ 覆盖
第三层：代码中的默认值
```

配置文件结构：

```json
{
  "auth": {
    "user": { "username": "...", "password_hash": "..." },
    "session_secret_encrypted": "enc::..."
  },
  "user_settings": {
    "fns_base_url": "...",
    "image_mode": "wechat_hotlink",
    "image_storage": { ... },
    "telegram": { ... },
    "feishu": { ... },
    "wechat_mp": { ... },
    "ai": { "providers": [...], "models": [...], "selected_model_id": "..." },
    ...
  }
}
```

敏感字段（token、密码等）在 JSON 文件中以 `enc::` 前缀 + Fernet 加密存储，读取时用 `WECHAT_MD_APP_MASTER_KEY` 环境变量解密。

#### 与其他模块的关系

```text
config.py 被几乎所有模块调用
→ auth.py（加密/解密函数）
← services.py、routes.py 等通过 get_settings() 读取配置
```

#### 可迁移启发

- **三层配置 + 敏感字段加密**是一个完整的个人项目配置方案
- **Settings dataclass + property 验证**是 Python 配置管理的清晰模式

---

### 5.5 api/routes.py — API 路由

#### 模块职责

定义所有 REST API 端点，是前端和后端之间的桥梁。

#### 主要端点分组

```text
认证：
  POST /api/login       — 登录
  POST /api/logout      — 登出

文章转换：
  POST /api/convert     — 单篇转换
  POST /api/batch       — 批量转换
  GET  /api/jobs/{id}   — 查询批量任务状态

设置管理：
  GET  /api/settings    — 获取配置
  POST /api/settings    — 保存配置
  GET  /api/fns/status  — 检查 FNS 连接状态
  POST /api/settings/test-ai — 测试 AI 连通性

同步源管理（公众号自动同步）：
  GET  /api/sync/sources       — 列出同步源
  POST /api/sync/sources       — 创建同步源
  DELETE /api/sync/sources/{id} — 删除同步源
  POST /api/sync/sources/{id}/sync — 触发同步

文章库：
  GET  /api/sync/articles      — 列出文章
  POST /api/sync/articles/ingest — 提交文章入库
  DELETE /api/sync/articles    — 删除文章

公众号后台：
  POST /api/wechat-mp/login/start    — 开始扫码登录
  GET  /api/wechat-mp/login/status/{id} — 查询扫码状态
  POST /api/wechat-mp/login/confirm/{id} — 确认登录
  GET  /api/wechat-mp/login/status   — 检查登录状态
  POST /api/wechat-mp/search         — 搜索公众号

搜索：
  POST /api/search/sogou     — 搜狗微信搜索
  GET  /api/search/history   — 搜索历史

Bot Webhook：
  POST /api/integrations/telegram/webhook — Telegram Webhook 回调
  POST /api/integrations/feishu/webhook   — 飞书 Webhook 回调

任务：
  GET  /api/tasks          — 任务列表
  POST /api/tasks/{id}/rerun — 重跑失败任务

配置导入导出：
  GET  /api/settings/export  — 导出设置
  POST /api/settings/import/preview — 预览导入
  POST /api/settings/import  — 执行导入
```

#### 可迁移启发

- REST API 的端点组织方式可以参考：按功能分组、统一的 `/api/` 前缀
- 文件上传、配置导入导出的 API 设计模式

---

### 5.6 ai_adapters.py — AI 适配层

#### 模块职责

统一封装多个 AI 服务商的 API 调用，让上层代码不需要关心具体是哪个 Provider。

#### 工作机制

```text
request_ai_completion(provider, model, messages)
  ↓ 根据 provider.type 分发：
  ├→ _request_openai_compatible()  # OpenAI / DeepSeek / OpenRouter / 自定义
  ├→ _request_anthropic()          # Anthropic Claude
  ├→ _request_gemini()             # Google Gemini
  └→ _request_ollama()             # 本地 Ollama
```

每个 Provider 的请求格式不同，但统一返回一个包含 AI 回复的字典。

还有配套的响应解析函数 `extract_completion_text()` 和 `extract_completion_preview()`，负责从不同 Provider 的响应格式中提取文本内容。

#### 支持的 Provider

| Provider | 类型标识 | 特点 |
| --- | --- | --- |
| OpenAI Compatible | `openai_compatible` | 最通用的类型，DeepSeek 也走这个 |
| Anthropic | `anthropic` | Claude 系列 |
| Gemini | `gemini` | Google AI |
| Ollama | `ollama` | 本地部署 |
| OpenRouter | `openrouter` | 聚合多种模型 |
| DeepSeek | 归入 `openai_compatible` | 只是指向 DeepSeek 的 base_url |

#### 可迁移启发

- **适配器模式（Adapter Pattern）**：这是经典的适配器设计模式——定义统一接口，为每种具体实现写适配器。可以迁移到任何需要对接多个外部服务的场景

---

### 5.7 ai_polish.py — AI 润色工作流

#### 模块职责

用 AI 对转换后的 Markdown 做增强处理。支持三个层次的润色：

1. **解释器调用**：生成 summary、tags、my_understand、body_polish 等变量
2. **模板渲染**：把变量填入 frontmatter 模板和 body 模板
3. **正文润色**：可选地让 AI 对整篇正文做重新排版

#### 工作机制

```text
apply_ai_polish_to_markdown()
  ↓
1. 读取 Markdown 文件
  ↓
2. request_interpreter_variables()      # 第一次 AI 调用
   → 把标题、作者、URL、正文发给 AI
   → AI 返回 JSON：{ summary, tags, my_understand, body_polish }
  ↓
3. render_template(frontmatter_template, variables)  # 渲染 frontmatter
   render_template(body_template, variables)          # 渲染 body
  ↓
4. 如果启用正文润色：
   request_polished_content()           # 第二次 AI 调用
   → 把正文发给 AI，让它重新排版
  ↓
5. 组装最终 Markdown：
   frontmatter + body（包含润色后的正文）
  ↓
6. 写回文件
```

**模板变量系统**：模板中使用 `{{变量名}}` 占位符，如：

```yaml
# frontmatter 模板
---
title: {{title}}
summary: {{summary}}
tags: {{tags}}
---
```

```markdown
# body 模板
> [!summary] 一句话总结
> {{summary}}

---

> [!tip] 我的理解
> {{my_understand}}

{{body_polish}}
```

#### 可迁移启发

- **模板驱动的 AI 润色工作流**是一个完整的"AI 增强内容"模式：定义模板 → AI 生成变量 → 渲染模板 → 输出结果
- 这个模式可以迁移到任何需要用 AI 增强笔记、文档或内容的场景

---

### 5.8 auth.py — 认证系统

#### 模块职责

提供安全的用户认证和敏感数据加密。

#### 工作机制

```text
密码存储：
  hash_password(password)
  → 生成随机 salt
  → PBKDF2-SHA256 迭代 200,000 次
  → 存储格式：pbkdf2_sha256$200000$salt$digest

Session 验证：
  build_session_token(username, password_hash, session_secret)
  → HMAC-SHA256 签名
  → Cookie 格式：username:signature

敏感配置加密：
  encrypt_secret(value)
  → 用 WECHAT_MD_APP_MASTER_KEY 派生 Fernet 密钥
  → 加密后存储为 "enc::base64_token"

登录保护：
  check_login_allowed(identifier)
  → 10 分钟窗口内允许 5 次失败
  → 超过后锁定 15 分钟
```

#### 可迁移启发

- PBKDF2 + Fernet 加密是 Python 项目中安全存储密码和敏感配置的标准方案
- 登录频率限制（滑动窗口 + 锁定）是防暴力破解的基本实践

---

### 5.9 content_sources.py — 内容源检测与抓取

#### 模块职责

判断一个 URL 是什么类型的内容，并调用对应的抓取逻辑。

#### 工作机制

```text
fetch_article_from_url(url)
  ↓
1. detect_source_type(url)
   → mp.weixin.qq.com → "wechat"
   → 其他有效域名 → "web"
   → zhihu.com → 抛出不支持错误
  ↓
2. load_cached_source(url)    # 先查缓存
   → 如果缓存命中 → 直接返回
  ↓
3. 如果是微信文章：
   → WeChatArticlePipeline 抓取和解析
  ↓
4. 如果是普通网页：
   → 用 readability-lxml 提取正文
   → 用 BeautifulSoup 清理 HTML
   → 提取标题和作者
  ↓
5. write_source_cache()        # 写入缓存
```

普通网页的处理比微信文章简单：用 `readability` 库提取正文区域，再用 BeautifulSoup 去除多余标签。

#### 可迁移启发

- **URL 检测 → 分发处理** 是内容处理项目的常见入口模式
- **缓存层**避免重复抓取，SHA256 做 cache key 是标准做法

---

### 5.10 source_cache.py — 源缓存

#### 模块职责

把抓取过的文章 HTML 缓存到本地文件系统，避免重复网络请求。

#### 工作机制

```text
缓存目录结构：
  source-cache/
    └── {SHA256 hash 前两位}/
        └── {完整 SHA256 hash}/
            ├── source.html      # 原始 HTML
            ├── article.json     # 解析后的文章数据
            └── diagnostics.json # 抓取诊断信息

写入：write_source_cache(url, article, source_html, ...)
读取：load_cached_source(url) → 缓存命中返回数据，未命中返回 None
```

#### 可迁移启发

- **基于内容哈希的目录缓存**是一种简单有效的文件系统缓存模式

---

### 5.11 bot_workers.py — Bot 后台工作线程

#### 模块职责

在后台线程中运行 Bot 接收器，持续监听新消息。

#### 工作机制

```text
Telegram Polling 模式：
  后台线程循环 {
    调用 Telegram getUpdates API（长轮询）
    → 收到新消息
    → 调用 process_telegram_polling_update()
    → 触发文章转换
    → 发送回执消息
  }

飞书长连接模式：
  后台线程 {
    使用 lark-oapi SDK 建立 WebSocket 连接
    → 注册消息回调
    → 收到新消息
    → 调用 process_feishu_long_connection_event()
    → 触发文章转换
    → 发送回执消息
  }
```

两种模式的共同点：都是在后台线程中持续运行，收到消息后异步处理。

#### 可迁移启发

- **后台守护线程 + 事件回调**是轻量级消息接收的标准 Python 模式
- Telegram polling 和飞书长连接是两种不需要公网 IP 的 Bot 接入方式

---

### 5.12 scheduler.py — 定时任务调度器

#### 模块职责

定时执行两个任务：
1. **source_sync_schedule**：从公众号同步源拉取新文章
2. **article_ingest_schedule**：把待处理文章批量入库

#### 工作机制

```text
后台线程循环（每 30 秒检查一次）{
  读取调度配置
  → 检查是否到了执行时间（基于 cron 表达式或间隔）
  → 检查是否被暂停
  → 用线程锁防止重复执行
  → 执行对应任务
}
```

配置存储在数据库中（sync_db.py 的 scheduler_configs 表），可以通过 Web 界面修改。

#### 可迁移启发

- **30 秒轮询 + 线程锁 + 数据库配置**是一种简单但实用的定时任务方案，不需要 Celery 或 APScheduler
- 适合个人项目

---

### 5.13 wechat_sync.py — 微信公众号 API

#### 模块职责

封装微信公众号后台的 API 调用：
- 搜索公众号
- 拉取公众号文章列表
- 检查登录状态

#### 工作机制

```text
WechatMPClient：
  - 需要 token + cookie（通过扫码登录获取）
  - 调用 mp.weixin.qq.com 的内部 API
  - 搜索：search_accounts(keyword)
  - 文章列表：fetch_articles(fakeid, begin, size)
  - 登录检查：check_login_status()
```

token 和 cookie 通过 Web 界面的扫码登录功能获取，加密后存储在数据库中。

#### 可迁移启发

- 微信公众号 API 是非公开接口，token/cookie 会过期，需要定期重新扫码
- 这个模块的 API 调用方式参考了开源项目 `wechat-article-exporter`

---

### 5.14 sync_db.py — SQLite 数据库层

#### 模块职责

管理所有持久化数据的 SQLite 操作。

#### 主要数据表

```text
accounts         — 公众号账号信息（fakeid、昵称、签名等）
articles         — 文章记录（URL、标题、状态、同步源等）
article_executions — 文章转换执行记录（每次转换一条）
sync_sources     — 同步源（关注的公众号）
sync_runs        — 同步运行记录
ingest_jobs      — 入库任务（批量处理）
artifacts        — 产出物记录（markdown 文件、FNS 路径等）
search_history   — 搜索历史
users            — 用户表（管理员）
audit_logs       — 审计日志（密码修改等操作）
scheduler_configs — 调度器配置
scheduler_runs   — 调度器运行记录
wechat_mp_credentials — 微信公众号凭据（加密存储）
wechat_mp_qr_sessions — 扫码登录会话
```

#### 可迁移启发

- 单文件 SQLite + 手写 SQL 是轻量项目的标准数据层
- 审计日志（记录谁在什么时间做了什么操作）是好习惯

---

### 5.15 task_history.py — 任务历史

#### 模块职责

用 JSONL（每行一个 JSON）格式记录任务历史。这是早期版本的简单记录方式，现在主要数据已经迁移到 SQLite，但仍然保留用于兼容。

---

### 5.16 search/sogou_weixin.py — 搜狗微信搜索

#### 模块职责

通过搜狗微信搜索（weixin.sogou.com）按关键词搜索公众号文章。

#### 工作机制

```text
1. 构造搜狗搜索 URL（关键词 + 页码）
2. 发送 HTTP 请求（带浏览器 User-Agent）
3. 解析搜索结果页面（BeautifulSoup）
4. 提取文章标题、摘要、链接、公众号名
5. 解析搜狗的跳转链接，获取真实微信文章 URL
```

---

### 5.17 Dockerfile — Docker 镜像

#### 模块职责

构建项目的 Docker 镜像。

```text
FROM python:3.14-slim-bookworm
→ 创建非 root 用户 app
→ 安装 Python 依赖
→ 复制 app/ 和 scripts/ 目录
→ 创建数据目录
→ 以 app 用户运行 uvicorn
```

---

## 6. 数据流 / 调用链

### 6.1 单篇文章转换（Web 界面触发）

```text
用户粘贴链接
  ↓
浏览器 → POST /api/convert
  ↓
routes.py → services.execute_single_conversion()
  ↓
1. detect_source_type(url)                    # 判断类型
2. fetch_article_from_url(url)                # 抓取（带缓存）
   ├→ 微信：WeChatArticlePipeline
   └→ 网页：readability + BeautifulSoup
3. run_pipeline(article)                      # 转换
   ├→ HTMLToMarkdownParser（HTML→MD）
   ├→ MarkdownImageDownloader（图片处理）
   └→ format_markdown（格式化、去噪）
4. apply_ai_polish_to_result() [可选]         # AI 润色
   ├→ request_interpreter_variables()          # 第一次 AI 调用
   └→ request_polished_content() [可选]        # 第二次 AI 调用
5. sync_result_to_output()                    # 输出
   ├→ "local"：保存到本地文件
   └→ "fns"：调用 Fast Note Sync API 同步到 Obsidian
6. 记录到 SQLite + 返回 JSON 结果
```

### 6.2 Telegram Bot 触发

```text
用户在 Telegram 发送链接
  ↓
[Webhook 模式]
  Telegram 服务器 → POST /api/integrations/telegram/webhook
    ↓
[Polling 模式]
  bot_workers.py 后台线程 → Telegram getUpdates API
    ↓
services.build_telegram_bot_message()        # 解析消息
services.handle_bot_message()                # 验证权限、提取 URL
services.submit_telegram_convert_task()      # 提交异步任务
  ↓
ThreadPoolExecutor → process_telegram_convert_task()
  ↓
execute_single_conversion() → 同 6.1 的步骤 2-6
  ↓
send_telegram_message()                      # 发送回执
```

### 6.3 公众号自动同步

```text
scheduler.py 定时触发
  ↓
sync_source_articles(source_id)
  ↓
1. WechatMPClient.fetch_articles()           # 调用微信 API 拉取文章列表
2. 存入 articles 表（去重）
3. 用户在 Web 界面勾选文章
4. submit_article_ingest()                    # 提交入库任务
  ↓
_ingest_executor → _run_ingest_job()
  ↓
对每篇文章循环：
  execute_single_conversion() → 同 6.1 的步骤 2-6
  随机等待 5-12 秒（防止请求过快）
```

---

## 7. 架构设计分析

### 7.1 架构概览

这是一个**单体应用**，所有功能都在一个 Python 进程中运行。

```text
┌──────────────────────────────────────────┐
│            FastAPI 应用 (main.py)         │
├──────────────────────────────────────────┤
│  api/routes.py  ←→  app/web/ (前端 HTML) │
├──────────────────────────────────────────┤
│           services.py (业务编排层)         │
├──────┬──────┬───────┬───────┬────────────┤
│pipeline│ ai   │config │auth   │ sync_db    │
│(转换) │(润色)│(配置) │(认证) │(数据库)     │
├──────┴──────┴───────┴───────┴────────────┤
│  scheduler.py  │  bot_workers.py         │
│  (定时任务)     │  (Bot 接收)              │
└──────────────────────────────────────────┘
       │                    │
  SQLite 文件          外部服务
  JSON 配置文件      (微信、Telegram、飞书、AI、S3)
```

### 7.2 关键设计选择

| 选择 | 原因 |
| --- | --- |
| 自写 HTMLParser 而非用 markdownify | 微信 HTML 结构特殊，需要精细控制 |
| 自实现 S3 签名而非用 boto3 | 减少依赖，更轻量 |
| SQLite 而非 PostgreSQL/MySQL | 个人项目，单文件数据库足够 |
| 原生 HTML/JS 而非 React/Vue | 功能简单，不需要前端框架 |
| multiprocessing 隔离转换任务 | 防止抓取卡住影响主服务 |
| 线程而非 async | 代码简单，requests 库同步调用 |

### 7.3 这种架构的优点

- **部署简单**：一个 Docker 容器，一个 SQLite 文件
- **依赖少**：不需要 Redis、消息队列、ORM
- **代码直观**：没有复杂的抽象层，阅读门槛低
- **适合新手学习**：完整覆盖了 Web 后端的核心概念

### 7.4 这种架构的限制

- **单实例**：不能水平扩展（SQLite + 内存状态）
- **长任务会占线程**：批量转换时 ThreadPoolExecutor 的线程有限
- **没有 API 版本控制**：接口变更可能影响前端
- **没有数据库迁移**：表结构变更靠代码直接处理

### 7.5 对个人项目的启发

- 不一定要用最"高级"的技术栈，解决问题最重要
- SQLite + JSON 配置 + Docker 部署是个人项目的高效组合
- 关键路径上的防护（进程隔离、登录频率限制）比追求全面更重要

---

## 8. 值得复用的模块 / 机制

| 模块 / 机制 | 原项目中的作用 | 复用价值 | 已沉淀 Brick |
| --- | --- | --- | --- |
| HTML→Markdown 清洗管线 | 把微信 HTML 转为干净 Markdown | 高 | [[Skill：用 Pipeline 模式实现数据清洗与转换]] |
| 多 Provider AI 适配器 | 统一调用 6 种 AI 服务 | 高 | [[Skill：给 Python 项目添加新的 AI Provider]] |
| 模板驱动的 AI 润色 | 用模板变量做内容增强 | 高 | [[Skill：用模板引擎实现 AI 生成内容与文本渲染]] |
| 分层配置 + 加密存储 | 安全管理敏感配置 | 中 | [[Skill：给 Python 项目实现分层配置系统]] |
| 进程隔离执行 | 防止任务卡死影响主服务 | 中 | [[Skill：用进程隔离执行耗时或高风险任务]] |
| Bot 多平台接入 | Telegram + 飞书双平台 | 中 | [[Skill：在 FastAPI 项目中添加 Bot 接入]] |
| 认证与加密 | 密码哈希 + 对称加密 + 限速 | 中 | [[Concept：Python 后端安全基础 — 密码哈希、对称加密与会话限速]] |
| 30 秒轮询调度器 | 简单定时任务 | 低 | [[Concept：Python 后台定时调度的实现模式]] |

---

## 9. 已生成的 Brick 清单

### Skill-brick

| # | Brick | 来源模块 | 核心学到的模式 |
| --- | --- | --- | --- |
| 1 | [[Skill：用 Pipeline 模式实现数据清洗与转换]] | core/pipeline.py | 把复杂任务拆成独立步骤，数据在步骤间流动 |
| 2 | [[Skill：给 Python 项目添加新的 AI Provider]] | ai_adapters.py | 适配器模式统一多 Provider 接口 |
| 3 | [[Skill：用模板引擎实现 AI 生成内容与文本渲染]] | ai_polish.py | `{{variable}}` 占位符 + JSON 解析 + 数据与格式分离 |
| 4 | [[Skill：在 FastAPI 项目中添加 Bot 接入]] | bot_workers.py, services.py | 五层架构：接收→解析→去重→异步任务→回执 |
| 5 | [[Skill：给 Python 项目实现分层配置系统]] | config.py | 环境变量→配置文件→默认值 + Fernet 加密 |
| 6 | [[Skill：用进程隔离执行耗时或高风险任务]] | services.py | multiprocessing.spawn + Queue + 两级超时清理 |

### Concept-brick

| # | Brick | 来源模块 | 核心学到的概念 |
| --- | --- | --- | --- |
| 1 | [[Concept：Python 后端安全基础 — 密码哈希、对称加密与会话限速]] | auth.py | PBKDF2 不可逆存储 + Fernet 可逆加密 + 滑动窗口限速 |
| 2 | [[Concept：Python 后台定时调度的实现模式]] | scheduler.py | 守护线程 + 30s 轮询 + 非阻塞锁 + 数据库配置 |

---

## 10. 对我现有项目的启发

### 10.1 可以直接借鉴的地方

- pipeline.py 的 HTML 清洗逻辑可以独立使用，不需要整个项目
- ai_adapters.py 的多 Provider 适配可以直接复制到其他 Python 项目
- 模板变量系统（`{{变量名}}` 占位符 + render_template）简单但有效

### 10.2 需要改造后才能用的地方

- 配置系统比较重（1934 行），如果要迁移可以只取核心逻辑
- SQLite 数据模型是专门为文章管理设计的，迁移需要重新设计表结构
- Bot 接入逻辑和业务逻辑耦合在 services.py 中，迁移时需要解耦

### 10.3 不适合迁移的地方

- 搜狗搜索爬虫（平台特定，容易失效）
- 微信公众号 API 交互（非公开接口，需要维护 token/cookie）
- 前端 HTML/JS 页面（与后端紧耦合）

### 10.4 已生成的 Brick

所有 6 个 Skill-brick 和 2 个 Concept-brick 已生成，详见 [[#9. 已生成的 Brick 清单]]。

> [!tip] 阅读建议
> 先读 [[Skill：用 Pipeline 模式实现数据清洗与转换]] 和 [[Skill：给 Python 项目添加新的 AI Provider]]，这两个覆盖了项目最核心的两个设计模式。

---

## 11. 后续可实践任务

### 任务 1：让 AI Agent 读取 pipeline.py，生成一份"HTML→Markdown 清洗规则清单"

- 目标：整理出 pipeline.py 中所有清洗规则（去噪、去推广、标题规范化等），形成可复用的规则清单
- 输入：core/pipeline.py 源码
- 输出：Markdown 格式的清洗规则清单
- 适合谁执行：Claude Code / Codex
- 预期价值：理解清洗管线的每个步骤

### 任务 2：调用 skill-brick-writer，为"如何独立使用 pipeline.py"生成 skill-brick

- 目标：把 pipeline.py 的独立使用方法写成可复用的操作指南
- 输入：pipeline.py CLI 入口代码 + 使用示例
- 输出：skill-brick 文档
- 适合谁执行：Claude Code（调用 skill-brick-writer）
- 预期价值：学会如何把单篇文章转 Markdown

### 任务 3：调用 skill-brick-writer，为"多 Provider AI 适配"生成 skill-brick

- 目标：把 ai_adapters.py 的适配器模式写成学习指南
- 输入：ai_adapters.py 源码 + Provider 列表
- 输出：skill-brick 文档
- 适合谁执行：Claude Code（调用 skill-brick-writer）
- 预期价值：学会适配器模式在 AI 项目中的实际应用

### 任务 4：让 AI Agent 为 services.py 的核心流程生成一份"数据流图"

- 目标：画出从"用户输入 URL"到"文章入库 Obsidian"的完整数据流
- 输入：services.py + 依赖模块
- 输出：Mermaid 格式的数据流图
- 适合谁执行：Claude Code
- 预期价值：直观理解整个系统如何运转

---

## 12. 不确定点汇总

| 不确定点 | 原因 | 下一步验证方式 |
| --- | --- | --- |
| 搜狗搜索的稳定性 | 搜狗反爬策略可能变化，代码中未见详细的重试逻辑 | 实际运行测试 |
| 微信公众号 token/cookie 过期周期 | 代码中未见过期自动刷新，需要手动重新扫码 | 查看 wechat_sync.py 的登录检查逻辑 |
| 普通网页抓取成功率 | readability-lxml 对复杂页面可能提取不准 | 用多种网页测试 |

---

## 13. 总结判断

**这个项目最值得学习的是：**
- 一个完整的"内容抓取 → 清洗转换 → AI 增强 → 入库同步"管线设计
- 多种外部服务集成（微信、Telegram、飞书、AI、S3）的统一编排方式

**这个项目最值得迁移的是：**
- pipeline.py 的 HTML→Markdown 清洗逻辑（可独立使用）
- ai_adapters.py 的多 Provider 适配模式
- ai_polish.py 的模板驱动 AI 润色工作流

**这个项目不值得投入太多精力的是：**
- 搜狗搜索爬虫（平台特定，容易失效）
- 前端页面（原生 HTML/JS，无设计模式可学）

**下一步最应该深挖的是：**
- pipeline.py 的清洗规则细节
- AI 润色的模板变量系统

**最值得调用 skill-brick-writer 的候选是：**

1. [[Skill：用 Pipeline 模式实现数据清洗与转换]] — 已生成
2. [[Skill：给 Python 项目添加新的 AI Provider]] — 已生成
3. [[Skill：用模板引擎实现 AI 生成内容与文本渲染]] — 已生成
4. [[Skill：在 FastAPI 项目中添加 Bot 接入]] — 已生成
5. [[Skill：给 Python 项目实现分层配置系统]] — 已生成
6. [[Skill：用进程隔离执行耗时或高风险任务]] — 已生成
7. [[Concept：Python 后端安全基础 — 密码哈希、对称加密与会话限速]] — 已生成
8. [[Concept：Python 后台定时调度的实现模式]] — 已生成
