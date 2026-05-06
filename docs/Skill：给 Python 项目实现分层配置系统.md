---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - configuration
  - python
  - security
summary: 实现"环境变量 → 运行时配置文件 → 代码默认值"的三层配置系统，支持敏感字段加密存储和 Web 界面动态修改
input:
  - 项目需要的配置项列表
  - 哪些字段是敏感字段（需要加密）
output:
  - 可工作的分层配置系统，支持动态修改和加密存储
source_wiki:
  - "[[wechat-md-server-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：给 Python 项目实现分层配置系统

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 理解并实现一个三层配置系统："环境变量 → 运行时配置文件 → 代码默认值"，每层覆盖上一层，敏感字段用 Fernet 加密存储，支持 Web 界面动态修改。

---

## 1 一句话用途

给 Python 项目实现一个分层配置系统，让配置可以在环境变量、JSON 文件和代码默认值之间灵活覆盖，敏感字段加密存储，运行时可动态修改。

## 2 什么时候使用

> [!tip] 适用

- 项目有多种部署环境（本地开发 / Docker / NAS / VPS），配置来源不同
- 需要通过 Web 界面动态修改配置，不想重启服务
- 配置中包含敏感字段（API Key、Token、密码），不能明文存储在文件里
- 需要导入/导出配置（迁移部署场景）

> [!warning] 不适用

- 项目只有少量配置（<10 个），且全部来自环境变量——用 `os.environ.get()` 就够了
- 配置只需要在启动时读取一次，不需要运行时修改——用 `.env` 文件就够了
- 不需要加密存储——三层覆盖的模式仍然可用，但加密部分可以跳过

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| 配置项列表 | 是 | 无 | 否 | 包括字段名、类型、默认值 |
| 敏感字段清单 | 是 | 无 | 是 | AI 可以根据字段名推断（含 token/key/secret/password 的字段） |
| 配置文件路径 | 是 | `data/runtime-config.json` | 是 | AI 可以 grep `RUNTIME_CONFIG` 定位 |
| 加密主密钥 | 条件必需 | 无 | 否 | 环境变量 `WECHAT_MD_APP_MASTER_KEY` |
| frozen dataclass | 是 | 是 | 是 | 用 `@dataclass(frozen=True)` 防止运行时意外修改 |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 字段类型 | 字符串、整数、布尔值、元组——用 `@dataclass` 定义；嵌套结构——用 JSON 子对象 | 影响 `_normalize_runtime_config` 中的解析逻辑 |
| 哪些字段是敏感的 | 字段名包含 `token`、`key`、`secret`、`password`、`cookie` 的 → 加密存储 | 影响 `SECRET_FIELDS` 集合和加密/解密逻辑 |
| 覆盖优先级 | 环境变量 > 配置文件 > 代码默认值 | 影响 `get_settings()` 中每个字段的读取顺序 |
| frozen vs 可变 | 用 `frozen=True`，每次修改配置后重新创建 Settings 对象 | 影响是否需要全局 Settings 单例和刷新机制 |
| 配置文件格式 | JSON——可以直接读写，Python 标准库支持 | 影响读写函数的实现 |

> [!important] 关键原则
> 三层覆盖的核心逻辑：每个字段读取时，先看环境变量有没有 → 再看配置文件有没有 → 最后用代码默认值。优先级：**环境变量 > 配置文件 > 默认值**。

---

## 5 直接照做

### 5.1 准备

1. **列出所有配置项**

```text
必填项（启动时必须有）：
  - default_output_dir: Path
  - runtime_config_path: Path
  - username: str
  - password_hash: str
  - session_secret: str

可选功能（有默认值）：
  - default_timeout: int = 30
  - image_mode: str = "wechat_hotlink"
  - telegram_enabled: bool = False
  - ai_enabled: bool = False

敏感字段（需要加密）：
  - fns_token, telegram_bot_token, feishu_app_secret, ...
```

2. **定位配置文件**

```bash
grep -n "runtime_config\|RUNTIME_CONFIG" app/config.py
```

### 5.2 执行

#### 位置 1：定义数据结构（app/config.py）

用 frozen dataclass 定义所有配置字段：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    # 必填项
    default_output_dir: Path
    runtime_config_path: Path
    username: str
    password_hash: str
    session_secret: str

    # 可选功能
    default_timeout: int = 30
    image_mode: str = "wechat_hotlink"
    cleanup_temp_on_success: bool = True

    # 敏感字段
    fns_token: str | None = None
    telegram_bot_token: str | None = None

    # 派生属性（根据其他字段计算）
    @property
    def fns_enabled(self) -> bool:
        return bool(self.fns_base_url and self.fns_token and self.fns_vault)
```

> [!example]- 为什么用 frozen=True？
>
> ```text
> frozen=True 让 Settings 对象创建后不可修改。
> 好处：
> 1. 防止运行时意外修改配置（应该通过 save_runtime_config 修改文件，然后重新读取）
> 2. 可以作为 dict 的 key（hashable）
> 3. 明确表达"配置是只读的"这个语义
> ```

#### 位置 2：定义敏感字段集合（app/config.py）

```python
# 敏感字段：这些字段在 JSON 文件中用 enc:: 前缀加密存储
SECRET_FIELDS = {
    "fns_token",
    "telegram_bot_token",
    "telegram_webhook_secret",
    "feishu_app_secret",
}
```

#### 位置 3：实现三层读取（app/config.py 的 `get_settings()`）

这是核心函数。每个字段都按照"环境变量 → 配置文件 → 默认值"的顺序读取：

```python
def get_settings() -> Settings:
    # 读取配置文件（不存在则返回空 dict）
    runtime_config_path = get_runtime_config_path()
    runtime_values = load_runtime_config(runtime_config_path)
    user_settings = runtime_values["user_settings"]

    # 三层覆盖示例：
    # 优先级：环境变量 > 配置文件 > 默认值
    fns_base_url = str(
        user_settings.get("fns_base_url")       # 第 2 层：配置文件
        or os.environ.get("WECHAT_MD_FNS_BASE_URL")  # 第 1 层：环境变量
        or ""                                          # 第 3 层：默认值
    ).strip() or None

    fns_token = str(
        user_settings.get("fns_token")
        or os.environ.get("WECHAT_MD_FNS_TOKEN")
        or ""
    ).strip() or None

    # 解密敏感字段（如果以 enc:: 开头）
    # decrypt_secret 会检查 enc:: 前缀并解密

    # 构建 frozen Settings 对象
    return Settings(
        default_output_dir=output_dir,
        runtime_config_path=runtime_config_path,
        fns_base_url=fns_base_url.rstrip("/") if fns_base_url else None,
        fns_token=fns_token,
        ...
    )
```

> [!example]- 三层覆盖的模式
>
> ```python
> # 字符串字段
> value = str(
>     config_file.get("field_name")              # 配置文件
>     or os.environ.get("ENV_VAR_NAME")          # 环境变量
>     or "default_value"                         # 默认值
> ).strip() or None
>
> # 布尔字段
> value = _as_bool(
>     os.environ.get("ENV_VAR")                  # 环境变量优先
>     if os.environ.get("ENV_VAR") is not None
>     else config_file.get("field_name"),        # 再看配置文件
>     default=True,                              # 默认值
> )
>
> # 整数字段
> value = _as_int(
>     os.environ.get("ENV_VAR")
>     if os.environ.get("ENV_VAR") is not None
>     else config_file.get("field_name"),
>     default=30,
>     minimum=1,
> )
> ```

#### 位置 4：实现配置文件读写（app/config.py）

```python
def load_runtime_config(path: Path | None = None) -> dict:
    """读取 JSON 配置文件。不存在则返回空结构。"""
    config_path = path or get_runtime_config_path()
    if config_path.exists():
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        raw_data = {}
    # 标准化：补全缺失的字段和子结构
    normalized = _normalize_runtime_config(raw_data)
    # 回写：确保文件结构完整
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_runtime_config(config_path, normalized)
    return normalized

def save_runtime_config(payload: dict, clear_fields: list[str] | None = None) -> dict:
    """保存配置：读取当前值 → 合并新值 → 加密敏感字段 → 写入文件"""
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)

    # 合并新值到 current 中
    for field in FNS_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        user_settings[field] = str(raw_value).strip()

    # 敏感字段：明文值在保存时加密
    # clear_fields 用于 Web 界面"清空某个敏感字段"

    _write_runtime_config(config_path, current)
    return current
```

#### 位置 5：实现敏感字段加密（app/auth.py）

```python
from cryptography.fernet import Fernet
import base64, hashlib

def _get_fernet_key(master_key: str) -> bytes:
    """从主密钥派生 Fernet 密钥"""
    digest = hashlib.sha256(master_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)

def encrypt_secret(plaintext: str, master_key: str) -> str:
    """加密：返回 enc:: 前缀的密文"""
    if not plaintext:
        return ""
    key = _get_fernet_key(master_key)
    encrypted = Fernet(key).encrypt(plaintext.encode())
    return f"enc::{encrypted.decode()}"

def decrypt_secret(ciphertext: str, master_key: str) -> str:
    """解密：识别 enc:: 前缀并解密"""
    if not ciphertext or not ciphertext.startswith("enc::"):
        return ciphertext or ""
    key = _get_fernet_key(master_key)
    encrypted = ciphertext[5:]  # 去掉 enc:: 前缀
    return Fernet(key).decrypt(encrypted.encode()).decode()
```

#### 位置 6：实现导入/导出（app/config.py）

```python
# 导出：读取当前 Settings → 组装 payload（敏感字段脱敏）
def export_settings() -> dict:
    settings = get_settings()
    payload = _build_settings_export_payload(settings)
    return {
        "schema_version": 1,
        "app": "your-app-name",
        "settings": payload,
    }

# 导入：校验 schema → 过滤非法字段 → 保存
def import_settings_package(package: dict) -> dict:
    if int(package.get("schema_version") or 0) != 1:
        raise ValueError("配置包版本不匹配")
    settings_payload = package.get("settings")
    # 过滤：只允许白名单中的字段
    invalid_fields = [f for f in settings_payload if f not in SETTINGS_EXPORT_ALLOWED_FIELDS]
    # 保存
    payload = _build_runtime_payload_from_import(settings_payload)
    save_runtime_config(payload)
    return {"status": "success"}
```

### 5.3 配置

1. 环境变量在 `.env` 文件或 Docker Compose 的 `environment` 中设置
2. 运行时配置在 Web 管理界面修改（修改后自动保存到 JSON 文件）
3. 每次请求都会调用 `get_settings()` 重新读取配置

### 5.4 验证

1. **三层覆盖测试**

```python
# 测试 1：只有默认值
os.environ.pop("WECHAT_MD_FNS_BASE_URL", None)
# 配置文件中也没有 fns_base_url
assert get_settings().fns_base_url is None

# 测试 2：配置文件覆盖默认值
save_runtime_config({"fns_base_url": "https://fns.example.com"})
assert get_settings().fns_base_url == "https://fns.example.com"

# 测试 3：环境变量覆盖配置文件
os.environ["WECHAT_MD_FNS_BASE_URL"] = "https://override.example.com"
assert get_settings().fns_base_url == "https://override.example.com"
```

2. **加密测试**

```python
# 敏感字段保存后应该是 enc:: 开头
save_runtime_config({"fns_token": "my-secret-token"})
config = load_runtime_config()
# JSON 文件中存储的应该是加密值
# get_settings() 应该能正确解密
assert get_settings().fns_token == "my-secret-token"
```

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| Settings 是 frozen dataclass | `grep "frozen=True" app/config.py` | `@dataclass(frozen=True)` 存在 |
| 每个字段有三种来源 | 检查 `get_settings()` | 每个字段都检查了环境变量、配置文件和默认值 |
| 敏感字段已标记 | `grep "SECRET_FIELDS" app/config.py` | `SECRET_FIELDS` 集合包含所有敏感字段 |
| 加密函数存在 | `grep "encrypt_secret\|decrypt_secret" app/auth.py` | 两个函数都存在 |
| JSON 文件可读写 | `grep "load_runtime_config\|save_runtime_config" app/config.py` | 两个函数都存在 |
| 配置修改后立即生效 | Web 界面修改后，下一次请求使用新值 | `get_settings()` 每次调用都重新读取 |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| 配置读取到空值 | `get_settings()` 中该字段的读取顺序 | 确认三层覆盖逻辑：env → file → default |
| JSON 解析失败 | 配置文件是否被手动编辑损坏 | 检查 JSON 语法，或删除文件让系统重建 |
| 加密解密失败 | `WECHAT_MD_APP_MASTER_KEY` 是否改变过 | 主密钥一旦改变，旧的加密值无法解密 |
| 环境变量不生效 | 变量名是否正确、是否在容器中设置 | `print(os.environ.get("ENV_VAR"))` 检查 |
| frozen dataclass 报错 | 是否在运行时尝试修改 Settings 属性 | 应通过 `save_runtime_config` 修改，而非直接赋值 |
| 敏感字段明文存储 | `save_runtime_config` 是否调用了加密 | 检查写入 JSON 前是否有 `encrypt_secret` 调用 |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我给项目实现一个三层配置系统。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先列出项目需要的所有配置项，区分：必填项、可选项、敏感字段。
> 3. 如果缺少关键参数（配置项列表、敏感字段清单），必须先问我。
> 4. 按以下顺序实现：
>    a. 定义 Settings frozen dataclass
>    b. 定义 SECRET_FIELDS 集合
>    c. 实现 load_runtime_config 和 save_runtime_config
>    d. 实现 get_settings 三层覆盖
>    e. 实现 encrypt_secret 和 decrypt_secret
> 5. 实现完后，帮我生成三层覆盖的测试用例。
> 6. 结束时按"成功标准"逐项验证，并告诉我每项是否通过。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - 配置项列表：{{列出所有需要的配置项}}
> - 敏感字段：{{列出需要加密的字段}}
> ```

---

## 9 来源

相关 Wiki：
- [[wechat-md-server-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/config.py`（Settings dataclass、get_settings 函数、load/save_runtime_config 函数）
- wechat-md-server 项目源码 `app/auth.py`（encrypt_secret、decrypt_secret 函数）

---

**引用来源**：[[wechat-md-server-wiki]]、`app/config.py`、`app/auth.py`
