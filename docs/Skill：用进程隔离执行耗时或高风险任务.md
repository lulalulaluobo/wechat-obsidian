---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - multiprocessing
  - process-isolation
  - python
  - timeout
summary: 用 multiprocessing 在独立子进程中执行耗时或可能崩溃的任务，带硬超时控制、结果回传和异常捕获，保护主进程不被拖垮
input:
  - 需要隔离执行的任务函数
  - 任务的超时时间
output:
  - 子进程的执行结果，或超时/崩溃的错误信息
source_wiki:
  - "[[project-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：用进程隔离执行耗时或高风险任务

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 理解并实现进程隔离模式：在子进程中执行耗时或可能崩溃的任务，主进程通过 Queue 获取结果，带硬超时保护，确保主进程永远不会被单个任务拖垮。

---

## 1 一句话用途

把耗时或高风险的任务放到独立子进程中执行，主进程设置硬超时，即使子进程卡死或崩溃，主进程也能正常继续。

## 2 什么时候使用

> [!tip] 适用

- 任务执行时间不确定，可能卡死（如网络请求、外部程序调用）
- 任务可能因为内存溢出、段错误等原因崩溃
- 多个任务并发执行，一个任务崩溃不能影响其他任务
- Web 服务中，请求处理不能被单个耗时任务阻塞超过某个上限

> [!warning] 不适用

- 任务执行时间很短且可控（< 1 秒）——直接在主进程中执行
- 任务只是 I/O 等待（如 HTTP 请求）——用 `asyncio` 或 `threading` 更轻量
- 需要大量并发（成百上千个）——进程开销大，应该用线程池或协程
- 任务之间需要共享大量内存——进程间不共享内存，需要用 Queue 或共享内存

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| 要隔离执行的任务函数 | 是 | 无 | 否 | 如 `convert_article()`、`process_data()` |
| 硬超时时间（秒） | 是 | 180 | 是 | AI 可以 grep `hard_timeout` 定位 |
| 任务函数的参数 | 是 | 无 | 否 | 需要是可序列化的（能通过 pickle 传递） |
| 返回值类型 | 是 | 无 | 否 | 也需要可序列化 |
| 是否需要并发执行 | 否 | 否 | 是 | AI 可以检查是否用了进程池 |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 进程 vs 线程 | 任务是 CPU 密集型或可能真正崩溃（段错误）→ 进程；只是 I/O 等待 → 线程或协程 | 进程隔离更彻底，但开销更大 |
| spawn vs fork | **用 `spawn`**。`fork` 会复制父进程的所有内存和线程状态，可能导致死锁 | `mp.get_context("spawn")` 更安全 |
| 结果传递方式 | 用 `multiprocessing.Queue`。不要用共享变量或管道 | Queue 是最通用和安全的进程间通信方式 |
| 超时处理策略 | 先 `terminate()`（发 SIGTERM），等 5 秒；还活着就 `kill()`（发 SIGKILL） | 确保无论什么情况子进程都能被清理 |
| 开关控制 | 提供配置项让用户决定是否启用进程隔离 | 开发时关闭隔离方便调试，生产时开启保护主进程 |

> [!important] 关键原则
> 进程隔离的核心目标是**保护主进程**。不管子进程发生什么（卡死、崩溃、OOM），主进程都必须能在超时后继续运行。所以：必须设置硬超时，必须处理超时后的清理，必须通过 Queue 传递结果而不是共享内存。

---

## 5 直接照做

### 5.1 准备

1. **确定要隔离的任务**

```text
示例：convert_article(url)
- 执行时间：通常 5-30 秒，极端情况可能卡死
- 风险：网络请求可能挂起、HTML 解析可能崩溃
- 要求：即使卡死，主进程也要在 180 秒后继续
```

2. **定位需要改的文件**

```bash
grep -n "multiprocessing\|Process\|Queue\|isolated\|_invoke" app/services.py
```

### 5.2 执行

#### 位置 1：初始化 multiprocessing 上下文（app/services.py）

```python
import multiprocessing as mp
import queue
import traceback

# 关键：用 spawn 而不是 fork
# fork 会复制父进程的线程锁状态，可能导致子进程死锁
_mp_context = mp.get_context("spawn")
```

#### 位置 2：定义 worker 注册表（app/services.py）

把所有可能被隔离执行的函数注册到一个字典中：

```python
# 注册表：worker_name → worker_function
_ISOLATED_WORKERS: dict[str, Any] = {
    "_isolated_single_conversion_worker": _isolated_single_conversion_worker,
}
```

> [!note] 为什么需要注册表？
> `spawn` 模式下，子进程会重新导入模块。注册表让子进程通过字符串名称找到对应的函数，而不需要传递函数对象。

#### 位置 3：实现 worker 入口函数（app/services.py）

这是在子进程中运行的第一个函数，负责调用实际的任务函数并通过 Queue 回传结果：

```python
def _isolated_worker_entry(worker_name: str, kwargs: dict, result_queue) -> None:
    """子进程入口：查找 worker → 执行 → 通过 Queue 回传结果"""
    try:
        worker = _ISOLATED_WORKERS[worker_name]
    except KeyError as error:
        result_queue.put({
            "ok": False,
            "error_type": "RuntimeError",
            "error": f"未知隔离 worker: {worker_name}",
        })
        raise RuntimeError(f"未知隔离 worker: {worker_name}") from error

    try:
        # 执行实际任务，成功时把结果放入 Queue
        result = worker(**kwargs)
        result_queue.put({"ok": True, "result": result})
    except Exception as error:
        # 失败时把错误信息放入 Queue
        result_queue.put({
            "ok": False,
            "error_type": error.__class__.__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        })
```

#### 位置 4：实现进程调用和超时控制（app/services.py）

这是核心函数：启动子进程、等待超时、清理、读取结果：

```python
def _invoke_isolated_worker(
    worker_name: str,
    kwargs: dict,
    *,
    timeout_seconds: int,
) -> dict:
    """在子进程中执行任务，带硬超时保护"""
    # 1. 创建 Queue 用于进程间通信
    result_queue = _mp_context.Queue()

    # 2. 创建并启动子进程
    process = _mp_context.Process(
        target=_isolated_worker_entry,
        args=(worker_name, kwargs, result_queue),
    )
    process.start()

    # 3. 等待子进程完成（带超时）
    process.join(timeout_seconds)

    # 4. 超时处理
    if process.is_alive():
        # 先尝试优雅终止（SIGTERM）
        process.terminate()
        process.join(5)
        # 如果还活着，强制杀死（SIGKILL）
        if process.is_alive():
            process.kill()
            process.join(1)
        raise TimeoutError(f"任务硬超时（{timeout_seconds}s）")

    # 5. 读取结果
    try:
        payload = result_queue.get(timeout=1)
    except queue.Empty as error:
        # Queue 为空说明子进程没来得及写入结果
        if process.exitcode and process.exitcode != 0:
            raise RuntimeError(
                f"子进程异常退出（exit={process.exitcode}）"
            ) from error
        raise RuntimeError("子进程未返回结果") from error

    # 6. 解析结果
    if payload.get("ok"):
        return dict(payload.get("result") or {})

    # 7. 还原异常类型
    error_message = str(payload.get("error") or "隔离执行失败")
    error_type = str(payload.get("error_type") or "RuntimeError")
    if error_type == "TimeoutError":
        raise TimeoutError(error_message)
    raise RuntimeError(error_message)
```

> [!example]- 超时处理的完整流程
>
> ```text
> process.start()                    # 启动子进程
>   ↓
> process.join(timeout_seconds)      # 等待，最多 timeout_seconds 秒
>   ↓
> process.is_alive()?                # 还活着吗？
>   ├── No → 读取 Queue 结果
>   └── Yes →
>       process.terminate()           # 发 SIGTERM
>       process.join(5)               # 等 5 秒让它退出
>       process.is_alive()?           # 还活着吗？
>         ├── No → 抛 TimeoutError
>         └── Yes →
>             process.kill()          # 发 SIGKILL（强制杀死）
>             process.join(1)         # 等 1 秒
>             抛 TimeoutError
> ```

#### 位置 5：封装调用接口（app/services.py）

给调用方提供一个简洁的接口：

```python
def _run_single_conversion_isolated(*, url: str, timeout: int, hard_timeout_seconds: int, ...) -> dict:
    """对外接口：把参数打包，调用隔离执行"""
    return _invoke_isolated_worker(
        "_isolated_single_conversion_worker",
        {
            "url": url,
            "timeout": timeout,
            # ... 其他参数
        },
        timeout_seconds=hard_timeout_seconds,
    )
```

#### 位置 6：添加开关控制（app/services.py + app/config.py）

让用户可以开启/关闭进程隔离：

```python
# config.py 中
single_conversion_isolation_enabled: bool = True
single_conversion_hard_timeout_seconds: int = 180

# services.py 中，execute_single_conversion 根据开关决定执行方式
def execute_single_conversion(url: str, ...) -> dict:
    settings = get_settings()
    if settings.single_conversion_isolation_enabled:
        return _run_single_conversion_isolated(
            url=url, ..., hard_timeout_seconds=settings.single_conversion_hard_timeout_seconds,
        )
    # 关闭隔离时直接在主进程执行（方便调试）
    return _run_single_conversion(url=url, ...)
```

### 5.3 验证

1. **超时测试**

```python
# 模拟一个会卡死的任务
def _isolated_sleep_worker(seconds: int = 999) -> dict:
    import time
    time.sleep(seconds)
    return {"status": "done"}

_ISOLATED_WORKERS["_isolated_sleep_worker"] = _isolated_sleep_worker

# 测试：2 秒超时
try:
    _invoke_isolated_worker("_isolated_sleep_worker", {"seconds": 999}, timeout_seconds=2)
    assert False, "应该超时"
except TimeoutError as e:
    assert "硬超时" in str(e)
    print("超时测试通过")
```

2. **崩溃测试**

```python
# 模拟一个会崩溃的任务
def _isolated_crash_worker() -> dict:
    raise ValueError("模拟崩溃")

_ISOLATED_WORKERS["_isolated_crash_worker"] = _isolated_crash_worker

try:
    _invoke_isolated_worker("_isolated_crash_worker", {}, timeout_seconds=10)
    assert False, "应该报错"
except RuntimeError as e:
    assert "模拟崩溃" in str(e)
    print("崩溃测试通过")
```

3. **正常执行测试**

```python
def _isolated_echo_worker(message: str = "") -> dict:
    return {"message": message}

_ISOLATED_WORKERS["_isolated_echo_worker"] = _isolated_echo_worker

result = _invoke_isolated_worker("_isolated_echo_worker", {"message": "hello"}, timeout_seconds=10)
assert result["message"] == "hello"
print("正常执行测试通过")
```

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| 使用 spawn 上下文 | `grep "get_context.*spawn" app/services.py` | `mp.get_context("spawn")` 存在 |
| worker 注册表存在 | `grep "_ISOLATED_WORKERS" app/services.py` | 字典存在且包含 worker 函数 |
| 入口函数处理成功和失败 | `grep "_isolated_worker_entry" app/services.py` | 函数中有 `ok: True` 和 `ok: False` 两种情况 |
| 超时处理完整 | `grep "terminate\|kill" app/services.py` | 先 terminate 再 kill 的两级清理 |
| 结果通过 Queue 传递 | `grep "result_queue" app/services.py` | Queue.put 和 Queue.get 都存在 |
| 有开关控制 | `grep "isolation_enabled" app/config.py` | 配置项存在，且 execute 函数中有分支 |
| 超时测试通过 | 运行超时测试用例 | 抛出 TimeoutError |
| 崩溃测试通过 | 运行崩溃测试用例 | 抛出 RuntimeError，错误信息包含原始异常 |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| `RuntimeError: 未知隔离 worker` | worker 函数是否注册到 `_ISOLATED_WORKERS` | 在注册表中添加 worker |
| `RuntimeError: 子进程未返回结果` | 子进程是否在写入 Queue 之前就崩溃了 | 检查 worker 函数是否有未捕获的异常 |
| `RuntimeError: 子进程异常退出（exit=-N）` | 负数 exit code 表示被信号杀死（如 -9 = SIGKILL, -11 = SIGSEGV） | 检查 worker 是否有段错误或内存问题 |
| 子进程启动后立即退出 | 用 `spawn` 时，worker 函数和参数必须能被 pickle 序列化 | 检查参数中是否包含不可序列化的对象（如 lambda、数据库连接） |
| 超时后进程不退出 | `terminate()` 后进程仍然活着 | 确认有 `kill()` 兜底逻辑 |
| 导入错误 | `spawn` 模式下子进程会重新导入模块 | 确保所有需要的依赖在子进程中也可导入 |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我给项目实现进程隔离执行。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先判断：要隔离的任务是什么？超时时间是多少？
> 3. 如果缺少关键参数（任务函数、超时时间），必须先问我。
> 4. 按以下顺序实现：
>    a. 初始化 spawn 上下文
>    b. 实现 worker 入口函数
>    c. 实现进程调用和超时控制
>    d. 封装调用接口
>    e. 添加开关控制
> 5. 实现完后，帮我生成超时测试、崩溃测试和正常执行测试。
> 6. 结束时按"成功标准"逐项验证，并告诉我每项是否通过。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - 要隔离的任务：{{描述任务函数}}
> - 超时时间：{{秒数}}
> ```

---

## 9 来源

相关 Wiki：
- [[project-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/services.py`（_invoke_isolated_worker 函数、_isolated_worker_entry 函数、execute_single_conversion 函数）
- Python `multiprocessing` 文档：`https://docs.python.org/3/library/multiprocessing.html`

---

**引用来源**：[[project-wiki]]、`app/services.py`
