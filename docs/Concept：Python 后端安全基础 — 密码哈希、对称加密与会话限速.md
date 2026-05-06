---
type: brick_note
brick_type: concept
status: draft
domain: backend-development
tags:
  - concept-brick
  - security
  - authentication
  - encryption
  - python
summary: 理解 Python 后端安全的三块基石：PBKDF2 密码哈希（不可逆存储）、Fernet 对称加密（可逆敏感数据保护）、基于滑动窗口的登录限速（防暴力破解）
source_wiki:
  - "[[project-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Concept：Python 后端安全基础 — 密码哈希、对称加密与会话限速

> [!abstract]
> 这个 Concept-brick 帮助理解后端安全的三块基石：**密码不能明文存，要用哈希**（PBKDF2）；**敏感配置不能明文存，要用加密**（Fernet）；**登录接口不能无限试，要限速**（滑动窗口）。三个机制各管一件事，组合起来构成后端安全的基础层。

---

## 1 这个概念是什么

后端安全有三件最基础的事：

1. **密码存储**：用户密码不能明文保存。用哈希函数把密码变成不可逆的摘要值，即使数据库泄露也无法还原密码。
2. **敏感数据加密**：API Key、Token 等配置项需要可逆加密存储（保存时加密、使用时解密），不能明文写在 JSON 文件里。
3. **登录限速**：防止暴力破解，限制单位时间内的登录失败次数。

这三件事相互独立，但在一个完整的后端项目中缺一不可。

## 2 为什么重要

| 场景 | 不做的后果 | 做了的好处 |
| --- | --- | --- |
| 密码明文存储 | 数据库泄露 → 所有用户密码暴露 | 泄露也只暴露哈希值，无法反推密码 |
| 敏感配置明文存储 | 配置文件泄露 → API Key、Token 全部暴露 | 泄露只看到密文，需要主密钥才能解密 |
| 不限速登录 | 攻击者可以每秒尝试上万次密码 | 5 次失败后锁定 15 分钟，暴力破解不可行 |

## 3 核心原理

### 3.1 密码哈希（PBKDF2）

```text
用户注册/修改密码时：
  原始密码 → 加盐（随机 salt） → PBKDF2 迭代 200,000 次 → 得到哈希值
  存储：pbkdf2_sha256$200000$盐$哈希值

用户登录时：
  输入密码 + 存储的盐 → PBKDF2 迭代同样的次数 → 得到新的哈希值
  对比新的哈希值和存储的哈希值 → 相同则密码正确
```

关键点：
- **加盐（salt）**：每个用户的盐不同，同样的密码哈希结果不同，防止彩虹表攻击
- **迭代次数高**：200,000 次迭代让单次哈希需要约 0.2 秒，暴力破解成本极高
- **不可逆**：哈希是单向函数，无法从哈希值反推密码
- **格式化存储**：`算法$迭代次数$盐$哈希值`，一条字符串包含所有信息

> [!example]- 代码实现
>
> ```python
> import hashlib, secrets, hmac
>
> def hash_password(password: str, salt: str | None = None) -> str:
>     password_salt = salt or secrets.token_hex(16)
>     digest = hashlib.pbkdf2_hmac(
>         "sha256",
>         password.encode("utf-8"),
>         password_salt.encode("utf-8"),
>         200_000,  # 迭代次数
>     )
>     return f"pbkdf2_sha256$200000${password_salt}${digest.hex()}"
>
> def verify_password(password: str, stored_hash: str) -> bool:
>     scheme, iterations_text, salt, expected = stored_hash.split("$", 3)
>     digest = hashlib.pbkdf2_hmac(
>         "sha256",
>         password.encode("utf-8"),
>         salt.encode("utf-8"),
>         int(iterations_text),
>     ).hex()
>     # hmac.compare_digest 防止时序攻击
>     return hmac.compare_digest(digest, expected)
> ```

### 3.2 对称加密（Fernet）

```text
加密流程：
  明文 → Fernet.encrypt() → 密文
  存储：enc::密文

解密流程：
  读取 enc:: 开头的值 → 去掉前缀 → Fernet.decrypt() → 明文
```

关键点：
- **主密钥（Master Key）**：通过环境变量传入，不存储在代码或配置文件中
- **密钥派生**：Master Key → SHA-256 → Base64 编码 → Fernet 密钥
- **enc:: 前缀**：用来区分"已加密"和"未加密"的值
- **可逆**：和哈希不同，加密是可逆的——加密后可以解密还原

> [!example]- 代码实现
>
> ```python
> import base64, hashlib, os
> from cryptography.fernet import Fernet
>
> def _get_fernet() -> Fernet:
>     master_key = os.environ.get("WECHAT_MD_APP_MASTER_KEY", "").strip()
>     digest = hashlib.sha256(master_key.encode()).digest()
>     return Fernet(base64.urlsafe_b64encode(digest))
>
> def encrypt_secret(value: str) -> str:
>     token = _get_fernet().encrypt(value.encode()).decode()
>     return f"enc::{token}"
>
> def decrypt_secret(value: str | None) -> str:
>     value = (value or "").strip()
>     if not value or not value.startswith("enc::"):
>         return value  # 未加密的值直接返回
>     return _get_fernet().decrypt(value[5:].encode()).decode()
> ```

### 3.3 登录限速（滑动窗口）

```text
每次登录失败：
  记录时间戳到队列

检查是否允许登录：
  1. 清理超过 10 分钟的旧记录
  2. 检查是否在锁定期内
  3. 如果最近 10 分钟内失败超过 5 次 → 锁定 15 分钟
```

关键点：
- **滑动窗口**：不是固定时间窗口，而是"过去 10 分钟内的失败次数"
- **队列（deque）**：用 `collections.deque` 存储失败时间戳，自动清理过期记录
- **线程安全**：用 `threading.Lock` 保护共享状态
- **锁定时间**：达到阈值后锁定 15 分钟，锁定期间不允许任何登录尝试

> [!example]- 代码实现
>
> ```python
> import threading, time
> from collections import deque
>
> WINDOW_SECONDS = 10 * 60   # 10 分钟窗口
> FAILURE_THRESHOLD = 5       # 最多允许 5 次失败
> LOCK_SECONDS = 15 * 60      # 锁定 15 分钟
>
> _attempts: dict[str, deque] = {}
> _locks: dict[str, float] = {}
> _lock = threading.Lock()
>
> def check_login_allowed(identifier: str) -> tuple[bool, int | None]:
>     now = time.time()
>     with _lock:
>         # 清理过期记录
>         attempts = _attempts.get(identifier, deque())
>         while attempts and now - attempts[0] > WINDOW_SECONDS:
>             attempts.popleft()
>         # 检查锁定
>         locked_until = _locks.get(identifier)
>         if locked_until and locked_until > now:
>             return False, int(locked_until - now)
>         return True, None
>
> def record_login_failure(identifier: str) -> tuple[bool, int | None]:
>     now = time.time()
>     with _lock:
>         _attempts.setdefault(identifier, deque()).append(now)
>         # 清理后检查阈值
>         if len(_attempts.get(identifier, ())) > FAILURE_THRESHOLD:
>             _locks[identifier] = now + LOCK_SECONDS
>             return False, LOCK_SECONDS  # 被锁定了
>         return True, None
> ```

## 4 三个机制的关系

```text
用户登录流程：
  1. check_login_allowed(ip)           → 限速检查（防暴力破解）
  2. verify_password(input, stored)     → 密码验证（PBKDF2 哈希对比）
  3. 登录失败 → record_login_failure(ip) → 记录失败次数
  4. 登录成功 → clear_login_failures(ip) → 清除失败记录

配置存储流程：
  用户在 Web 界面填入 API Key
  → encrypt_secret(api_key)            → 加密存储（Fernet）
  → 写入 runtime-config.json           → 文件中看到 enc::xxxxx

配置读取流程：
  启动时读取 runtime-config.json
  → decrypt_secret(stored_value)        → 解密还原（Fernet）
  → 注入到 Settings 对象中              → 运行时使用明文值
```

## 5 常见误区

| 误区 | 正确理解 |
| --- | --- |
| "哈希就是加密" | 哈希是**不可逆**的（只能验证），加密是**可逆**的（可以还原） |
| "密码可以直接 MD5 存储" | MD5 太快且已被破解。用 PBKDF2 + 高迭代次数 + 随机盐 |
| "加密和哈希用同一个密钥" | 密码哈希不需要密钥（用盐）；对称加密才需要密钥 |
| "限速只检查 IP 就够了" | 还要考虑账号维度的限速（防止针对特定账号的攻击） |
| "hmac.compare_digest 和 == 一样" | `compare_digest` 是**恒定时间比较**，防止时序攻击；`==` 不是 |

---

## 6 来源

相关 Wiki：
- [[project-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/auth.py`（193 行，完整实现）
- Python `hashlib.pbkdf2_hmac` 文档
- `cryptography.fernet` 文档

---

**引用来源**：[[project-wiki]]、`app/auth.py`
