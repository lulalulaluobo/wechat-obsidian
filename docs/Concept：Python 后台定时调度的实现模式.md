---
type: brick_note
brick_type: concept
status: draft
domain: backend-development
tags:
  - concept-brick
  - scheduler
  - threading
  - python
summary: 理解用 threading + Event + Lock 实现后台定时调度的模式：30 秒轮询循环、数据库存储调度配置、按频率判断是否到期、非阻塞锁防止重叠执行
source_wiki:
  - "[[wechat-md-server-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Concept：Python 后台定时调度的实现模式

> [!abstract]
> 这个 Concept-brick 帮助理解如何用 Python 标准库实现一个轻量级后台调度器：**守护线程 + 30 秒轮询 + 数据库存储调度配置 + 非阻塞锁防重叠 + 多频率支持（每天/每周/每月/按小时间隔）**。

---

## 1 这个概念是什么

后台定时调度就是在主程序运行的同时，有一个后台线程每隔一段时间"醒来"检查：

```text
是否到了该执行某个任务的时间？
  → 是：执行任务
  → 否：继续睡觉
```

不需要 cron、不需要 Celery、不需要 Redis，只用 Python 标准库的 `threading` 模块就能实现。

## 2 为什么重要

| 场景 | 为什么需要调度器 |
| --- | --- |
| 定时同步公众号文章 | 每天早上 9 点拉取新文章列表 |
| 定时入库待处理文章 | 每隔 2 小时处理一批待入库的文章 |
| 暂停/恢复调度 | 用户临时暂停同步，之后再恢复 |
| 修改频率不重启 | 用户在 Web 界面修改频率，下次 tick 自动生效 |

## 3 核心原理

### 3.1 整体架构

```text
主线程（FastAPI 服务）
  └── start_scheduler()  ← 在 FastAPI lifespan 中启动
        └── 守护线程 _scheduler_loop()
              └── 每 30 秒执行 _run_scheduler_tick()
                    ├── 检查 source_sync_schedule 是否到期
                    │     └── 到期 → 加锁 → 执行同步 → 释放锁
                    └── 检查 article_ingest_schedule 是否到期
                          └── 到期 → 加锁 → 执行入库 → 释放锁
```

### 3.2 五个关键组件

**1. 守护线程**

```python
_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()  # 用来通知线程停止

def start_scheduler() -> None:
    global _scheduler_thread
    with _scheduler_lock:
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            name="scheduler",
            daemon=True,  # 守护线程：主线程退出时自动结束
        )
        _scheduler_thread.start()
```

**2. 轮询循环**

```python
def _scheduler_loop() -> None:
    while not _scheduler_stop.is_set():       # 检查是否收到停止信号
        try:
            _run_scheduler_tick()              # 执行一次调度检查
        except Exception as error:
            print(f"[scheduler] tick failed: {error}")
        _scheduler_stop.wait(30)              # 等 30 秒（或被停止信号唤醒）
```

> [!note] 为什么用 `Event.wait(30)` 而不是 `time.sleep(30)`？
> `Event.wait(30)` 可以被 `_scheduler_stop.set()` 立即唤醒，实现快速停止。`time.sleep(30)` 必须等满 30 秒。

**3. 到期判断**

```python
def _is_due(payload: dict) -> bool:
    """检查调度是否到期"""
    now = 当前时间（考虑时区）

    # 检查是否暂停中
    if paused_until and now < paused_until:
        return False

    # 按小时间隔模式
    if interval_hours > 0:
        return now >= last_run + interval_hours

    # 固定时间模式（每天/每周/每月）
    scheduled = 计算今天应该执行的时间点
    return now >= scheduled and last_run < scheduled
```

**4. 非阻塞锁（防止重叠执行）**

```python
_runner_locks = {
    "source_sync_schedule": threading.Lock(),
    "article_ingest_schedule": threading.Lock(),
}

# 在 tick 中：
lock = _runner_locks[key]
if not lock.acquire(blocking=False):  # 非阻塞：拿不到锁就跳过
    continue
try:
    _run_schedule(key)
finally:
    lock.release()
```

> [!important] 为什么用非阻塞锁？
> 如果上一次同步还没跑完（比如网络慢），新的 tick 不应该再启动一次同步。`blocking=False` 表示"如果锁被占用了就跳过"，而不是"等锁释放"。

**5. 数据库存储调度配置**

```text
调度配置存在数据库中（不是代码中）：
{
  "enabled": true,
  "frequency": "daily",        # daily / weekly / monthly
  "time_of_day": "09:00",      # 每天几点执行
  "interval_hours": 2,         # 或按小时间隔
  "timezone": "Asia/Shanghai",
  "last_run_at": "2026-05-06T09:00:00+08:00",
  "paused_until": ""           # 暂停截止时间
}
```

用户在 Web 界面修改配置 → 写入数据库 → 下一次 tick 自动读取新配置。

### 3.3 多频率支持

```python
def _scheduled_time_for_now(now, payload) -> datetime | None:
    frequency = payload.get("frequency", "daily")
    time_of_day = payload.get("time_of_day", "09:00")

    if frequency == "daily":
        return 今天的时间点（如 09:00）
    if frequency == "weekly":
        day_of_week = payload.get("day_of_week", 1)
        return 今天是周几 == day_of_week ? 今天的 09:00 : None
    if frequency == "monthly":
        day_of_month = payload.get("day_of_month", 1)
        return 今天是几号 == day_of_month ? 今天的 09:00 : None
```

## 4 组件之间的关系

```text
start_scheduler()          ← FastAPI lifespan 启动时调用
  └── _scheduler_loop()    ← 守护线程的主循环
        └── 每 30 秒:
              ├── get_scheduler_settings()     ← 从数据库读取配置
              ├── _is_due(config)              ← 判断是否到期
              ├── lock.acquire(blocking=False) ← 非阻塞加锁
              └── _run_schedule(key)           ← 执行任务
                    ├── 创建 scheduler_run 记录
                    ├── 执行具体任务
                    └── 更新 scheduler_run 状态

stop_scheduler()           ← FastAPI lifespan 关闭时调用
  └── _scheduler_stop.set() ← 唤醒并停止循环
```

## 5 常见误区

| 误区 | 正确理解 |
| --- | --- |
| "调度器就是定时器" | 调度器是"轮询 + 到期判断"，不是精确的定时器。最多有 30 秒的偏差 |
| "用 time.sleep 就行" | `sleep` 不能被中途唤醒，应该用 `Event.wait` |
| "锁是为了线程安全" | 这里的锁是为了**防止任务重叠执行**（上次还没跑完，这次不要启动） |
| "配置应该写在代码里" | 配置存数据库，用户才能通过 Web 界面动态修改，不需要重启 |
| "调度精度是 30 秒" | 30 秒是检查间隔，不是执行精度。实际执行时间取决于 tick 开始的时间 |

---

## 6 来源

相关 Wiki：
- [[wechat-md-server-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/scheduler.py`（174 行，完整实现）
- Python `threading` 模块文档

---

**引用来源**：[[wechat-md-server-wiki]]、`app/scheduler.py`
