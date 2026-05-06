---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - ai
  - adapter-pattern
  - python
summary: 用适配器模式给已有 Python 项目添加一个新的 AI 服务商（Provider），包括请求函数、响应解析、配置定义和参数验证
input:
  - 新 AI 服务商的 API 文档（端点 URL、请求格式、认证方式、响应格式）
output:
  - 可工作的新 Provider 集成，上层代码无需任何改动
source_wiki:
  - "[[wechat-md-server-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：给 Python 项目添加新的 AI Provider

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 在已有适配器架构中添加一个新的 AI 服务商，只需改 3 个文件的 5 个位置，上层业务代码完全不用动。

---

## 1 一句话用途

在已有的多 Provider AI 适配层中，添加一个新的 AI 服务商（如 Mistral、Groq、Cohere 等），使项目能通过统一接口调用它。

## 2 什么时候使用

> [!tip] 适用

- 项目已有多 Provider 适配层（类似 `request_ai_completion` → 按 type 分发的结构）
- 需要接入一个新的 AI 服务商的 Chat Completion API
- 新 Provider 的 API 遵循"发送 messages 列表 → 返回 AI 回复"的模式

> [!warning] 不适用

- 新 Provider 的 API 不是 Chat Completion 模式（如 Embedding API、Image Generation API）——需要重新设计接口
- 项目没有适配层，需要从零开始——先参考本文建立适配层骨架
- 只是想换一个 OpenAI Compatible 的服务（如 DeepSeek）——不需要加新类型，只需改 `base_url`

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| 新 Provider 的 API 端点 URL | 是 | 无 | 否 | `https://api.mistral.ai/v1` |
| 认证方式 | 是 | 无 | 否 | Bearer Token / API Key in Header / Query Param |
| 请求格式 | 是 | 无 | 否 | 通常类似 OpenAI 的 `{model, messages, temperature}` |
| 响应中回复文本的位置 | 是 | 无 | 否 | `choices[0].message.content` 或其他结构 |
| Provider 类型标识 | 是 | 无 | 否 | 小写下划线格式，如 `mistral` |
| 是否需要 API Key | 是 | 无 | 否 | 大部分云端服务需要，本地 Ollama 不需要 |
| 项目中适配层文件路径 | 是 | `app/ai_adapters.py` | 是 | AI 可以 grep `request_ai_completion` 定位 |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 新 API 是否兼容 OpenAI 格式 | 如果端点是 `/chat/completions`、请求体包含 `model` + `messages`、响应包含 `choices` → 归入 `openai_compatible` 类型，只加一个 Provider 定义即可 | 影响工作量：兼容 → 只改 config.py；不兼容 → 需要写新适配函数 |
| 认证方式 | Bearer Token → 放 `Authorization` 头；自定义头 → 看文档要求；Query Param → 放 URL 参数 | 影响请求函数中 headers 的构造 |
| system 消息处理 | 有的 API 把 system 消息放在 messages 列表里（OpenAI 风格），有的单独放在顶层字段（Anthropic 风格） | 影响请求体中 messages 的构造 |
| 是否需要 base_url | 云端服务需要；本地服务（如 Ollama）也需要但有默认值 | 影响验证规则和 Provider 定义 |

> [!important] 关键原则
> **如果你的新 Provider 兼容 OpenAI 的 Chat Completions API，不需要写新的适配函数。** 只需在 `config.py` 中添加一个 `BUILTIN_AI_PROVIDER_DEFINITIONS` 条目，类型设为 `openai_compatible`，填入 base_url 即可。DeepSeek 就是这样做的。

---

## 5 直接照做

### 5.1 准备

1. **确认 API 文档**：找到新 Provider 的 Chat API 端点、请求体格式、响应体格式、认证方式

2. **定位需要改的文件**：

```bash
# 适配层文件
grep -n "request_ai_completion\|extract_completion_text\|validate_provider_model" app/ai_adapters.py

# 配置文件
grep -n "AI_PROVIDER_TYPE_VALUES\|BUILTIN_AI_PROVIDER_DEFINITIONS" app/config.py
```

3. **判断是否需要新类型**：对照下面两种情况

### 5.2 情况 A：兼容 OpenAI 格式（最常见）

只需要改 `app/config.py`，加一个 Provider 定义：

```python
# 在 BUILTIN_AI_PROVIDER_DEFINITIONS 元组中添加一个字典
{
    "id": "mistral-default",           # 唯一 ID
    "type": "openai_compatible",        # 复用 OpenAI 兼容类型
    "display_name": "Mistral",          # 显示名称
    "built_in": True,
    "enabled": True,
    "base_url": "https://api.mistral.ai/v1",  # Provider 的 API 地址
    "api_key": "",                       # 留空，运行时由用户填写
},
```

完成。不需要改 ai_adapters.py。

### 5.3 情况 B：API 格式不同（需要新适配函数）

需要改 3 个文件的 5 个位置：

#### 位置 1：添加请求函数（app/ai_adapters.py）

在文件底部，参考现有函数写一个新的 `_request_xxx()`：

```python
def _request_xxx(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    session = http_session or requests.Session()
    base_url = str(provider.get("base_url") or "").rstrip("/")
    api_key = str(provider.get("api_key") or "").strip()

    # 1. 构造请求头（看 API 文档的认证方式）
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",  # 或其他认证方式
    }

    # 2. 构造请求体（看 API 文档的请求格式）
    payload = {
        "model": str(model.get("model_id") or ""),
        "messages": messages,  # 有的 API 需要拆分 system 消息
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    # 3. 发送请求（看 API 文档的端点路径）
    response = session.post(
        f"{base_url}/chat/completions",  # 替换为实际端点
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
```

> [!example]- 参考：Anthropic 的不同之处
>
> ```python
> # Anthropic 的 system 消息不在 messages 列表中，而是顶层字段
> system_messages = [msg.get("content") for msg in messages if msg.get("role") == "system"]
> user_messages = [msg for msg in messages if msg.get("role") != "system"]
> payload = {
>     "model": str(model.get("model_id") or ""),
>     "messages": user_messages,
>     "max_tokens": max_tokens or 256,
> }
> if system_messages:
>     payload["system"] = "\n".join(str(item or "") for item in system_messages)
> ```

> [!example]- 参考：Gemini 的不同之处
>
> ```python
> # Gemini 把所有消息合并为 parts，API Key 通过 query param 传递
> parts = []
> for message in messages:
>     role = str(message.get("role") or "")
>     content = str(message.get("content") or "")
>     prefix = "System" if role == "system" else "User"
>     parts.append({"text": f"{prefix}: {content}"})
>
> response = session.post(
>     f"{base_url}/models/{model.get('model_id')}:generateContent",
>     params={"key": api_key},  # 注意：Key 在 query param 里
>     json={"contents": [{"parts": parts}]},
>     timeout=timeout,
> )
> ```

#### 位置 2：添加分发分支（app/ai_adapters.py）

在 `request_ai_completion()` 函数中添加 elif：

```python
def request_ai_completion(*, provider, model, messages, timeout, ...) -> dict:
    provider_type = str(provider.get("type") or "").strip()
    # ... 现有分支 ...
    if provider_type == "xxx":          # ← 添加这一行
        return _request_xxx(             # ← 添加这一行
            provider=provider,            # ← 添加这一行
            model=model,                  # ← 添加这一行
            messages=messages,            # ← 添加这一行
            timeout=timeout,              # ← 添加这一行
            http_session=http_session,    # ← 添加这一行
            temperature=temperature,      # ← 添加这一行
            max_tokens=max_tokens,        # ← 添加这一行
        )                                # ← 添加这一行
    raise RuntimeError(f"暂不支持的 AI provider 类型: {provider_type}")
```

#### 位置 3：添加响应解析（app/ai_adapters.py）

在 `extract_completion_text()` 函数中添加分支：

```python
def extract_completion_text(payload, *, provider_type) -> str:
    # ... 现有分支 ...
    if provider_type == "xxx":
        # 根据新 API 的响应格式提取文本
        # 例如：return str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        ...
```

#### 位置 4：添加类型常量和 Provider 定义（app/config.py）

```python
# 在 AI_PROVIDER_TYPE_VALUES 集合中添加新类型
AI_PROVIDER_TYPE_VALUES = {"openai_compatible", "anthropic", "gemini", "ollama", "openrouter", "custom", "xxx"}

# 在 BUILTIN_AI_PROVIDER_DEFINITIONS 元组中添加定义
{
    "id": "xxx-default",
    "type": "xxx",
    "display_name": "XXX AI",
    "built_in": True,
    "enabled": True,
    "base_url": "https://api.xxx.com/v1",
    "api_key": "",
},
```

#### 位置 5：添加验证规则（app/ai_adapters.py）

在 `validate_provider_model()` 函数中：

```python
def validate_provider_model(provider, model) -> None:
    # 更新类型白名单
    if provider_type not in {"openai_compatible", ..., "xxx"}:
        raise ValueError("AI provider 类型无效")
    # 如果新类型需要 base_url：
    if provider_type == "xxx" and not base_url:
        raise ValueError("xxx Provider 的 Base URL 未配置")
    # 如果新类型需要 API Key：
    if provider_type == "xxx" and not api_key:
        raise ValueError("xxx Provider 需要 API Key")
```

### 5.4 配置

1. 在 Web 管理界面的"设置"页面，选择新添加的 Provider
2. 填入 Base URL 和 API Key
3. 点击"测试连接"验证

### 5.5 验证

1. **单元测试**：写一个简单测试调用新 Provider

```python
from app.ai_adapters import request_ai_completion, extract_completion_text

result = request_ai_completion(
    provider={"id": "xxx", "type": "xxx", "base_url": "替换成你的 base_url", "api_key": "替换成你的 api_key"},
    model={"id": "test", "model_id": "替换成模型名"},
    messages=[{"role": "user", "content": "你好"}],
    timeout=30,
)
text = extract_completion_text(result, provider_type="xxx")
assert text, "AI 返回内容不应为空"
print(f"新 Provider 返回：{text[:100]}")
```

2. **集成验证**：在 Web 界面开启 AI 润色，转换一篇文章，确认新 Provider 被正确调用

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| 新类型已在常量中 | `grep "xxx" app/config.py` | `AI_PROVIDER_TYPE_VALUES` 中包含新类型 |
| 请求函数已定义 | `grep "_request_xxx" app/ai_adapters.py` | 函数存在且参数签名与其他适配函数一致 |
| 分发分支已添加 | `grep "xxx" app/ai_adapters.py` | `request_ai_completion()` 和 `extract_completion_text()` 中都有新分支 |
| Provider 定义已添加 | `grep "xxx-default" app/config.py` | `BUILTIN_AI_PROVIDER_DEFINITIONS` 中有新条目 |
| 测试通过 | 运行单元测试 | AI 返回非空文本 |
| 上层代码无改动 | `git diff app/ai_polish.py` | ai_polish.py 无变更（证明接口统一） |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| `暂不支持的 AI provider 类型` | `request_ai_completion()` 中是否添加了新类型的 elif 分支 | 补充分发分支 |
| `AI 返回内容为空` | `extract_completion_text()` 中新分支的 JSON 路径是否正确 | 用 `print(result)` 查看实际响应结构，调整提取逻辑 |
| `401 Unauthorized` | API Key 是否正确、认证头字段名是否与文档一致 | 对照 API 文档检查 headers 构造 |
| `404 Not Found` | base_url 末尾是否多了 `/`、端点路径是否正确 | 检查完整 URL 拼接结果 |
| 连接超时 | base_url 是否可访问、是否需要代理 | 用 `curl base_url` 测试连通性 |
| JSON 解析失败 | 响应可能不是 JSON（如 HTML 错误页） | 检查 `response.status_code` 和 `response.text` |
| config.py 启动报错 | `AI_PROVIDER_TYPE_VALUES` 是否包含新类型、元组末尾逗号是否正确 | Python 元组语法检查 |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我给项目添加一个新的 AI Provider。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先判断：新 Provider 是否兼容 OpenAI Chat Completions 格式？
>    - 如果兼容 → 只需在 config.py 中添加 Provider 定义（情况 A）
>    - 如果不兼容 → 需要改 3 个文件 5 个位置（情况 B）
> 3. 如果缺少关键参数（API 端点、认证方式、请求/响应格式），必须先问我。
> 4. 按"直接照做"的顺序执行，每完成一个位置告诉我改了什么。
> 5. 如果是新类型，写完代码后帮我生成一个简单的测试脚本验证连通性。
> 6. 结束时按"成功标准"逐项验证，并告诉我每项是否通过。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - 新 Provider 名称：{{粘贴 Provider 名称}}
> - API 文档：{{粘贴 API 文档链接或关键信息}}
> ```

---

## 9 来源

相关 Wiki：
- [[wechat-md-server-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/ai_adapters.py`
- wechat-md-server 项目源码 `app/config.py`

---

**引用来源**：[[wechat-md-server-wiki]]、`app/ai_adapters.py`、`app/config.py`
