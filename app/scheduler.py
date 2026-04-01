from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services import get_scheduler_settings, get_settings, get_sync_store, submit_article_ingest, sync_source_articles


_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_scheduler_lock = threading.Lock()
_runner_locks = {
    "source_sync_schedule": threading.Lock(),
    "article_ingest_schedule": threading.Lock(),
}


def start_scheduler() -> None:
    global _scheduler_thread
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(target=_scheduler_loop, name="wechat-md-scheduler", daemon=True)
        _scheduler_thread.start()


def stop_scheduler() -> None:
    _scheduler_stop.set()
    thread = _scheduler_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)


def _scheduler_loop() -> None:
    while not _scheduler_stop.is_set():
        try:
            _run_scheduler_tick()
        except Exception as error:
            print(f"[scheduler] tick failed: {error}")
        _scheduler_stop.wait(30)


def _run_scheduler_tick() -> None:
    schedules = get_scheduler_settings()
    for key in ("source_sync_schedule", "article_ingest_schedule"):
        payload = schedules.get(key) if isinstance(schedules.get(key), dict) else {}
        if not payload or not bool(payload.get("enabled")):
            continue
        if not _is_due(payload):
            continue
        lock = _runner_locks[key]
        if not lock.acquire(blocking=False):
            continue
        try:
            _run_schedule(key)
        finally:
            lock.release()


def _is_due(payload: dict[str, object]) -> bool:
    timezone_name = str(payload.get("timezone") or "Asia/Shanghai")
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    paused_until = str(payload.get("paused_until") or "").strip()
    if paused_until:
        try:
            paused_at = datetime.fromisoformat(paused_until)
            if paused_at.tzinfo is None:
                paused_at = paused_at.replace(tzinfo=timezone.utc)
            if now.astimezone(timezone.utc) < paused_at.astimezone(timezone.utc):
                return False
        except ValueError:
            pass
            
    last_run_at = str(payload.get("last_run_at") or "").strip()
    try:
        last_run = datetime.fromisoformat(last_run_at)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        last_run = last_run.astimezone(zone)
    except ValueError:
        return True

    interval_hours = int(payload.get("interval_hours") or 0)
    if interval_hours > 0:
        from datetime import timedelta
        return now >= last_run + timedelta(hours=interval_hours)

    scheduled = _scheduled_time_for_now(now, payload)
    if scheduled is None or now < scheduled:
        return False
    return last_run < scheduled

def _scheduled_time_for_now(now: datetime, payload: dict[str, object]) -> datetime | None:
    frequency = str(payload.get("frequency") or "daily")
    time_of_day = str(payload.get("time_of_day") or "09:00")
    try:
        hour_text, minute_text = time_of_day.split(":", 1)
        scheduled = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
    except Exception:
        scheduled = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if frequency == "daily":
        return scheduled
    if frequency == "weekly":
        day_of_week = int(payload.get("day_of_week") or -1)
        if day_of_week < 1 or day_of_week > 7:
            return None
        return scheduled if now.isoweekday() == day_of_week else None
    if frequency == "monthly":
        day_of_month = int(payload.get("day_of_month") or -1)
        if day_of_month < 1 or day_of_month > 31:
            return None
        return scheduled if now.day == day_of_month else None
    return None


def _run_schedule(key: str) -> None:
    store = get_sync_store()
    run = store.create_scheduler_run(key)
    try:
        if key == "source_sync_schedule":
            result = _run_source_sync_schedule()
        else:
            result = _run_article_ingest_schedule()
        store.finish_scheduler_run(run["id"], status="completed", result=result)
    except Exception as error:
        store.finish_scheduler_run(run["id"], status="error", result={}, error_message=str(error))


def _run_source_sync_schedule() -> dict[str, object]:
    items = get_sync_store().list_sync_sources()
    synced = 0
    skipped = 0
    failed = 0
    failures: list[str] = []
    for item in items:
        if not bool(item.get("enabled", True)):
            skipped += 1
            continue
        try:
            sync_source_articles(source_id=str(item.get("id") or ""))
            synced += 1
        except ValueError as error:
            skipped += 1
            failures.append(f"{item.get('account_name') or item.get('account_fakeid')}: {error}")
        except Exception as error:
            failed += 1
            failures.append(f"{item.get('account_name') or item.get('account_fakeid')}: {error}")
    return {"synced": synced, "skipped": skipped, "failed": failed, "failures": failures}


def _run_article_ingest_schedule() -> dict[str, object]:
    store = get_sync_store()
    article_ids = store.find_article_ids(
        process_status="pending",
        is_ingested=False,
        has_execution=False,
    )[:20]
    if not article_ids:
        return {"queued": 0, "job_id": "", "output_target": "fns" if get_settings().fns_enabled else "local"}
    job = submit_article_ingest(
        article_ids=article_ids,
        ai_enabled=get_settings().ai_enabled,
        output_target="fns" if get_settings().fns_enabled else "local",
        skip_ingested=True,
    )
    return {"queued": len(article_ids), "job_id": str(job.get("id") or ""), "output_target": str(job.get("output_target") or "")}
