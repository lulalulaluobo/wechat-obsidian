---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - bot
  - fastapi
  - telegram
  - feishu
  - webhook
summary: 在已有 FastAPI 项目中添加 Telegram 或飞书 Bot 接入，包括消息接收（Webhook / 主动连接）、消息解析、异步任务提交和回执发送的完整流程
input:
  - Bot 平台的 API 文档（消息格式、认证方式、发送接口）
  - Bot 凭证（Telegram Bot Token / 飞书 App ID + App Secret）
output:
  - 可工作的 Bot 接入，用户发消息后 Bot 能异步处理并回执结果
source_wiki:
  - "[[project-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：在 FastAPI 项目中添加 Bot 接入

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 在已有 FastAPI 项目中添加一个新的 Bot 接入（如 Telegram、飞书），只需改 3 个文件，核心架构是"消息接收 → 解析 → 去重 → 校验 → 异步任务 → 回执"。

---

## 1 一句话用途

在已有的 FastAPI 项目中，添加一个新的 Bot 平台接入（如 Telegram、飞书），使用户通过 Bot 发送消息后，系统能异步处理任务并返回结果。

## 2 什么时候使用

> [!tip] 适用

- 项目已有 FastAPI 框架，想接入 Telegram Bot 或飞书 Bot
- Bot 的使用场景是"用户发消息 → 后台异步处理 → Bot 回执结果"
- 需要支持两种接收模式：Webhook（需要公网 IP）和主动连接（不需要公网 IP）
- 想理解 Bot 接入的分层架构：接收层、解析层、校验层、任务层、回执层

> [!warning] 不适用

- Bot 需要支持复杂的交互式对话（如多轮对话、按钮回调）——本文只覆盖"单条消息 → 单次任务"的模式
- 项目不是 FastAPI——接收层需要替换为对应框架的路由
- 想接入的 Bot 平台不是 Telegram 或飞书——解析层和发送层需要重写，但整体架构可以复用

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| Bot 平台类型 | 是 | 无 | 否 | `telegram` 或 `feishu` |
| Bot 凭证 | 是 | 无 | 否 | Telegram: Bot Token；飞书: App ID + App Secret |
| 接收模式 | 是 | `webhook` | 是 | `webhook`（需要公网）或 `polling`/`long_connection`（不需要公网） |
| Webhook URL | 条件必需 | 无 | 否 | 仅 Webhook 模式需要，如 `https://your.domain/api/integrations/telegram/webhook` |
| Webhook Secret | 条件必需 | 无 | 否 | 仅 Telegram Webhook 需要，用于验证请求来源 |
| 消息白名单 | 否 | 空（不限制） | 否 | Telegram: allowed_chat_ids；飞书: allowed_open_ids |
| 异步任务执行器 | 否 | `ThreadPoolExecutor` | 是 | AI 可以 grep 项目中已有的 executor |
| FastAPI 路由文件 | 是 | `app/api/routes.py` | 是 | AI 可以 grep 找到路由定义文件 |
| 消息处理逻辑文件 | 是 | `app/services.py` | 是 | AI 可以 grep 找到 bot 相关函数 |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 接收模式：Webhook vs 主动连接 | 如果服务器有公网 IP 且能配置 HTTPS → Webhook；如果是 NAS / 本地部署 / 无公网 IP → 主动连接（Telegram 用 Polling，飞书用长连接） | 影响 bot_workers.py 中是否需要后台线程 |
| 消息去重 | 必须实现。Telegram 用 `chat_id:message_id`，飞书用 `event_id` | 防止同一条消息被重复处理 |
| 白名单 vs 全放行 | 生产环境建议开启白名单；个人使用可以留空（不限制） | 影响 handle_bot_message 中的校验逻辑 |
| 异步 vs 同步处理 | Bot 消息处理必须异步（用 ThreadPoolExecutor），不能在请求线程中执行耗时任务 | 影响是否需要 submit_xxx_task 和 process_xxx_task 两个函数 |

> [!important] 关键原则
> Bot 接入的核心架构是固定的五层流水线：**接收 → 解析 → 去重 → 校验 → 异步任务 → 回执**。不管接入哪个平台，只有"接收层"和"解析层"不同，其余层可以完全复用。

---

## 5 直接照做

### 5.1 准备

1. **确认 Bot 凭证**：在对应平台创建 Bot 并获取凭证
   - Telegram：通过 @BotFather 创建 Bot，获取 Bot Token
   - 飞书：在飞书开放平台创建应用，获取 App ID 和 App Secret

2. **定位需要改的文件**：

```bash
# 路由文件（Webhook 端点）
grep -n "webhook\|router" app/api/routes.py

# 服务文件（消息处理逻辑）
grep -n "handle_bot_message\|build_.*_bot_message\|submit_.*_task\|process_.*_task" app/services.py

# Bot 接收器文件（后台线程）
grep -n "start_bot_receivers\|stop_bot_receivers" app/bot_workers.py

# 配置文件
grep -n "telegram_\|feishu_" app/config.py
```

3. **理解五层架构**：

```text
用户发送消息
  ↓
第 1 层：接收（Webhook 端点 / Polling 循环 / 长连接回调）
  ↓
第 2 层：解析（build_xxx_bot_message → 提取 chat_id、sender_id、text、urls）
  ↓
第 3 层：去重 + 校验（handle_bot_message → 白名单检查、消息去重、链接提取）
  ↓
第 4 层：异步任务（submit_xxx_task → ThreadPoolExecutor → process_xxx_task）
  ↓
第 5 层：回执（send_xxx_message → 调用平台 API 发送结果消息）
```

### 5.2 添加新 Bot 接入

下面以添加一个新平台 `xxx` 为例，展示需要改的 3 个文件的每个位置。

#### 位置 1：添加配置项（app/config.py）

在 `Settings` 中添加新平台的配置字段：

```python
# 在 Settings dataclass 中添加
xxx_enabled: bool = False
xxx_bot_token: str = ""              # 或 app_id + app_secret
xxx_receive_mode: str = "webhook"    # "webhook" 或 "polling" 或 "long_connection"
xxx_webhook_url: str = ""
xxx_webhook_secret: str = ""
xxx_allowed_user_ids: list[str] = field(default_factory=list)
xxx_notify_on_complete: bool = True
```

#### 位置 2：添加消息解析函数（app/services.py）

参考 `build_telegram_bot_message()` 或 `build_feishu_bot_message()`，写一个新函数：

```python
def build_xxx_bot_message(payload: dict[str, Any], receive_mode: str) -> dict[str, Any] | None:
    """
    从平台原始 payload 中提取统一格式的 Bot 消息。
    返回 None 表示无法解析（忽略这条消息）。
    """
    # 1. 从 payload 中提取关键字段（看平台 API 文档的回调格式）
    chat_id = str(payload.get("chat_id") or "").strip()
    sender_id = str(payload.get("sender_id") or chat_id).strip()
    message_id = str(payload.get("message_id") or "").strip()
    text = str(payload.get("text") or "").strip()

    if not chat_id:
        return None

    # 2. 从文本中提取链接
    urls = parse_links(urls_text=text)

    # 3. 返回统一格式
    return {
        "trigger_channel": "xxx",
        "receive_mode": receive_mode,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "event_key": f"xxx:{chat_id}:{message_id}" if message_id else "",
        "raw_text": text,
        "urls": urls,
        "created_at": _utc_now(),
    }
```

> [!example]- 参考：Telegram 的解析逻辑
>
> ```python
> # Telegram 的 payload 格式：
> # {"update_id": 123, "message": {"message_id": 456, "chat": {"id": 789}, "from": {"id": 789}, "text": "..."}}
> message = payload.get("message") if isinstance(payload.get("message"), dict) else None
> if not message:
>     return None
> chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
> sender = message.get("from") if isinstance(message.get("from"), dict) else {}
> chat_id = str(chat.get("id") or "").strip()
> sender_id = str(sender.get("id") or chat_id).strip()
> ```

> [!example]- 参考：飞书的解析逻辑
>
> ```python
> # 飞书的 payload 格式：
> # {"header": {"event_id": "...", "event_type": "im.message.receive_v1"},
> #  "event": {"message": {"message_id": "...", "content": "{\"text\":\"...\"}"},
> #            "sender": {"sender_id": {"open_id": "..."}}}}
> text, open_id, chat_type = extract_feishu_message_text(payload)
> if not open_id:
>     return None
> # 飞书的 open_id 既是 sender_id 也是 chat_id（私聊场景）
> ```

#### 位置 3：在 handle_bot_message 中添加新平台分支（app/services.py）

在 `handle_bot_message()` 函数中添加新的 `if trigger_channel == "xxx":` 分支：

```python
def handle_bot_message(message, *, telegram_sender=None, feishu_sender=None, ...) -> dict:
    trigger_channel = str(message.get("trigger_channel") or "").strip()
    # ... 现有的 telegram 和 feishu 分支 ...

    if trigger_channel == "xxx":
        if not settings.xxx_enabled:
            return {"status": "ignored", "reason": "xxx_disabled"}
        # 白名单检查（如果配置了白名单）
        if settings.xxx_allowed_user_ids and sender_id not in settings.xxx_allowed_user_ids:
            return {"status": "ignored", "reason": "user_not_allowed"}
        # 消息去重
        if _remember_service_bot_event(event_key, "xxx"):
            return {"status": "ignored", "reason": "duplicate_message"}
        # 提取链接
        url, url_count = extract_single_wechat_url(raw_text)
        if url_count == 0 or not url:
            xxx_sender(chat_id, "未识别到可用链接，请发送一条链接。")
            return {"status": "replied", "reason": "no_link"}
        if url_count > 1:
            xxx_sender(chat_id, "一次只支持一条链接。")
            return {"status": "replied", "reason": "multiple_links"}
        # 发送"已接收"回执
        xxx_sender(chat_id, "已接收，开始处理。")
        # 提交异步任务
        xxx_submitter(url, chat_id)
        return {"status": "accepted"}

    return {"status": "ignored", "reason": "unsupported_channel"}
```

#### 位置 4：添加消息发送函数（app/services.py）

参考 `send_telegram_message()` 或 `send_feishu_message()`：

```python
def send_xxx_message(chat_id: str, text: str, http_session=None) -> dict[str, Any]:
    """调用平台 API 发送消息给用户。"""
    settings = get_settings()
    session = http_session or requests.Session()

    # 根据平台 API 文档构造请求
    response = session.post(
        f"https://api.xxx.com/v1/messages",  # 替换为实际 API
        headers={"Authorization": f"Bearer {settings.xxx_bot_token}"},
        json={"chat_id": chat_id, "text": text},
        timeout=max(settings.default_timeout, 15),
    )
    response.raise_for_status()
    return response.json()
```

> [!example]- 参考：飞书的认证方式
>
> ```python
> # 飞书不是直接用 Bot Token，而是先获取 tenant_access_token
> tenant_access_token = get_feishu_tenant_access_token(http_session=session)
> response = session.post(
>     "https://open.feishu.cn/open-apis/im/v1/messages",
>     params={"receive_id_type": "open_id"},
>     headers={
>         "Authorization": f"Bearer {tenant_access_token}",
>         "Content-Type": "application/json; charset=utf-8",
>     },
>     json={
>         "receive_id": open_id,
>         "msg_type": "text",
>         "content": json.dumps({"text": text}, ensure_ascii=False),
>     },
>     timeout=max(settings.default_timeout, 15),
> )
> ```
>
> 飞书的 token 还有缓存机制，避免每次发送消息都重新获取。

#### 位置 5：添加异步任务提交和处理函数（app/services.py）

```python
# 线程池（模块级别）
_xxx_executor = ThreadPoolExecutor(max_workers=2)

def submit_xxx_convert_task(url: str, chat_id: str, *, receive_mode: str = "webhook", ...) -> None:
    """提交异步转换任务。不在请求线程中执行耗时操作。"""
    task = get_task_history_store().create_task(
        trigger_channel="xxx",
        source_type=detect_source_type(url),
        source_url=url,
    )
    _xxx_executor.submit(
        process_xxx_convert_task,
        url, chat_id, str(task["task_id"]), receive_mode, ...
    )

def process_xxx_convert_task(url: str, chat_id: str, task_id: str | None = None, ...) -> None:
    """在后台线程中执行实际的任务。"""
    settings = get_settings()
    try:
        payload = execute_single_conversion(
            url=url,
            timeout=settings.default_timeout,
            trigger_channel="xxx",
            task_id=task_id,
            ...
        )
    except Exception as error:
        send_xxx_message(chat_id, f"处理失败：{error}")
        return

    # 发送成功回执
    if settings.xxx_notify_on_complete:
        title = str(payload["result"].get("title") or "处理完成")
        send_xxx_message(chat_id, f"处理完成：{title}")
```

#### 位置 6：添加 Webhook 端点（app/api/routes.py）

仅 Webhook 模式需要。参考 `telegram_webhook()` 或 `feishu_webhook()`：

```python
@router.post("/api/integrations/xxx/webhook")
async def xxx_webhook(
    request: Request,
    xxx_secret: str | None = Header(default=None, alias="X-XXX-Secret"),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.xxx_enabled:
        return {"status": "ignored", "reason": "xxx_disabled"}
    if settings.xxx_receive_mode != "webhook":
        return {"status": "ignored", "reason": "webhook_disabled"}
    # 验证 Secret
    if not settings.xxx_webhook_secret or xxx_secret != settings.xxx_webhook_secret:
        raise HTTPException(status_code=403, detail="Webhook secret 无效")

    payload = await request.json()
    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "no_message"}
    bot_message = build_xxx_bot_message(payload, "webhook")
    if bot_message is None:
        return {"status": "ignored", "reason": "no_message"}
    return handle_bot_message(
        bot_message,
        xxx_sender=send_xxx_message,
        xxx_submitter=submit_xxx_convert_task,
    )
```

> [!example]- 参考：飞书 Webhook 的特殊处理
>
> ```python
> # 飞书 Webhook 有一个"URL 验证"步骤：
> # 飞书开放平台在配置 Webhook URL 时会发一个 verification 请求
> if payload.get("type") == "url_verification":
>     token = str(payload.get("token") or "").strip()
>     if token != settings.feishu_verification_token:
>         raise HTTPException(status_code=403, detail="verification token 无效")
>     return {"challenge": str(payload.get("challenge") or "")}
> ```
>
> 这一步必须在 Webhook 端点的最前面处理，否则无法通过飞书的验证。

#### 位置 7：添加主动连接接收器（app/bot_workers.py）

仅 Polling 或长连接模式需要。

**Polling 模式（参考 Telegram）**：

```python
_xxx_thread: threading.Thread | None = None
_xxx_stop = threading.Event()

def start_xxx_polling_worker() -> None:
    global _xxx_thread
    with _worker_lock:
        if _xxx_thread is not None and _xxx_thread.is_alive():
            return
        _xxx_stop.clear()
        _xxx_thread = threading.Thread(target=_xxx_polling_loop, name="xxx-polling", daemon=True)
        _xxx_thread.start()

def _xxx_polling_loop() -> None:
    """后台线程：循环调用平台 API 拉取新消息。"""
    session = requests.Session()
    while not _xxx_stop.is_set():
        settings = get_settings()
        if not settings.xxx_enabled or settings.xxx_receive_mode != "polling":
            _xxx_stop.wait(5)
            continue
        try:
            response = session.get(
                f"https://api.xxx.com/v1/messages",
                headers={"Authorization": f"Bearer {settings.xxx_bot_token}"},
                timeout=30,
            )
            response.raise_for_status()
            messages = response.json().get("messages") or []
            for msg in messages:
                # 解析并处理每条消息
                bot_message = build_xxx_bot_message(msg, "polling")
                if bot_message:
                    handle_bot_message(bot_message)
        except Exception as error:
            print(f"[xxx] polling failed: {error}")
            _xxx_stop.wait(5)
            continue
        _xxx_stop.wait(5)  # 控制拉取频率

```

**长连接模式（参考飞书）**：

```python
def start_xxx_long_connection_worker() -> None:
    global _xxx_thread
    with _worker_lock:
        if _xxx_thread is not None and _xxx_thread.is_alive():
            return
        _xxx_stop.clear()
        _xxx_thread = threading.Thread(target=_xxx_long_connection_loop, name="xxx-lc", daemon=True)
        _xxx_thread.start()

def _xxx_long_connection_loop() -> None:
    while not _xxx_stop.is_set():
        settings = get_settings()
        if not settings.xxx_enabled or settings.xxx_receive_mode != "long_connection":
            _xxx_stop.wait(5)
            continue
        try:
            # 使用平台 SDK 建立 WebSocket 长连接
            # 当收到消息时，回调 on_message 函数
            # on_message → build_xxx_bot_message → handle_bot_message
            ...
        except Exception as error:
            print(f"[xxx] long connection failed: {error}")
            _xxx_stop.wait(5)
```

#### 位置 8：注册接收器的启动和停止（app/bot_workers.py）

在 `start_bot_receivers()` 和 `stop_bot_receivers()` 中添加新平台：

```python
def start_bot_receivers() -> None:
    settings = get_settings()
    # ... 现有的 telegram 和 feishu 启动逻辑 ...
    if settings.xxx_enabled and settings.xxx_receive_mode == "polling":
        start_xxx_polling_worker()

def stop_bot_receivers() -> None:
    _telegram_stop.set()
    _feishu_stop.set()
    _xxx_stop.set()  # ← 添加这一行
    for thread in (_telegram_thread, _feishu_thread, _xxx_thread):
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
```

> [!note] main.py 不需要改
> `main.py` 中的 lifespan 已经调用了 `start_bot_receivers()` 和 `stop_bot_receivers()`，新增的接收器会自动被管理。

### 5.3 配置

1. 在 Web 管理界面的"设置"页面，启用新 Bot
2. 填入 Bot 凭证（Token / App ID + Secret）
3. 选择接收模式（Webhook 或 主动连接）
4. 如果选 Webhook，填写公网回调地址和 Secret
5. 如果选主动连接，启动后 Bot 会自动开始接收消息

### 5.4 验证

1. **Webhook 模式**：用 curl 模拟平台回调

```bash
curl -X POST http://127.0.0.1:8765/api/integrations/xxx/webhook \
  -H "Content-Type: application/json" \
  -H "X-XXX-Secret: 替换成你的secret" \
  -d '{"chat_id": "test", "message_id": "1", "text": "https://mp.weixin.qq.com/s/test"}'
```

2. **主动连接模式**：启动服务后，直接给 Bot 发消息

3. **检查日志**：观察控制台输出

```text
[bot] receiver configuration deployment_mode=xxx xxx=polling
[xxx] received message chat_id=xxx url_count=1
[xxx] conversion synced chat_id=xxx path=00_Inbox/微信公众号/xxx.md
```

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| 消息解析函数已定义 | `grep "build_xxx_bot_message" app/services.py` | 函数存在，返回统一格式的 dict |
| 发送函数已定义 | `grep "send_xxx_message" app/services.py` | 函数存在，能调用平台 API |
| 异步任务函数已定义 | `grep "submit_xxx_convert_task\|process_xxx_convert_task" app/services.py` | 两个函数都存在 |
| handle_bot_message 有新分支 | `grep "xxx" app/services.py` | `handle_bot_message()` 中有 `trigger_channel == "xxx"` 分支 |
| Webhook 端点已定义（如适用） | `grep "xxx/webhook" app/api/routes.py` | 路由存在且能返回 `{"status": "accepted"}` |
| 接收器已注册（如适用） | `grep "xxx" app/bot_workers.py` | `start_bot_receivers()` 和 `stop_bot_receivers()` 中都有新平台 |
| 消息去重生效 | 连续发送两次相同消息 | 第二次返回 `{"status": "ignored", "reason": "duplicate_message"}` |
| 白名单生效 | 用不在白名单中的 ID 发送 | 返回 `{"status": "ignored", "reason": "user_not_allowed"}` |
| 上层代码无改动 | `git diff app/main.py` | main.py 无变更（证明生命周期管理是通用的） |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| Webhook 返回 403 | Secret 是否正确、Header 名称是否与平台一致 | 对照平台文档检查验证逻辑 |
| Webhook 返回 ignored | Bot 是否启用、receive_mode 是否设为 webhook | 检查配置项的 enabled 和 receive_mode |
| 主动连接不启动 | `start_bot_receivers()` 中是否添加了新平台的启动逻辑 | 检查 bot_workers.py 的启动条件 |
| 消息被重复处理 | `event_key` 是否正确拼接、去重缓存是否生效 | 检查 `build_xxx_bot_message` 中 event_key 的生成逻辑 |
| 回执发送失败 | 平台 API 的认证方式是否正确（Token vs tenant_access_token） | 对照平台 API 文档检查 send_xxx_message |
| 消息解析返回 None | payload 结构是否与平台回调格式匹配 | 用 `print(payload)` 查看实际结构，调整 build_xxx_bot_message |
| 线程池任务卡住 | executor 的 max_workers 是否够用、任务函数是否抛了未捕获异常 | 检查 process_xxx_convert_task 的 try/except |
| 飞书 URL 验证失败 | verification_token 是否与飞书开放平台配置一致 | 检查 feishu_webhook 端点中 url_verification 分支 |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我给项目添加一个新的 Bot 接入。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先判断：新 Bot 平台是 Telegram 还是飞书还是其他？
>    - 如果是 Telegram → 用 Polling 或 Webhook 模式
>    - 如果是飞书 → 用长连接或 Webhook 模式
>    - 如果是其他平台 → 需要写新的解析函数和发送函数
> 3. 如果缺少关键参数（Bot 凭证、接收模式、API 文档），必须先问我。
> 4. 按"直接照做"的顺序执行，每完成一个位置告诉我改了什么。
> 5. 写完代码后，帮我生成一个 curl 命令或测试步骤验证连通性。
> 6. 结束时按"成功标准"逐项验证，并告诉我每项是否通过。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - Bot 平台：{{telegram / feishu / 其他}}
> - 接收模式：{{webhook / polling / long_connection}}
> - Bot 凭证：{{粘贴 Token 或 App ID/Secret}}
> - API 文档：{{粘贴 API 文档链接或关键信息}}
> ```

---

## 9 来源

相关 Wiki：
- [[project-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/bot_workers.py`
- wechat-md-server 项目源码 `app/services.py`（bot 相关函数：第 1996-2520 行）
- wechat-md-server 项目源码 `app/api/routes.py`（webhook 端点：第 976-1044 行）
- Telegram Bot API: `https://core.telegram.org/bots/api`
- 飞书开放平台: `https://open.feishu.cn/document/server-docs`

---

**引用来源**：[[project-wiki]]、`app/bot_workers.py`、`app/services.py`、`app/api/routes.py`
